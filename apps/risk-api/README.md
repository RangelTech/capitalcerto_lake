# risk-api

Motor de score e decisao de credito do projeto. Unico servico deste repositorio com codigo
proprietario, os demais sao imagens publicas configuradas.

## O que faz hoje

A API expoe duas rotas: `/health`, pra checagem de disponibilidade, e `/score`, que recebe um
`contract_id`, busca as features desse contrato no Feature Mart (ClickHouse) e devolve um score
financeiro, um score de risco de golpe e uma decisao.

O modelo de credito real ainda nao existe, esta em fase de estudo e rotulagem. No lugar dele roda
um modelo dummy (`app/model/dummy_model.py`), que devolve um score deterministico so pra manter a
API testavel de ponta a ponta. O arquivo tem um aviso de placeholder no topo.

## Onde entra o modelo real

`app/model/interface.py` define o contrato que qualquer modelo precisa respeitar: um metodo
`predict(features)` que devolve score financeiro, score de golpe, decisao e versao do modelo.
Quando o modelo de credito estiver pronto, ele entra como uma nova implementacao dessa interface
dentro de `app/model/`, e `app/main.py` passa a instanciar essa implementacao no lugar do
`DummyModel`. Nenhuma rota, schema ou configuracao externa precisa mudar por causa disso.

## Variaveis de ambiente

`RISK_API_SECRET_KEY`, chave tecnica interna da API. `CLICKHOUSE_HOST` e `CLICKHOUSE_PASSWORD`,
conexao com o Feature Mart. Nenhuma delas e login de operador humano, sao credenciais tecnicas
distintas do usuario administrativo unico usado nos paineis (ver `SPEC.md`, secao 6.1).

## Com quem conversa

Le do ClickHouse (Feature Mart) pra buscar as features de um contrato. Nao escreve em nenhum
outro servico. E chamada de fora, provavelmente pela plataforma de contratos do cliente, atraves
do Traefik.

## Rodando local

```
cd apps/risk-api
pip install -r requirements.txt
CLICKHOUSE_HOST=localhost CLICKHOUSE_PASSWORD=change-me RISK_API_SECRET_KEY=change-me \
  uvicorn app.main:app --reload
```

Testes em `tests/`, rodar com `pytest` dentro desta pasta.
