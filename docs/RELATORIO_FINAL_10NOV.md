# Capital Certo: relatório da noite de 09 para 10/11/2026

Escrito para o Lucas ler ao acordar. Nada foi commitado no GitHub e nenhuma credencial existente foi rotacionada (foram criadas só contas de serviço novas, descritas no fim).

## Em uma frase
O motor de risco, a API, o dashboard e a base de dados estão no ar e documentados. O motor acerta bem **depois** que o contrato anda (dia 7 em diante) e **pouco** no dia da assinatura. O estudo mostrou que, com os dados de hoje, o teto do dia 0 é cerca de 0,65, e que o que mais pesa é o **canal de cobrança**, não o perfil do cliente.

## 1. O que o motor acerta (testado em contratos que o modelo não viu)
| Momento do contrato | Acerto (0,5 = chute, 1 = perfeito) | Confiança |
|---|---|---|
| Dia 0 (assinatura) | 0,63 a 0,67 | baixa |
| Dia 1 | 0,69 (antes 0,62) | média |
| Dia 2 | 0,70 (antes 0,65) | média |
| Dia 3 | 0,69 (antes 0,67) | média |
| Dia 5 | 0,74 | média |
| Dias 7, 10, 14 | 0,80, 0,82, 0,85 | alta |
Na inadimplência o acerto chega a 0,91 no dia 14.

## 2. Dia 0: o que descobrimos (o que mais interessava)
- **O canal de cobrança explica quase tudo que dá para prever na assinatura:** Pluggy cerca de 10% de calote, Efi automático de 6% a 31%, Manual cerca de 42% e **Pix agendado cerca de 74%**.
- **Mais variáveis não ajudam.** Produto curto, valor, tom da conversa, endereço dividido, valor sobre faturamento, idade e histórico fazem o acerto **cair** (0,59 a 0,65). Dentro do grupo mais difícil (sem Pix, 60 parcelas) o teto é 0,57.
- **Contratos de 59 parcelas** são um produto novo de junho (valor mediano R$ 500, juros 100%) com calote de 13% contra 34%; isso já é explicado pelo canal.
- **Por que o Pix agendado falha:** dos mais de 104 mil eventos de falha, **82% são "saldo insuficiente"**, repetidos todo dia; 7% são falha do banco e 5% "cancelado pelo cliente no banco". O débito é agendado quando a conta não tem saldo. O Banco Central avisa que, no Pix Automático, o risco passa para o consumidor, que pode cancelar a autorização a qualquer momento.
- **Recomendação:** o ganho do dia 0 não virá de mais variáveis do mesmo tipo, e sim de (a) decisão de produto (empurrar o canal que funciona, escolher o dia e o horário do débito) e (b) **dado novo na assinatura**: extrato e saldo por Open Finance (a Pluggy já é usada para cobrar). A literatura aponta dado de transação bancária como o sinal mais forte em crédito de pequeno negócio (acerto de 0,80 em um banco holandês).
- Pesquisa de literatura (resumo): dados do celular e de transações melhoram o acerto; inadimplência na primeira parcela indica intenção de não pagar e fraude de identidade; não achei estudo com crédito diário e Pix agendado.

## 3. Matriz de testes (tudo que foi cruzado)
Está no ClickHouse para o Claude Code e o Hermes consultarem: `analytics_ml.motor_testes_resumo` (uma linha por família) e `motor_testes_realizados` (cerca de 27 mil testes, um por linha).
- 391 mil combinações de 1 a 4 variáveis por modo de cobrança; 113 efeitos ajustados por Pix, provedor e mês; sazonalidade controlada; 11 algoritmos × 10 conjuntos × 4 alvos × 2 grupos no dia 0; matriz por idade (dias 1 a 14); tom da conversa em 654 contratos; estudo do dia 0 (62 modelos e 219 testes de eventos do agendamento).
- **Funciona:** Pix e canal de cobrança; comportamento de pagamento; idade e histórico de atraso; endereço dividido com outro cliente (calote 43% contra 30%); contrato anterior que "pagou no começo e parou" (82% contra 22%, só 11 casos).
- **Não ajuda (testado):** bureau, sexo, UF, DDD, nome, CNAE, sócios, mídia, embeddings, funil, PIB, tom da conversa (efeito real mas metade é tamanho de texto e não melhora o modelo), OCR de prints (só 26 casos com valor).
- **A safra importa** (mês de início tem efeito real mesmo descontando Pix e provedor): o motor valida só nos 5 meses mais recentes.
- **Ainda não testado:** leis estaduais, causalidade (só medimos associação) e combinações de 5 ou mais variáveis.

