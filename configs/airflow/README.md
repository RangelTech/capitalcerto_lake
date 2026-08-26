# airflow

Orquestracao das pipelines de ingestao e transformacao, LocalExecutor, um DAG por fonte de dado.

## O que tem aqui hoje

`dags/example_healthcheck_dag.py` e um DAG de exemplo, so confirma que o Airflow consegue
resolver as conexoes com MinIO e ClickHouse. Os DAGs reais de ingestao (ERP, Chatwoot, bureaus de
credito, fotos de estabelecimento) entram aqui quando o acesso a cada fonte estiver confirmado
com o cliente.

## Conexoes esperadas

`minio_s3` e `clickhouse_default` sao criadas automaticamente no boot via variavel de ambiente
(`AIRFLOW_CONN_MINIO_S3` e `AIRFLOW_CONN_CLICKHOUSE_DEFAULT`, ver `compose/docker-compose.yml`),
nao precisam ser cadastradas manualmente na interface do Airflow.

## Variaveis de ambiente

`AIRFLOW_FERNET_KEY`, chave tecnica de criptografia interna, distinta do login administrativo.
`AIRFLOW_ADMIN_USER` e `AIRFLOW_ADMIN_PASSWORD`, login do painel, reaproveitam o usuario
administrativo unico (ver `SPEC.md`, secao 6.1).

## Convencao de DAG

Um DAG por fonte de dado (`ingest_erp`, `ingest_chatwoot`, `ingest_bureau_x`, e assim por diante).
Cada DAG extrai da fonte, grava a camada raw no MinIO, transforma pra trusted e carrega no
Feature Mart do ClickHouse.
