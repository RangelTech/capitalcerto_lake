# Imagem do Airflow deste projeto com os pacotes que os DAGs precisam pra falar com MinIO
# (S3 compativel) e ClickHouse. A imagem oficial apache/airflow nao vem com isso instalado
# por padrao, so registrar AIRFLOW_CONN_* nao e suficiente sem o driver (ver
# configs/airflow/README.md).
ARG AIRFLOW_VERSION=2.9.3
FROM apache/airflow:${AIRFLOW_VERSION}

RUN pip install --no-cache-dir \
    apache-airflow-providers-amazon \
    clickhouse-connect
