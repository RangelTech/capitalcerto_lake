# Spec: mapeamento de todas as variáveis, combinações e modelos do motor de crédito

Escrita em 10/11/2026. Objetivo: **mapear primeiro** tudo o que pode explicar calote e inadimplência (variáveis, combinações, alvos, modelos), **depois testar o que falta** e só então fechar o motor. O catálogo completo, linha a linha, está em [`catalogo_variaveis.csv`](catalogo_variaveis.csv) (gerado por `gerar_catalogo_variaveis.py`). O que já foi medido está em `ESTADO_MOTOR.md` e nas tabelas `analytics_ml.motor_correlacoes`, `motor_algoritmos`, `motor_dia0_segmentos`.

Regra de trabalho (decisão do Lucas): **testes exploratórios com dado externo rodam na máquina local**; só se houver correlação se monta a carga diária na VPS. Sem correlação, o dado é abandonado.

## 1. Quando a decisão acontece (momentos)

| Momento | O que se sabe | Decisão | Exemplos de dado |
|---|---|---|---|
| **M0** antes de assinar | lead, conversa, cadastro, bureau, anexos | aprovar ou não, e em que condições | idade, CEP, cidade, CNAE, bureau, tom da conversa, fotos |
| **M1** assinatura (dia 0) | tudo de M0 + condições do contrato | valor, nº de parcelas, taxa, **cobrança (Pix agendado, provedor)** | `pix_agendado`, `provedor`, `valor`, `qtd_parcelas`, `taxa_juros` |
| **M2** renovação | histórico completo do cliente | liberar novo contrato após quitar | pagou bem o anterior, salto de valor, cluster anterior, conversa antes da renovação |
| **M3** D+1 a D+14 | pagamentos reais | cobrança preventiva | % no prazo, atraso máximo, 1ª parcela |

## 2. Alvos

| Alvo | Definição | Observação |
|---|---|---|
| **Calote** `ye_k`, `y_calote_H` | ≥20% das parcelas depois da etapa k (ou até H) em aberto e vencidas, com 30 dias de prazo | já no motor; taxa 24% a 36% |
| **Inadimplência** `y_15`, `y_30`, `y_45` | ≥30% das parcelas até H pagas com mais de 3 dias de atraso ou em aberto | taxa 57% a 76%; **muito mais sensível** a prazo, valor e taxa |
| **Cluster de trajetória** | k-means (k=4, silhouette 0,47) sobre D+1..D+45 | 4 padrões abaixo |
| Nunca pagou | nenhuma parcela paga entre as vencidas | só 3,6% (26 de 714): sem poder |
| Parou cedo | pagou ≤5 e parou | 5%: idade protege (q=0,08) |
| Fuga de garantia | etiqueta da operação | 7,5% |
| Severidade | atraso máximo e % em aberto (contínuos) | Spearman |

**Clusters de inadimplência (4 padrões, 560 contratos maduros):** paga em dia (22,5%); paga com atraso moderado, ~12 dias, e quita (41,6%); paga com atraso longo, ~55 dias (10,2%); **paga no começo e para (25,7%)**. O cluster "paga e para" reúne **57% dos contratos com Pix agendado** contra 19% dos sem Pix. No dia 0, **provedor, cobrança, valor, linha de WhatsApp, idade e ramo** se associam ao cluster (qui-quadrado com correção de falsa descoberta, q < 0,02); mais do que ao alvo binário.

## 3. Mapa das variáveis (resumo; detalhe no CSV)

71 famílias mapeadas. Situação em 10/11:

| Status | Qtd | Quais |
|---|---:|---|
| **SINAL** (passa a correção de falsa descoberta) | 14 | `pix_agendado`, `provedor`, `valor`, `qtd_parcelas`, razão parcela/principal, `taxa_juros`, idade, CEP vazio, ramo 95, dias até fechar, pagou bem o anterior, atraso anterior, comportamento D+1..D+14, fuga de garantia |
| **SINAL_FRACO** | 7 | cidade/PIB per capita e porte do município, linha de WhatsApp, salto de valor, vendas mensais, cadastro incompleto, sexo (inadimplência) |
| **SEM_SINAL** | 35 | bureau (score, dívidas, protestos, processos, identidade, vínculo), região fiscal do CPF, UF/DDD, nome, estilo textual da conversa, funil, mídia, embeddings, sócios, porte, MEI |
| **NÃO TESTADA** | 10 | tom por LLM, razão valor/faturamento, sazonalidade, OCR do print da maquininha, **telefone/e-mail/endereço compartilhado**, **índice de inconsistências**, cluster do contrato anterior, conversa antes da renovação, **falhas de cobrança (EFI/Pluggy)**, macro (Selic, etc.) |
| INDISPONÍVEL / BLOQUEADA | 5 | raça/cor, naturalidade, lat/long, leis estaduais, clima |

