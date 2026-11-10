# Motor de risco de crédito da Capital Certo

Estado em 10/11/2026. Tudo roda na VPS da Capital Certo (`169.58.240.203`), sem provedor externo de LLM. Código em `configs/airflow/dags/` (DAGs e módulos) e `apps/risk-api/` (API). Acompanhamento detalhado e histórico dos testes: `ESTADO_MOTOR.md`.

## 1. O que o motor faz

Todo dia, depois da carga dos dados, ele **retreina**, escolhe o melhor modelo por idade do contrato, calibra as probabilidades, **pontua todos os contratos** e guarda tudo no ClickHouse. A API de risco consulta uma pessoa por **telefone, CPF ou CNPJ**, junta tudo que a base tem e infere o risco (dos contratos existentes e de um contrato novo simulado).

**O que é "calote"** (o rótulo): 20% ou mais das parcelas com vencimento **depois da etapa** e até o dia 45 do contrato continuarem em aberto e vencidas, com 30 dias de prazo para o cliente regularizar. O alvo é medido só no futuro de cada etapa, para o modelo nunca ver o que está prevendo.

**Um modelo por idade do contrato** (etapas D+0, 1, 2, 3, 5, 7, 10, 14). A informação útil muda com a idade: no dia da contratação quase não existe sinal; depois, o comportamento de pagamento dos primeiros dias passa a ser o sinal.

## 2. Pipeline diário (orquestradora `orquestradora_diaria_capital_certo`, 03:30 de Brasília)

```
chatwoot_diario ─┐                       ┌─> credit_motor_diario (features, treino, score)
                 ├─> analytics_capital_certo ─┤
credhub_diario ──┘     (dimensoes e fatos)    └─> (todos esperam a analytics)
chatwoot_diario ─> bureau_gold_diario  (parse Dora/DeskData, ficha por conversa)
chatwoot_diario ─> midia_diario        (baixa anexos novos, imagem/PDF/video/audio)
[motor, midia, bureau] ─> resumo_diario (LLM pequeno, so quem teve atividade)
```

| DAG | O que faz |
|---|---|
| `credhub_diario` | entra no portal `credhub.pro` (login NextAuth + MFA, credencial na Airflow Connection `credhub_portal`), espera o backup do dia (03:00), baixa o `.sql.gz` por tenant, restaura em banco de staging, confere contagem contra o `manifest.json`, grava raw (MinIO) e trusted (ClickHouse) |
| `chatwoot_diario` | conversas, mensagens e notas do Chatwoot do cliente (túnel SSH, só leitura) |
| `analytics_capital_certo` | dimensões e fatos da Gold (contrato, parcela, cliente, funil...) |
| `bureau_gold_diario` | transforma as notas Dora e DeskData em campos (sem LLM): `dim_relatorio_bureau`, `fato_bureau_dora`, `fato_bureau_integridade`, `fato_bureau_divida`, `fato_bureau_processo`, `ficha_credito_conversa` |
| `midia_diario` | baixa os anexos novos pela API do Chatwoot (só GET, MD5 conferido), processa imagem (CLIP, hash, nitidez), PDF (texto e campos de extrato), áudio (Whisper) e vídeo (6 quadros + CLIP) |
| `credit_motor_diario` | 3 tarefas: `retreinar_e_pontuar` (features, valida, escolhe o algoritmo por etapa e por alvo, calibra, pontua, salva os modelos em `models/credit_motor/ultimo`), `clusters_etapas_e_gold` (chance por cluster, score por etapa, views e tabelas Gold) e `dicionario_nomes_claros` |
| `resumo_diario` | resumo por cliente (estruturado sempre; LLM pequeno como complemento) |

**Atenção ao criar DAG nova:** ela nasce **pausada**. Conferir `is_paused` depois que o Airflow ler o arquivo; uma DAG pausada deixa a orquestradora esperando para sempre.

## 3. Tabelas (o que o Claude Code e o Metabase devem consultar)

| Tabela | Conteúdo |
|---|---|
| `analytics.score_contrato` / `score_contrato_atual` | score diário por contrato: probabilidade calibrada, faixa, confiança do modelo, motivos, fase (ativo ou encerrado) e `calote_observado` para auditar previsto x real |
| `analytics_ml.motor_features_contrato` | uma linha por contrato com todas as features e rótulos |
| `analytics_ml.motor_execucoes` | validação temporal de cada configuração testada, por etapa e dia (AUC, IC95, Brier, lift); `escolhido` e `fallback` |
| `analytics_ml.motor_backtest` | previsões fora do tempo (para curva de calibração) |
| `analytics_ml.motor_importancia` | fatores mais importantes por etapa |
| `analytics_ml.motor_correlacoes` | **todas as correlações testadas** (1.219 testes de 10 famílias de variáveis), com p, p ajustado e estabilidade |
| `analytics_ml.motor_algoritmos` | comparação de 10 algoritmos x 6 conjuntos de variáveis |
| `analytics.resumo_pessoa` | resumos por cliente |
| `trusted_chatwoot.anexo_*` | arquivo, imagem, PDF, áudio e vídeo processados |

