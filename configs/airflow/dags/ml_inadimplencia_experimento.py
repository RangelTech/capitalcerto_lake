# DAG do experimento de ML de inadimplencia da Capital Certo. Separada de proposito das DAGs de producao:
# sem schedule, nao e chamada pela orquestradora diaria, grava so no banco `analytics_ml`.
#   1. perfil_e_conversas: perfil x inadimplencia com fator normalizador + modelo de texto das conversas (numpy puro)
#   2. quadros: dados cadastrais x quadro de pagamento (nunca pagou, pagou e parou, atrasa mas paga, paga em dia), com normalizador
#   3. modelos (ml_modelo): conjuntos A cohort, B +cadastro, C +Receita, D +conversas; Receita x quadros; alerta precoce (primeiros 7 dias)
#   4. llm_piloto (opcional): extracao de features com o Qwen particular do Lucas (beta). Roda so com rodar_llm = true E a
#      Variable do Airflow `qwen_api_key` preenchida. Nao e produto: serve para levantar hipoteses.
import pendulum
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator, ShortCircuitOperator


def _perfil_e_conversas(**ctx):
    import ml_inadimplencia

    ml_inadimplencia.rodar((ctx["params"].get("data_referencia") or "").strip() or None)


def _quadros(**ctx):
    import ml_quadros

    ml_quadros.rodar((ctx["params"].get("data_referencia") or "").strip() or None)


def _modelos(**ctx):
    import ml_modelo

    ml_modelo.rodar((ctx["params"].get("data_referencia") or "").strip() or None)


def _receita_quadros(**ctx):
    import ml_modelo

    ml_modelo.receita_x_quadros((ctx["params"].get("data_referencia") or "").strip() or None)


def _alerta_precoce(**ctx):
    import ml_modelo

    ml_modelo.alerta_precoce((ctx["params"].get("data_referencia") or "").strip() or None)


def _tem_llm(**ctx):
    from airflow.models import Variable

    return bool(ctx["params"].get("rodar_llm")) and bool(Variable.get("qwen_api_key", default_var=""))


def _llm(**ctx):
    from airflow.models import Variable

    import ml_llm_piloto

    F, perfil, ref, n = ml_llm_piloto.rodar(ref=(ctx["params"].get("data_referencia") or "").strip() or None, chave=Variable.get("qwen_api_key"))
    a, m = ml_llm_piloto.analisar(F, perfil, ref, n)
    ml_llm_piloto.gravar(F, a, m)


with DAG(
    dag_id="ml_inadimplencia_experimento",
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={"data_referencia": Param("", type="string", description="vazio = ultima atividade do CRM"),
            "rodar_llm": Param(False, type="boolean", description="liga o piloto com o Qwen (exige a Variable qwen_api_key)")},
    tags=["capital_certo", "ml", "experimento"],
) as dag:
    base = PythonOperator(task_id="perfil_e_conversas", python_callable=_perfil_e_conversas)
    gate = ShortCircuitOperator(task_id="tem_llm_e_foi_pedido", python_callable=_tem_llm)
    llm = PythonOperator(task_id="llm_piloto", python_callable=_llm, execution_timeout=pendulum.duration(hours=1))
    quadros = PythonOperator(task_id="quadros_de_pagamento", python_callable=_quadros)
    modelos = PythonOperator(task_id="modelos_por_conjunto", python_callable=_modelos)
    receita = PythonOperator(task_id="receita_x_quadros", python_callable=_receita_quadros)
    precoce = PythonOperator(task_id="alerta_precoce", python_callable=_alerta_precoce)
    base >> quadros >> [modelos, receita, precoce] >> gate >> llm