**Raça e cor** não existem em nenhuma fonte; inferir por nome ou foto seria impreciso e discriminatório, então só entra se o cliente fornecer o campo. **Leis estaduais** não têm variável objetiva; o proxy é a UF, já testada.

## 4. Espaço de combinações

Duas camadas, para não afogar a base (~900 contratos, 560 a 720 com resultado) em testes:

1. **Combinações de 2 e 3 variáveis discretizadas** (14 variáveis: cobrança, provedor, faixa de idade, faixa de valor, histórico, CEP, segmento, região do DDD, origem do CPF, ramo, linha de WhatsApp, dias até fechar, sexo, tipo de pessoa). Já rodado: **5.186 perfis** (calote 30d, todos os contratos), 4.154 (só sem Pix) e 3.413 (desde junho). **Problema:** a maioria dos perfis "significativos" é o Pix agendado reaparecendo. Próximo: refazer **condicionado à cobrança** (estratos Pix e sem Pix) para achar o que não é redundante.
2. **Interações contínuas** (21 pares de 8 variáveis, regressão logística com e sem termo de interação): nenhuma passa a correção (melhor: valor alto × Pluggy, q=0,16). Próximo: varrer pares entre **todas** as variáveis do catálogo por triagem em árvore (força de interação) e confirmar só as que sobrevivem.

Variáveis de contrato (`valor`, `qtd_parcelas`, `taxa_juros`, razão) são **decisão da operação**: o motor deve tratá-las como alavanca, não como atributo do cliente.

## 5. Modelos: qual responde melhor a qual tipo de correlação

Duas fontes de evidência:

**(a) Dados reais** (validação temporal, `motor_algoritmos`): floresta aleatória e gradient boosting vencem no dia 0 com poucas variáveis (AUC 0,63); logística L1 é a melhor em `y_calote_30` (0,62); logística L2, kNN e naive Bayes ficam entre 0,3 e 0,5 no dia 0 (o Pix agendado quase não existia nos meses de treino); **mais variáveis pioram** todos os modelos (0,53 com o conjunto enxuto, 0,47 a 0,50 com tudo).

**(b) Simulação controlada** (`sim_modelos.py`, local): 10 estruturas de correlação conhecidas (linear, degrau, U, categoria rara, categoria com 15 níveis, interação E, interação XOR, soma de efeitos fracos, ausência informativa, só ruído) × 11 modelos × n=400 e n=900, com 30 variáveis de ruído. Resultado em `docs/sim_modelos_resumo.csv` e na seção 5.1 abaixo.

| Tipo de modelo | Capta bem | Falha em |
|---|---|---|
| Logística L2/L1 | efeitos lineares aditivos, muitas variáveis fracas | degraus, U, interações |
| Logística + splines | não linearidade suave | interações |
| Árvore rasa | degraus e interações simples | variância alta |
| Random forest / Extra trees | degraus, interações, ruído | efeitos lineares fracos somados |
| Gradient boosting | quase tudo com n suficiente | sobreajuste com n pequeno |
| Naive Bayes / kNN / SVM / MLP | casos específicos | ruído alto, n pequeno |

#### 5.1 Resultado da simulação (n=900; % do sinal verdadeiro recuperado, fora da amostra)

Cada estrutura tem sinal conhecido (AUC do oráculo 0,57 a 0,67) e 30 variáveis de ruído. 100% = o AUC do oráculo. Dados completos: `sim_modelos_resumo.csv`.

| Tipo de correlação | Melhor modelo | % recuperado | Pior |
|---|---|---:|---|
| Linear numa variável | **logística L1** | 84% | rede neural 14% |
| Soma de vários efeitos fracos | **logística L1** (L2: 76%) | 81% | rede neural 22% |
| Degrau (acima de um corte) | **random forest** | 93% (GB 84%, árvore rasa 82%) | rede neural 11% |
| Interação E (duas condições juntas) | **árvore rasa, RF, GB** | 89% a 94% | rede neural 14% |
| Forma de U | **RF, GB, árvore rasa, naive Bayes** | 60% a 66% (logística **-6%**) | logística |
| Categoria rara (8% dos casos) | SVM, naive Bayes, L1, RF, extra trees | 91% a 111% | rede neural 64% |
| Ausência do dado informa | **logística L1**, SVM | 98% a 99% | rede neural 17% |
| Categoria com 15 níveis | todos fracos (**<35%**) | 34% (GB) | kNN 2% |
| Interação XOR (A ou B, não os dois) | **ninguém** | 0% a 11% | todos |
| Só ruído (controle) | todos ~0,50 (sem falso positivo) | - | - |

