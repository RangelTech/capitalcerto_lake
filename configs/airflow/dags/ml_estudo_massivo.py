# DAG do estudo massivo de inadimplencia da Capital Certo (experimento, sem schedule, fora da orquestradora).
#   1. llm_v2_e_embeddings: o Qwen particular do Lucas (beta, NAO produto) le a conversa pre-contrato e extrai features + embedding.
#      So roda com a Variable do Airflow `qwen_api_key` preenchida e o parametro rodar_llm = true.
#   2. estudo_massivo: triagem de ~1.400 combinacoes alvo x blocos x algoritmo, validacao contra o acaso estratificado por cohort,
#      selecao recursiva, regras com limite de familia e analise dos embeddings. Grava analytics_ml.estudo_*.
import pendulum
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.bash import BashOperator
from airflow.operators.python import BranchPythonOperator, PythonOperator


def _escolhe(**ctx):
    from airflow.models import Variable

    quer = bool(ctx["params"].get("rodar_llm")) and bool(Variable.get("qwen_api_key", default_var=""))
    return "llm_v2_e_embeddings" if quer else "estudo_massivo"


def _llm(**ctx):
    from airflow.models import Variable

    import estudo_llm_v2

    estudo_llm_v2.rodar(chave=Variable.get("qwen_api_key"))


with DAG(
    dag_id="ml_estudo_massivo",
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={"rodar_llm": Param(False, type="boolean", description="refaz as features e embeddings com o Qwen (exige a Variable qwen_api_key)"),
            "rapido": Param(False, type="boolean", description="so 4 algoritmos rapidos, para teste")},
    tags=["capital_certo", "ml", "experimento", "massivo"],
) as dag:
    gate = BranchPythonOperator(task_id="quer_llm", python_callable=_escolhe)
    llm = PythonOperator(task_id="llm_v2_e_embeddings", python_callable=_llm, execution_timeout=pendulum.duration(hours=2))
    # processo proprio: a tarefa do Airflow e daemon e nao pode criar o pool de processos que o estudo usa
    estudo = BashOperator(task_id="estudo_massivo", bash_command="cd /opt/airflow/dags && RAPIDO={{ '1' if params.rapido else '' }} python -u estudo_massivo.py",
                          execution_timeout=pendulum.duration(hours=6), trigger_rule="none_failed_min_one_success")
    gate >> llm >> estudo
    gate >> estudo