## 4. Correlações: o que existe e o que não existe (base de ~900 contratos, 560 a 720 com resultado fechado)

Método: cada variável testada sozinha (Mann-Whitney ou Fisher), correção de falsa descoberta (Benjamini-Hochberg por alvo) e checagem entre a metade antiga e a nova dos contratos. Lista completa em `analytics_ml.motor_correlacoes`.

**Têm correlação** (passam na correção):
- **Pix agendado**: calote em 30 dias de 46% a 67% com Pix contra 10% a 23% sem Pix, no mesmo mês e dentro do mesmo provedor (odds ratio 8,2 controlando o mês). Definido na criação do contrato; não é vazamento. O uso foi de 3% em maio para 94% em out e nov.
- **Idade do cliente** (mais velho, menos calote), **histórico de atraso em contrato anterior**, **CEP vazio no cadastro**.
- **Dias entre o primeiro contato e o contrato** (AUC 0,58 a 0,59, também só entre primeiros contratos): quanto mais demora, mais calote. **Linha de WhatsApp** (inbox 7: +9 pontos; inbox 38: -25 pontos).
- **Quem pagou bem o contrato anterior** tem calote de 6% no seguinte, contra 30% (n=17 contra 83). **Valor maior no 2º contrato**: 35% contra 21%.
- **Ramo**: divisão CNAE 95 (reparação de equipamentos) com 82% a 90% de calote (n=10 a 11).
- **Fuga de garantia** (etiqueta da operação): provedor MANUAL, valor, tempo de casa e taxa de juros.

**Não têm correlação** (nenhuma passa): sexo, UF, DDD, região fiscal do CPF, tipo de pessoa, renda, nome, estilo da conversa no WhatsApp, **notas do bureau** (score, faixa, dívidas, protestos, processos, veredito da IA), CNAE e porte (exceto divisão 95), quadro societário e rede de clientes ligados (só 10 contratos com vínculo), mídia (imagem, PDF, áudio, vídeo), embedding do texto, funil, interações entre pares.

**Sobre o bureau** (o que causa estranheza): (1) 59% das conversas têm score PF 350, que parece um piso do modelo, então a variável satura; (2) um modelo sobre os campos Dora e DeskData prevê se o lead **vira contrato** com AUC 0,87, então o bureau é determinante na **aprovação** e só existe contrato de quem passou no filtro: entre os aprovados o bureau varia pouco (restrição de faixa), e o efeito sobre os **reprovados é inobservável**, porque eles nunca geraram pagamento. Isso não prova que o bureau não presta; prova que, **nesta carteira de aprovados**, ele não separa calote. (O 0,87 pode estar inflado: as notas são escritas durante o processo.)

**Padrão de golpe do segundo contrato** (paga bem o primeiro, quita antes, pega mais e some): só 10 casos, com calote de 10% (IC 2% a 40%). Esta base não confirma, e mostra o oposto: quem paga bem continua pagando. Evento raro em amostra pequena.

**Hipótese "hamburgueria e mercearia dão golpe":** mercearia/mercadinho 24% (n=33), hamburgueria 31% (n=13, IC 13% a 58%), restaurantes 24%, varejo 22%. Não se sustenta.

## 5. Algoritmos: qual vale

Validação temporal (treina só com o que já estava fechado, testa no mês seguinte, 5 meses), 10 algoritmos x 6 conjuntos de variáveis (`motor_algoritmos`).
- **Floresta aleatória e gradient boosting** vencem no dia 0 com o conjunto **enxuto** (6 variáveis): AUC 0,63 (IC95 0,57 a 0,70). Logística L2, kNN e naive Bayes ficam entre 0,3 e 0,5 no dia 0 porque o Pix agendado quase não existia nos meses de treino.
- **Mais variáveis pioram**: AUC médio 0,53 com o conjunto enxuto e 0,47 a 0,50 com tudo. Com ~400 contratos, o modelo decora.
- **De D+5 em diante** a logística regularizada sobre o comportamento de pagamento vence: AUC 0,74 (D+5), 0,80 (D+7), 0,82 (D+10), 0,85 (D+14).
- O motor testa os candidatos todo dia e fica com o melhor por etapa (empate favorece o mais simples); se o retreino piorar mais de 0,05 de AUC, mantém o modelo anterior.

