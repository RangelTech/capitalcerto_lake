# DAG de ingestao diaria do CRM CredHub: backup do portal (credhub.pro) -> staging Postgres -> raw (MinIO) + trusted (ClickHouse).
# Sem schedule proprio: quem dispara e a orquestradora_diaria_capital_certo (03:30 de Brasilia, depois do backup das 03:00).
#
#   esperar_backup -> baixar_e_restaurar -> validar_manifest -> snapshot_trusted -> limpar_staging
#
# Teste com backup de outro dia: disparar com conf {"data": "AAAA-MM-DD"} (o portal guarda 7 dias).
import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.sensors.python import PythonSensor

import credhub_backup as bk

with DAG(
    dag_id="credhub_diario",
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": pendulum.duration(minutes=5)},
    tags=["credhub", "crm", "ingestao", "raw", "trusted"],
) as dag:
    esperar = PythonSensor(
        task_id="esperar_backup",
        python_callable=bk.esperar_backup,
        poke_interval=600,
        timeout=4 * 3600,
        mode="reschedule",
        retries=0,
    )
    restaurar = PythonOperator(task_id="baixar_e_restaurar", python_callable=bk.baixar_e_restaurar)
    validar = PythonOperator(task_id="validar_manifest", python_callable=bk.validar_manifest)

    def _snapshot(**context):
        from credhub_ingest import run_pipeline

        bancos = context["ti"].xcom_pull(task_ids="baixar_e_restaurar", key="staging")
        run_pipeline(data=bk.dia_alvo(context), bancos=bancos)

    snapshot = PythonOperator(task_id="snapshot_trusted", python_callable=_snapshot)
    limpar = PythonOperator(task_id="limpar_staging", python_callable=bk.limpar_staging, trigger_rule="all_done")

    esperar >> restaurar >> validar >> snapshot >> limpar
