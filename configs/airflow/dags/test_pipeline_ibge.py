# Pipeline de teste ponta a ponta, so pra provar a arquitetura funcionando com dado real:
# extrai da API publica do IBGE (sem autenticacao), grava o arquivo bruto no MinIO (camada raw),
# carrega os dados estruturados no ClickHouse (Feature Mart) e um dashboard no Metabase le
# esse resultado. Nao tem nenhuma relacao com o dominio do projeto (credito), e so infraestrutura
# de teste. Pode ser removido quando os DAGs reais de ingestao entrarem (ver SPEC.md secao 3.1).
import json
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

RAW_BUCKET = "raw"
RAW_KEY = "teste_ibge/estados.json"
IBGE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados"


def extract_and_store_raw():
    import requests
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    response = requests.get(IBGE_URL, timeout=30)
    response.raise_for_status()
    payload = response.content

    hook = S3Hook(aws_conn_id="minio_s3")
    if not hook.check_for_bucket(RAW_BUCKET):
        hook.create_bucket(RAW_BUCKET)
    hook.load_bytes(payload, key=RAW_KEY, bucket_name=RAW_BUCKET, replace=True)


def load_clickhouse():
    import os

    import clickhouse_connect
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    hook = S3Hook(aws_conn_id="minio_s3")
    estados = json.loads(hook.read_key(key=RAW_KEY, bucket_name=RAW_BUCKET))

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database="feature_mart",
    )
    client.command("DROP TABLE IF EXISTS test_ibge_estados")
    client.command(
        "CREATE TABLE test_ibge_estados ("
        "id UInt32, sigla String, nome String, "
        "regiao_id UInt32, regiao_sigla String, regiao_nome String"
        ") ENGINE = MergeTree ORDER BY id"
    )

    rows = [
        [e["id"], e["sigla"], e["nome"], e["regiao"]["id"], e["regiao"]["sigla"], e["regiao"]["nome"]]
        for e in estados
    ]
    client.insert(
        "test_ibge_estados",
        rows,
        column_names=["id", "sigla", "nome", "regiao_id", "regiao_sigla", "regiao_nome"],
    )


with DAG(
    dag_id="test_pipeline_ibge",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["teste", "ponta-a-ponta"],
) as dag:
    extract = PythonOperator(task_id="extract_and_store_raw", python_callable=extract_and_store_raw)
    load = PythonOperator(task_id="load_clickhouse", python_callable=load_clickhouse)
    extract >> load
