# DAG do motor de credito: retreina todo dia, escolhe o melhor modelo por etapa, calibra e pontua os contratos ativos.
# Sem schedule proprio: a orquestradora_diaria_capital_certo dispara depois da analytics (precisa de fato_contrato e fato_parcela do dia).
# Depois do treino: clusters_etapas_e_gold (probabilidade por cluster, score por etapa, Gold) e dicionario_nomes_claros.
# Teste/forcar um dia: disparar com conf {"data": "AAAA-MM-DD"}.
import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

with DAG(
    dag_id="credit_motor_diario",
    start_date=pendulum.datetime(2026, 11, 9, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=10)},
    tags=["capital_certo", "credito", "ml", "motor"],
) as dag:

    def _rodar(**context):
        import credit_engine

        conf = (context["dag_run"].conf or {}) if context.get("dag_run") else {}
        credit_engine.rodar_dia(conf.get("data"))

    def _pos(**context):
        import credit_pos_motor

        credit_pos_motor.main()   # chance por cluster, score por etapa e as views/tabelas Gold (contrato_risco_360, contrato_consulta, contrato_piorou...)

    def _variaveis(**context):
        import credit_variaveis

        credit_variaveis.main()   # catalogo de variaveis com correlacoes (aba Motor de credito do Data IA Hub)

    def _pedidos(**context):
        import credit_pedidos

        credit_pedidos.main()     # testa as variaveis pedidas pelo chat do Hub (hub_sandbox.motor_parametros)

    def _resumos_llm(**context):
        import credit_var_resumo

        try:
            credit_var_resumo.main()   # explicacao de cada variavel e resumo do acerto por LLM (opcional: sem a conta de IA, pula)
        except Exception as e:  # noqa
            print("resumos por LLM pulados:", str(e)[:200])

    def _dicionario(**context):
        import nomes_claros

        nomes_claros.main()

    t1 = PythonOperator(task_id="retreinar_e_pontuar", python_callable=_rodar, execution_timeout=pendulum.duration(hours=2))
    t2 = PythonOperator(task_id="clusters_etapas_e_gold", python_callable=_pos, execution_timeout=pendulum.duration(minutes=40))
    t3 = PythonOperator(task_id="dicionario_nomes_claros", python_callable=_dicionario, execution_timeout=pendulum.duration(minutes=5))
    t4 = PythonOperator(task_id="catalogo_de_variaveis", python_callable=_variaveis, execution_timeout=pendulum.duration(minutes=15))
    t5 = PythonOperator(task_id="testar_pedidos_do_chat", python_callable=_pedidos, execution_timeout=pendulum.duration(minutes=40))
    t6 = PythonOperator(task_id="explicar_variaveis_llm", python_callable=_resumos_llm, execution_timeout=pendulum.duration(minutes=35))
    t1 >> t2 >> t3 >> t4 >> t5 >> t6
