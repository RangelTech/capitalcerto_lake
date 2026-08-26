# DAG de exemplo, prova que o Airflow consegue de fato manipular arquivo no MinIO e fazer
# insert/select/delete no ClickHouse, nao so que a conexao esta cadastrada (ver SPEC.md
# secao 3.2 e apps/airflow-image, que instala os pacotes que isso exige). Os DAGs reais de
# ingestao (ERP, Chatwoot, bureaus, fotos) entram aqui quando o acesso a cada fonte estiver
# definido.
import os
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator


def check_minio():
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    hook = S3Hook(aws_conn_id="minio_s3")
    bucket = "raw"
    key = "healthcheck/example_healthcheck_dag.txt"

    if not hook.check_for_bucket(bucket):
        hook.create_bucket(bucket)

    hook.load_string("ok", key=key, bucket_name=bucket, replace=True)
    content = hook.read_key(key=key, bucket_name=bucket)
    assert content == "ok", f"esperava 'ok', leu '{content}'"
    hook.delete_objects(bucket=bucket, keys=[key])


def check_clickhouse():
    import clickhouse_connect

    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database="feature_mart",
    )
    client.command(
        "CREATE TABLE IF NOT EXISTS healthcheck (id UInt32, checked_at DateTime) "
        "ENGINE = MergeTree ORDER BY id"
    )
    client.insert("healthcheck", [[1, datetime.utcnow()]], column_names=["id", "checked_at"])

    result = client.query("SELECT count() FROM healthcheck").result_rows[0][0]
    assert result >= 1, "insert no ClickHouse nao surtiu efeito"

    client.command("ALTER TABLE healthcheck DELETE WHERE id = 1")


with DAG(
    dag_id="example_healthcheck",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["exemplo", "healthcheck"],
) as dag:
    PythonOperator(task_id="check_minio", python_callable=check_minio)
    PythonOperator(task_id="check_clickhouse", python_callable=check_clickhouse)
