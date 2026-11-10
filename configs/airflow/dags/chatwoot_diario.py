# DAG da ingestao diaria do Chatwoot do cliente (raw MinIO + trusted ClickHouse).
# Spec completa: skills/capitalcerto/references/spec_ingestao_chatwoot.md (repo personal-skills).
import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

with DAG(
    dag_id="chatwoot_diario",
    start_date=pendulum.datetime(2026, 9, 30, tz="America/Sao_Paulo"),
    schedule=None,  # disparada pela orquestradora_diaria_capital_certo (03:30 SP)
    # por isso o tz explicito aqui, nao dar pra confiar no horario local do container.
    catchup=False,  # watermark ja cobre atraso sozinho (spec secao 5.1), nao replay de dias
    max_active_runs=1,  # nunca duas execucoes batendo no servidor do cliente ao mesmo tempo
    default_args={
        "retries": 3,
        "retry_delay": pendulum.duration(minutes=5),
    },
    tags=["chatwoot", "ingestao", "raw", "trusted"],
) as dag:

    def _run():
        from chatwoot_ingest import run_pipeline

        run_pipeline()

    ingest = PythonOperator(task_id="extract_and_load_all_tables", python_callable=_run)