**Leitura para este projeto (n de 400 a 900):**
1. **Logística L1** é a melhor para o que for **linear, aditivo ou "ausência informa"**, e é imbatível em estabilidade. Falha em degrau, U e interação.
2. **Floresta aleatória e gradient boosting** são os melhores quando há **cortes, interações e formas não monotônicas** (o caso típico de "perfil de cobrança"). Com poucos dados, preferir a floresta (mais estável).
3. **Rede neural não presta com este n** (recupera 11% a 22%): descartada.
4. **kNN é fraco**; **naive Bayes e SVM só servem para categorias raras.**
5. **Categorias com muitos níveis (cidade, segmento) e interações XOR não são recuperáveis com 900 contratos por nenhum modelo.** Para isso é preciso agrupar níveis ou ter muito mais dados; "não deu nada" ali significa "sem poder".
6. O **comitê** (L1 + floresta + GB) cobre os dois mundos: o linear e o de cortes/interações. Foi o desenho adotado no motor; a validação temporal escolhe por etapa.


**Proposta:** manter no motor um **comitê pequeno e barato** (logística L1 + random forest + gradient boosting), escolher por etapa pela validação temporal e **medir também modelos de tempo até o evento** (hazard discreto) e **modelo por classe** (CPF, MEI, outros), como na literatura brasileira.

## 6. O que a literatura diz (pesquisa de 10/11)

