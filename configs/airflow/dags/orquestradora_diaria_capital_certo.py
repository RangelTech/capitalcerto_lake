# Orquestradora diaria da Capital Certo: dispara em paralelo as ingestoes do Chatwoot e do CRM CredHub e,
# quando as duas terminam com sucesso, dispara as analytics (modelo dimensional no ClickHouse).
#
#   chatwoot_diario ─┐
#                    ├─> analytics_capital_certo
#   credhub_diario  ─┘   (+ bureau_gold_diario depois do chatwoot, e credit_motor_diario depois da analytics)
#
# chatwoot_diario e credhub_diario NAO tem schedule proprio: so rodam por aqui (ou manualmente), para nunca
# haver analytics rodando sobre uma ingestao pela metade. Horario: 03:30 de Sao Paulo (o host da VPS esta em
# Europe/Berlin, por isso o tz explicito).
import pendulum
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

COMUM = dict(wait_for_completion=True, poke_interval=30, reset_dag_run=True, allowed_states=["success"], failed_states=["failed"])

with DAG(
    dag_id="orquestradora_diaria_capital_certo",
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    schedule="30 3 * * *",
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    tags=["capital_certo", "orquestradora", "chatwoot", "credhub", "analytics"],
) as dag:
    inicio = EmptyOperator(task_id="inicio")
    chatwoot = TriggerDagRunOperator(task_id="ingestao_chatwoot", trigger_dag_id="chatwoot_diario", **COMUM)
    credhub = TriggerDagRunOperator(task_id="ingestao_credhub", trigger_dag_id="credhub_diario", **COMUM)
    analytics = TriggerDagRunOperator(
        task_id="analytics", trigger_dag_id="analytics_capital_certo", conf={"data_referencia": ""}, **COMUM
    )
    motor = TriggerDagRunOperator(task_id="motor_credito", trigger_dag_id="credit_motor_diario", conf={"data": ""}, **COMUM)
    bureau = TriggerDagRunOperator(task_id="bureau_gold", trigger_dag_id="bureau_gold_diario", **COMUM)
    inicio >> [chatwoot, credhub] >> analytics >> motor
    chatwoot >> bureau  # o bureau Gold roda depois das notas novas, em paralelo com a analytics
    midia = TriggerDagRunOperator(task_id="midia", trigger_dag_id="midia_diario", **COMUM)
    chatwoot >> midia  # anexos novos: baixa e processa depois da carga do Chatwoot (nao bloqueia a analytics)
    tom = TriggerDagRunOperator(task_id="tom_llm", trigger_dag_id="tom_llm_diario", **COMUM)
    analytics >> tom   # tom da conversa por LLM em CPU, so no delta (conversas novas)
    resumo = TriggerDagRunOperator(task_id="resumo_llm", trigger_dag_id="resumo_diario", **COMUM)
    [motor, midia, bureau, tom] >> resumo  # por ultimo: o LLM e a etapa mais pesada e nao bloqueia nada