## 6. Confiança por idade do contrato (a leitura honesta)

| Idade | AUC fora do tempo | Confiança |
|---|---|---|
| D+0 (contrato novo) | ~0,63 | **baixa**: use como complemento |
| D+1 a D+3 | 0,62 a 0,68 | baixa |
| D+5 | 0,74 | média |
| D+7 a D+14 | 0,80 a 0,85 | alta |

**Limites conhecidos:** (a) contratos antigos "muito alto" saem superestimados (previsto 85% contra observado 56%); a calibração por safra é melhoria pendente; (b) no D+0 a probabilidade calibrada fica num patamar (~0,56 para quem tem Pix agendado), porque o modelo quase não separa; (c) a base é pequena e o Pix agendado mudou o perfil da carteira, então a validação só confia nos meses recentes.

## 7. API de risco (`http://169.58.240.203:8000`)

Chave no cofre (`capitalcerto-risk-api.json`), cabeçalho `X-API-Key`. Documentação: `apps/risk-api/README_API_V1.md` e `/docs`.
- `POST /v1/consulta` com `telefone` (preferido), `cpf` ou `cnpj` e, opcionalmente, `contrato` a simular. Devolve cadastro, contratos com score, conversas com bureau, mídia, resumo (`resumo_estruturado` sempre; `resumo_llm` quando aprovado) e risco.
- `GET /v1/score/contrato/{id}`, `GET /v1/modelo`, `GET /health`.
- A simulação de contrato novo assume Pix agendado se o front não informar (94% dos novos usam), por isso tende a "muito alto" até o front informar provedor e modo de cobrança.

## 8. Resumo com LLM pequeno

Qwen3 1,7B em CPU (llama.cpp). Medido: 110 a 140 s por cliente com a VPS carregada; o resumo gerado a partir de JSON **alucinou** (inventou região, "bureau associado a Michael" e estado de contratos), mesmo passando na checagem de números. Por isso o LLM só **reescreve o resumo estruturado** (que é montado por regra a partir das colunas) e descreve os trechos de conversa; a API sempre devolve o estruturado como fonte confiável e o texto do LLM como complemento. Imagens não são vistas por um modelo de visão: o que existe é CLIP (fachada, residência, captura de tela), OCR e hash para achar reuso.

## 9. Pendências e riscos

- Credenciais (portal CredHub, MFA, token do Chatwoot, chaves da API) estão em Airflow Connections e `.env`; faltou a "repaginada" de segurança combinada.
- A API roda por IP, sem HTTPS e sem domínio.
- Calibração dos contratos antigos e por safra.
- O LLM disputa CPU com o resto: a API pode levar 4 a 8 s enquanto ele roda; o `resumo_diario` roda por último e com teto de 90 minutos.
- Nada foi commitado no GitHub: o repo `capitalcerto_lake` tem alterações só no disco. O deploy faz `rsync --delete` e apaga na VPS o que não estiver no repo.


## 10. Atualização de 10/11 (madrugada): três alvos, clusters, Gold, dashboard e API

**Três alvos por etapa** (versão do código `motor-2026-11-10.1`). O motor treina, valida e pontua, em todas as etapas D+0 a D+14:
- **calote**: 20% ou mais das parcelas depois da etapa, até o dia 45, vencidas e em aberto (`prob_calote`);
- **inadimplência**: 30% ou mais das parcelas até o dia 45 pagas com mais de 3 dias de atraso ou em aberto (`prob_inadimplencia`). Quase toda a carteira atrasa algum dia, então é um alvo de base alta e ele só separa bem de D+5 em diante (AUC 0,81 em D+5; 0,91 em D+14); no dia 0 não separa (0,57);
- **cluster ruim**: a trajetória de pagamento cai em "paga no começo e para" ou "quase não paga" (`prob_cluster_ruim`; AUC 0,61 em D+0, 0,79 em D+7, 0,87 em D+14).

**Clusters de inadimplência.** K-means sobre a trajetória D+1..D+45 dos contratos maduros (45 dias + 30 de cura). Nomes automáticos pelo centro do cluster: *paga em dia*, *paga com atraso moderado (~10 dias)*, *paga com atraso longo (mais de 30 dias)* e *paga no começo e para*. O último reúne 26% dos contratos maduros, 53% deles com endereço dividido com outro cliente. A **chance de cada cluster** por contrato vem de uma regressão logística multinomial sobre o comportamento até a etapa (`analytics.score_cluster_contrato`); para contrato maduro a probabilidade é calculada fora da amostra.

