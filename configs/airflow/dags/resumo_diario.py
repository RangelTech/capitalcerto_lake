# DAG do resumo por cliente com LLM pequeno em CPU (credit_resumo.py): todos os clientes (resumo estruturado, sem LLM, e barato). Roda por ultimo na noite (e a mais pesada).
# Sem schedule proprio: a orquestradora_diaria_capital_certo dispara depois da midia e do motor. Teto de 90 min; o que nao couber fica para a proxima noite.
import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="resumo_diario",
    start_date=pendulum.datetime(2026, 11, 10, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["capital_certo", "llm", "resumo"],
) as dag:
    BashOperator(
        task_id="gerar_resumos_recentes",
        bash_command="cd /opt/airflow/dags && timeout 1500 python -u credit_resumo.py 5000 1 todos",
        execution_timeout=pendulum.duration(minutes=30),
    )
