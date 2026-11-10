# Risk API v1: consulta por telefone e risco de contrato

Base: `http://169.58.240.203:8000` (IP direto por enquanto). Autenticacao: cabecalho `X-API-Key` (chave no cofre: `capitalcerto-risk-api.json`; no servidor, `RISK_API_KEYS` em `/opt/capitalcerto/compose/.env`, aceita varias chaves separadas por virgula, uma por consumidor). Sem a chave: 401. Documentacao interativa: `/docs`.

Os dados sao os da **ultima carga diaria** do lake (a carga roda de madrugada); a **inferencia de risco roda na hora**, com os modelos que o motor treina todo dia e salva em `models/credit_motor/ultimo`.

## POST /v1/consulta

```bash
curl -s http://169.58.240.203:8000/v1/consulta -H "X-API-Key: $CHAVE" -H "Content-Type: application/json" \
  -d '{"telefone": "(61) 98563-2169", "contrato": {"valor_principal": 1000, "qtd_parcelas_contratadas": 60, "provedor_pagamento": "PLUGGY", "pix_agendado": true}}'
```

Corpo: `telefone` (preferido; aceita `+55`, parenteses, hifen) **ou** `cpf` **ou** `cnpj`; `contrato` e opcional e descreve o contrato a simular (campos: `valor_principal`, `qtd_parcelas_contratadas`, `taxa_juros_pct`, `valor_parcela`, `provedor_pagamento`, `pix_agendado`). Tambem existe `GET /v1/consulta?telefone=...`.

Como acha a pessoa: telefone (DDD + ultimos 8 digitos) no CRM e no Chatwoot; CPF/CNPJ no CRM e nos relatorios de bureau; depois liga cliente e contatos entre si pela ponte do lake. Resposta (blocos presentes so quando ha dado):

| Bloco | Conteudo |
|---|---|
| `encontrado`, `achou_por` | se achou e por qual chave |
| `pessoas` | cadastro do CRM (documento mascarado), situacao atual, contratos, total emprestado e pago, atraso em aberto |
| `crm.contratos` | cada contrato: datas, valor, parcelas pagas e atrasadas, atraso, saldo, provedor, Pix agendado e **o score do motor** |
| `chatwoot` | contatos e ate 15 conversas com estagio do funil e o que o bureau diz: Dora (veredito, score PF e PJ, dividas, protestos, processos) e integridade DeskData (identidade, vendas mensais, vinculo da maquininha) |
| `midia`, `midia_pdf` | anexos por tipo e, nos PDFs, extratos e relatorios de vendas encontrados |
| `risco.contratos_existentes` | probabilidade de calote de cada contrato existente, faixa, confianca, motivos e, se ja se sabe, o `calote_observado` |
| `risco.novo_contrato` | simulacao de um contrato **novo** hoje (modelo da etapa D+0) com as premissas usadas e `variaveis_sem_dado` |

## Como ler o risco

`prob_calote` = probabilidade calibrada de **20% ou mais das parcelas (depois da etapa e ate o dia 45) ficarem em aberto e vencidas**. Faixas: baixo < 15%, medio 15 a 30%, alto 30 a 50%, muito_alto >= 50%.

**A confianca depende da idade do contrato** (campo `confianca_modelo`, vinda do AUC fora do tempo do ultimo treino):

| Idade | AUC | Confianca |
|---|---|---|
| D+0 (contrato novo) | ~0,59 | **baixa**: no dia da contratacao o modelo separa pouco; usar como complemento |
| D+5 | ~0,74 | media |
| D+7 a D+14 | ~0,80 a 0,85 | alta |

Contratos com mais de 14 dias usam o modelo de D+14 e, quando o resultado ja e conhecido, trazem `calote_observado` para auditoria.

**Premissa importante da simulacao:** se `pix_agendado` nao for informado, assume **sim** (94% dos contratos novos de out/nov sao Pix agendado), e o Pix agendado e hoje o maior fator de calote observado. Por isso a simulacao de contrato novo tende a `muito_alto` ate o front informar o provedor e o modo de cobranca reais.

## Outras rotas

- `GET /v1/score/contrato/{contrato_id}`: score de um contrato (da tabela `analytics.score_contrato_atual`).
- `GET /v1/modelo`: etapas, algoritmo, AUC e confianca do ultimo treino.
- `GET /health`: sem chave.

## Operacao

Modelos recarregados a cada 30 min do MinIO. Latencia medida: 0,3 a 1,0 s por consulta (3 consultas reais). Imagem: `apps/risk-api` (FastAPI, mesmas versoes de numpy, scikit-learn e lightgbm do Airflow: **precisam ser iguais** para o arquivo de modelo carregar). Se o treino diario piorar mais de 0,05 de AUC numa etapa, o motor mantem o modelo anterior (campo `fallback` em `analytics_ml.motor_execucoes`).
