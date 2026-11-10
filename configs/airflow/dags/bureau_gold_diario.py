# DAG do bureau na Gold: parseia as notas Dora e DeskData do Chatwoot (sem LLM, so regra) e reconstroi
# analytics.dim_relatorio_bureau, fato_bureau_dora, fato_bureau_integridade, fato_bureau_divida, fato_bureau_processo e ficha_credito_conversa.
# Sem schedule proprio: a orquestradora_diaria_capital_certo dispara depois do chatwoot_diario.
import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

with DAG(
    dag_id="bureau_gold_diario",
    start_date=pendulum.datetime(2026, 11, 9, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=5)},
    tags=["capital_certo", "chatwoot", "bureau", "gold"],
) as dag:

    def _rodar(**_):
        import bureau_gold
        import credit_features as cf

        bureau_gold.rodar()
        bureau_gold.ficha_credito_conversa(cf.conecta())

    PythonOperator(task_id="parsear_e_gravar", python_callable=_rodar, execution_timeout=pendulum.duration(minutes=40))
