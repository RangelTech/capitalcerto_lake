# airflow

Orquestracao das pipelines de ingestao e transformacao, LocalExecutor, um DAG por fonte de dado.

## O que tem aqui hoje

`dags/example_healthcheck_dag.py` e um DAG de exemplo, so confirma que o Airflow consegue
resolver as conexoes com MinIO e ClickHouse. Os DAGs reais de ingestao (ERP, Chatwoot, bureaus de
credito, fotos de estabelecimento) entram aqui quando o acesso a cada fonte estiver confirmado
com o cliente.

## Imagem propria

A imagem oficial `apache/airflow` sozinha nao consegue falar com MinIO nem com ClickHouse, faltam
os pacotes. Este projeto usa uma imagem propria (`apps/airflow-image/Dockerfile`) que instala
`apache-airflow-providers-amazon` (S3, MinIO fala o mesmo protocolo) e `clickhouse-connect`.
Testado ao vivo em 2026-08-26 antes dessa correcao: sem a imagem propria, `AIRFLOW_CONN_*`
sozinho nao faz nada, e a tentativa de usar o driver de ClickHouse falhava por falta de pacote.

## Conexoes esperadas

`minio_s3` e criada automaticamente no boot via variavel de ambiente (`AIRFLOW_CONN_MINIO_S3`,
ver `compose/docker-compose.yml`), usa o provider da Amazon instalado na imagem propria, nao
precisa ser cadastrada manualmente na interface do Airflow.

ClickHouse nao usa o mecanismo de Connection do Airflow, nao existe provider oficial mantido pra
isso. Os DAGs leem `CLICKHOUSE_HOST` e `CLICKHOUSE_PASSWORD` como variavel de ambiente comum e
usam a biblioteca `clickhouse-connect` direto no codigo Python da task, mesmo padrao usado na
Risk API.

## Variaveis de ambiente

`AIRFLOW_FERNET_KEY`, chave tecnica de criptografia interna, distinta do login administrativo.
`AIRFLOW_ADMIN_USER` e `AIRFLOW_ADMIN_PASSWORD`, login do painel, reaproveitam o usuario
administrativo unico (ver `SPEC.md`, secao 6.1).

## Convencao de DAG

Um DAG por fonte de dado (`ingest_erp`, `ingest_chatwoot`, `ingest_bureau_x`, e assim por diante).
Cada DAG extrai da fonte, grava a camada raw no MinIO, transforma pra trusted e carrega no
Feature Mart do ClickHouse.
