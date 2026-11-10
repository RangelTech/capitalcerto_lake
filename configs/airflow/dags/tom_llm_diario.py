# DAG do tom da conversa por LLM OPCIONAL via Data IA Hub (credit_tom_hub.py; o 9Router do Hub usa a conta do cliente), SO NO DELTA: contratos com conversa nova ou alterada, os mais recentes primeiro.
# Sem schedule proprio: a orquestradora_diaria_capital_certo dispara depois da carga do Chatwoot e da analytics; roda antes do resumo (que e a etapa mais pesada).
# Teto de 80 min; o que nao couber fica para a proxima noite. Gera analytics.tom_conversa_contrato.
import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="tom_llm_diario",
    start_date=pendulum.datetime(2026, 11, 10, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["capital_certo", "llm", "tom", "delta"],
) as dag:
    BashOperator(
        task_id="tom_do_delta",
        bash_command="cd /opt/airflow/dags && timeout 3000 python -u credit_tom_hub.py 40 || true",   # LLM OPCIONAL (9Router do Data IA Hub): se nao houver conta ou o Hub estiver fora, pula e segue
        execution_timeout=pendulum.duration(minutes=55),
    )