- **Dados alternativos em microcrédito:** features de SMS e apps instalados melhoraram AUC e F1, com gradient boosting (XGBoost) à frente ([UCT](https://open.uct.ac.za/items/10c498bf-48ba-4ba8-973b-2f1698386d4b)); uso do celular e **mobilidade** foram os sinais mais preditivos num experimento de campo ([ICIS 2019](https://aisel.aisnet.org/icis2019/data_science/data_science/10)); **força dos laços** em chamadas e SMS ajuda a achar inadimplentes ([NCI](https://norma.ncirl.ie/3414/)); dado comportamental serve de proxy do histórico formal, com LightGBM e XGBoost bem ([BRAC](https://dspace.bracu.ac.bd/items/290cbba6-6043-4973-93c1-8f7f8660fa55)). → Ideias incorporadas: conversa de WhatsApp como dado alternativo (testada em estilo e latência; falta o tom por LLM); **telefone/endereço compartilhado entre clientes** (laço) e **geolocalização** (CEP).
- **Fraude e early payment default:** a inadimplência logo na primeira parcela é indício de renda declarada falsa, superendividamento ou identidade sintética ([Experian](https://www.experian.com/blogs/insights/first-payment-default/)); mais de 70% dos default precoces ligam-se a **declarações falsas** na aplicação ([BasePoint, fonte de fornecedor](https://www.housingwire.com/articles/study-70-percent-epds-linked-fraud/)); quem já teve default precoce nos últimos dois anos tem risco **8x** de repetir, e **telefone diferente em várias aplicações do mesmo tomador** e **mais de 17 indicadores de dado falso** (8x) são fortes ([Point Predictive, via nota de imprensa](https://nationalmortgageprofessional.com/news/15185/fdic-reports-housing-slowdown-poses-challenge-bank-loan-growth)). → **Índice de inconsistências** e **dados compartilhados** estão no backlog de teste. Atenção: os números vêm de fornecedores e de crédito imobiliário/automotivo, não de microcrédito diário.
- **Brasil, PME e MEI:** o Score MEI da Serasa usa cadastro, histórico de inadimplência e de pagamentos do CNPJ; o Score PJ estima a probabilidade em 6 meses ([Serasa](https://www.serasaexperian.com.br/blog-pme/ver-o-meu-score)); trabalhos usam **regressão logística e modelos separados por classe** (MEI, ME, EPP) e **behavior score** ([PUC-SP](https://repositorio.pucsp.br/bitstream/handle/45374/1/Flavia%20Braga%20de%20Souza.pdf)); abrir um MEI exige poucas formalidades, o que facilita seu uso em fraude ([Monitor Mercantil](https://monitormercantil.com.br/?p=179843)). → Modelo por classe no backlog.
- **Lacunas:** não achei estudo de **crédito diário com cobrança por Pix agendado** nem de conversa de WhatsApp como preditor. O achado de maior valor deste projeto (a cobrança por Pix agendado) é próprio desta base.

## 7. Backlog de testes (em ordem)

| # | Teste | Onde roda | Critério para virar dado diário |
|---|---|---|---|
| 1 | Combinações condicionadas à cobrança (estratos Pix e sem Pix) | local | perfis com q<0,10 fora do Pix |
| 2 | Telefone, e-mail, endereço e documento **compartilhados** entre clientes | local | associação com calote/cluster, q<0,10 |
| 3 | **Índice de inconsistências** (identidade divergente, terceiro, CNPJ não confirmado, dado faltante, DDD×CEP) | local | monotonia com a taxa de calote |
| 4 | Falhas de cobrança (`EFI_PAYMENT_FAILED`, `PLUGGY_PAYMENT_FAILED`) por contrato | local | explica o efeito do Pix agendado |
| 5 | Razão valor/faturamento, razão parcela/faturamento, sazonalidade (mês, dia, feriados) | local | q<0,10 |
| 6 | Tom da conversa por LLM (direto, agressivo, recusa, desconfiança, objeções) e latência bot×humano, **antes da assinatura e antes da renovação** | local (LLM pequeno) | q<0,10; custo de CPU aceitável |
| 7 | Cluster e comportamento do contrato anterior como preditor do próximo | local | AUC no D+0 de renovações |
| 8 | OCR do print da maquininha (faturamento) | local | correlação do faturamento extraído |
| 9 | Cidade com código IBGE pelo CEP (melhor casamento) + PIB, população; macro (Selic, desemprego) | local, depois lake | repetir o sinal de PIB per capita e porte |
| 10 | Modelo por classe, hazard discreto e comitê | local → motor | ganho de AUC fora do tempo > 0,02 |

Cada teste usa a mesma régua: **validação temporal**, **correção de falsa descoberta**, **estabilidade entre metades** e **poder** (com ~600 contratos só se detecta AUC ≥ 0,58; abaixo disso "não deu nada" significa "sem poder", não "não existe").

## 8. Como o resultado vira motor

Variável entra na etapa D+0 do motor se: (1) passa na correção em pelo menos um alvo e é estável; (2) melhora o AUC temporal em ≥ 0,01 sobre o conjunto enxuto atual; (3) está disponível **antes** da decisão (momento correto); (4) tem carga diária viável. As variáveis de contrato (cobrança, valor, parcelas, taxa) entram também como **alavancas** no retorno da API ("com Pix agendado a taxa observada é 74%; sem Pix, 20%").

Resultado esperado, honesto: o dia 0 por atributos do **cliente** continua fraco (AUC 0,6); o ganho vem de **condições do contrato**, da **cobrança** e do **comportamento depois da assinatura**.

## 9. Resultado final dos testes (10/11/2026, manhã)

Consolidado em `analytics_ml.motor_correlacoes`, `motor_algoritmos`, `motor_matriz_associacao`, `motor_informacao_mutua`, `motor_combinacoes_condicionadas` e `motor_comite_classes`; detalhes em `MOTOR_CREDITO.md` §10 e §11.

| Família | Resultado |
|---|---|
| Cobrança (Pix agendado, provedor) | SINAL FORTE e estável; entra no D+0 |
| Comportamento de pagamento (D+1..D+14) | SINAL FORTE, base do motor de D+5 em diante |
| Idade, histórico de atraso anterior, CEP vazio | SINAL, entram no D+0 |
| Endereço dividido com outro cliente | SINAL (43% x 30%, p=0,0014); candidato no D+0 |
| Cluster do contrato anterior (paga e para) | SINAL forte, n=11; candidato no D+0 |
| Índice de inconsistências | fraco (AUC 0,52 a 0,55) |
| Valor ÷ faturamento declarado | promissor (0,62 a 0,64 sem Pix), amostra curta |
| Tom da conversa por LLM (654 contratos) | engajamento AUC 0,39 sem Pix (p=0,0002), metade é tamanho de texto; não melhora o D+0 (0,637 → 0,658, dentro do IC) |
| OCR de prints de maquininha | 26 casos com valor; sem poder |
| PIB e porte do município | fraco; não melhora fora do tempo |
| Sazonalidade | não separável de safra e Pix com 10 meses |
| Bureau, sexo, UF, DDD, região do CPF, nome, CNAE, quadro societário, mídia, embeddings, funil | sem sinal estável |
| Combinações de 1 a 3 variáveis por modo de cobrança | 41.716 perfis testados com FDR |
| 4 ou mais variáveis, leis estaduais, causalidade | **não feitos** |
