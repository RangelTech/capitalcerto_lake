# ninerouter

Instancia propria do 9Router para este projeto, isolada de qualquer outra infraestrutura da
Rangel Tech (ver `SPEC.md`, secao 1). Faz o roteamento de LLM/VLM usado pra extrair sinal de
conversas do Chatwoot e de fotos de estabelecimento, nunca toma decisao de credito sozinho, so
alimenta features que entram no Feature Mart.

## O que tem aqui hoje

`app.py` e um placeholder minimo, so uma rota `/health`, pra a stack subir completa e testavel
antes do pacote real do 9Router ser integrado nesta pasta.

## Variaveis de ambiente

`NINEROUTER_API_KEYS`, chaves dos provedores de LLM/VLM que o roteador usa.

## Com quem conversa

Recebe chamadas do Airflow durante as pipelines de extracao de sinal. Nao guarda dado
permanente, so processa e devolve resultado.