**Variáveis novas testadas e o que ficou no motor** (bateria local de 10/11, tabelas `analytics_ml.motor_*`): entram como candidatas no D+0 *endereço dividido com outro cliente* (calote 43% contra 30%, p=0,0014), *índice de inconsistências cadastrais* e *contrato anterior no cluster "paga e para"* (82% contra 22%, n=11). O motor escolhe sozinho o conjunto por validação temporal. Não entram: tom da conversa, OCR dos prints, sazonalidade (veja §11).

**Gold nova** (tudo com comentário em tabela e coluna):
| Objeto | Conteúdo |
|---|---|
| `analytics.contrato_risco_360` (tabela) | uma linha por contrato: cadastro, termos, histórico, cluster, rótulos e score atual |
| `analytics.contrato_consulta` (tabela) | ficha para o dashboard: nome, telefone (só dígitos), scores, parcelas, saldo |
| `analytics.contrato_cluster_chances` | chance do contrato em cada cluster |
| `analytics.contrato_piorou` | contratos ativos cujo risco subiu de uma etapa para outra, com `prioridade` e `posicao` |
| `analytics.cluster_inadimplencia` | tamanho, perfil e taxa de calote de cada cluster |
| `analytics.risco_dia0_perfil` | dia 0: taxa de calote por modo de cobrança, tipo de contrato e flag, com intervalo de Wilson |
| `analytics.risco_renovacao` | renovações: como foi o contrato anterior, salto de valor, score e resultado |
| `analytics.risco_acuracia_modelo` | acerto por alvo e etapa, com intervalo de confiança |
| `analytics.score_contrato_etapas` | score do contrato em cada etapa que ele atravessou |
| `analytics.dicionario_nomes_motor` | nome técnico → nome claro (fatores, níveis, modelos, clusters) |
| `analytics_ml.motor_matriz_associacao`, `motor_informacao_mutua`, `motor_pares_redundantes`, `motor_combinacoes_condicionadas` (41.716 perfis), `motor_comite_classes` | resultados da bateria local |

**Metabase (dashboard 5).** Três abas depois de "Motor de crédito", com nomes em português claro para a área de negócio: *Consulta por contrato* (filtros de telefone e nome com sugestão dos valores existentes: ficha, chance em cada cluster, risco por etapa, fatores, parcelas e resumo), *Contratos que pioraram* (ordenados por prioridade: chance de calote × valor × (1 + piora)) e *API e pesos* (como chamar a API e como o risco é calculado). Gerado por `docs/metabase_motor2.py` + `docs/mb2_conteudo.py` (idempotente; `nomes_claros.py` traduz os nomes).

**API.** A consulta devolve também `prob_inadimplencia`, `prob_cluster_ruim`, `cluster_trajetoria`, `tipo_contrato`, `anterior_paga_e_para`, `endereco_compartilhado`. Novos: `GET /v1/clusters`, `GET /v1/renovacao/{contrato}`; `GET /v1/modelo` agora lista por alvo.

## 11. O que foi testado e não entrou (e por quê)

- **Tom da conversa por LLM** (Qwen do próprio Lucas, 12 perguntas sim/não, 654 contratos): quem fala menos e engaja menos dá mais calote (sem Pix: AUC 0,39, p=0,0002, estável nas duas metades). Cerca de metade do efeito é só tamanho do texto, e o modelo do dia 0 não melhora de forma confiável (0,637 → 0,658, dentro do IC 0,56 a 0,70). Fica como observação, não como variável.
- **OCR dos prints de maquininha:** 768 prints lidos, 106 parecem tela de vendas, só 26 trazem total vendido. Poder estatístico insuficiente para testar contra calote.
- **Índice de inconsistências:** AUC 0,52 a 0,55, sozinho não passa.
- **Falhas de cobrança Pix (EFI) nos 7 primeiros dias:** AUC 0,57, mas é o mesmo sinal do Pix agendado (média 2,6 falhas com Pix contra 0,4 sem), não um sinal novo.
- **Valor ÷ faturamento declarado** (n=278 a 318): AUC 0,56 a 0,60, 0,62 a 0,64 sem Pix. Promissor, ainda com amostra curta.
- **Sazonalidade** (dia da semana, mês, feriado): AUC 0,53 a 0,55. Com 10 meses e o Pix indo de 3% a 94% no período, safra e sazonalidade não se separam.
- **Identidade compartilhada** de telefone, e-mail e responsável: n de 10 a 24, sem poder.
- **PIB e porte do município:** sinal fraco (um perfil com q=0,02, n=53) e não melhora o modelo fora do tempo (AUC 0,59 a 0,61 contra 0,62 a 0,63 sem eles).
- **Não feito:** combinações de 4 ou mais variáveis, tom cruzado com outras variáveis, leis estaduais e inferência causal (só associação).