## 4. O que ficou pronto
- **Motor** (versão 10/11.2): 3 previsões (calote, inadimplência, cluster ruim) em 8 idades; chance de cada cluster; score por idade; nos dias 1 a 3 entram candidatos com cadastro (só aqui houve ganho real); regra de ganho para qualquer variável nova.
- **Gold e ClickHouse limpo:** `contrato_risco_360`, `contrato_consulta`, `contrato_piorou`, `cluster_inadimplencia`, `risco_dia0_perfil`, `risco_renovacao`, `risco_acuracia_modelo`, dicionário de nomes; catálogo de tabelas e colunas; 599 colunas documentadas; 2 bancos de lixo apagados; as tabelas do estudo de outubro marcadas como substituídas; backup de 09/11 em quarentena até 10/12.
- **API de risco:** chances de calote, inadimplência e cluster, `/v1/clusters`, `/v1/renovacao`, aviso nos contratos antigos (mais de 50 dias: probabilidade retroativa, use o resultado real).
- **Dashboard (Metabase, dashboard 5):** abas Motor de crédito, Consulta por contrato (filtro de telefone e nome com sugestão), Contratos que pioraram (ordenado por prioridade), Mapa de testes e API e pesos; nomes em português claro e texto explicativo ao lado de cada gráfico.
- **Pipeline diário (Airflow):** CRM, Chatwoot, analytics, bureau, mídia, motor (3 tarefas), tom da conversa por LLM em CPU **só no delta** (nova DAG `tom_llm_diario`) e resumo. Teste ponta a ponta da orquestradora: sucesso em todas as tarefas.
- **Data IA Hub:** ver seção 6.

## 5. Pendências e riscos
- A consulta da API chegou a levar 29 s durante o retreino (a VPS fica com a CPU toda ocupada); medir de novo com a VPS ociosa e, se persistir, separar o ClickHouse do treino.
- Tom da conversa por LLM é **opcional**: usa o 9Router do Hub quando há conta ligada; sem conta, a tarefa pula e o pipeline segue (não vira variável do motor).
- Segurança "repaginada" (credenciais em conexões do Airflow e `.env`, API por IP sem HTTPS, Hub só por túnel, sem domínio): combinada para depois.
- Open Finance entra só como indicativo (dia 0 está no teto de ~0,65 com os dados atuais).
- Calibração por safra; amostra pequena (cerca de 900 contratos, 560 a 720 com resultado): toda margem de erro é grande.

## 6. Data IA Hub (plataforma web dentro da infra deles)
Chat com o Hermes especialista no Data Lake, login único de administrador, **9Router embutido** com telas nativas (Provedores, Combos, Uso, Cota; conta ChatGPT já ligada e combo `principal`), histórico com busca, anexos no MinIO, ditado, arquivos gerados (Excel, CSV, PNG) com cartão de download.
Telas próprias:
- **Motor de crédito:** as 53 variáveis do catálogo, correlação com calote, inadimplência e cada cluster, resultado do teste, **chave liga/desliga** por variável (desligada, o próximo treino ignora; tabela `hub_sandbox.motor_parametros`) e **pedido de variável nova** (pelo formulário ou pelo chat; aparece na aba com estado "pedido" e a rotina noturna `testar_pedidos_do_chat` grava o resultado).
- **Dados e SQL:** catálogo estilo Databricks (bancos e tabelas, descrição, colunas com descrição, amostra de 10 linhas); SQL só leitura como opção secundária (usuário `hub_ia`, grava só em `hub_sandbox`).
- **Extração:** desenho do fluxo (ingestão, trusted, gold, bureau e mídia, motor, LLM opcional) com estado da última execução, tabela de execuções do pipeline (início, fim, duração, estado) e link para o Airflow.
QA feito pela interface (CDP) e com perguntas reais ao agente: contratos ativos e em risco (211 e 84, conferidos no ClickHouse), variáveis mais ligadas ao calote, Excel dos 20 maiores riscos, CSV de calote por canal, gráfico em matplotlib, gráfico de barras criado no Metabase, **pedido de variável nova pelo chat** (linha criada na tabela) e **desligar "Sexo" pelo chat** (religada depois), treino do dia e cluster de maior calote.
Infra: `/opt/capitalcerto/hub` na VPS, portas só em 127.0.0.1 (acesso por túnel SSH 8088), backup do estado do 9Router a cada 30 min. Código: repositório `data-ia-hub`.
Para o Airflow aceitar o login do Hub foi preciso ligar `basic_auth` na API (`AIRFLOW__API__AUTH_BACKENDS` no webserver; backup do compose em `docker-compose.yml.bak_pre_apiauth`).
