# DAG de exemplo, so pra confirmar que o Airflow esta rodando e enxergando MinIO e ClickHouse
# (ver SPEC.md secao 3.2). Os DAGs reais de ingestao (ERP, Chatwoot, bureaus, fotos) entram
# aqui quando o acesso a cada fonte estiver definido.
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator


def check_connections():
    from airflow.hooks.base import BaseHook

    BaseHook.get_connection("minio_s3")
    BaseHook.get_connection("clickhouse_default")


with DAG(
    dag_id="example_healthcheck",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["exemplo", "healthcheck"],
) as dag:
    PythonOperator(
        task_id="check_connections",
        python_callable=check_connections,
    )
