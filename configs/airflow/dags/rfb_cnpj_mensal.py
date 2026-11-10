# DAG da ingestao mensal da base de CNPJ da Receita Federal (trusted_rfb no ClickHouse da Capital Certo).
# Roda no SEGUNDO DOMINGO de cada mes, 04:00 de Sao Paulo. Cron "dia 8 a 14" + checagem de domingo, porque no cron
# padrao dia-do-mes e dia-da-semana juntos viram OU e rodariam em todo domingo. A Receita publica por volta do dia 14; no segundo
# domingo a pasta do mes ainda pode nao existir, e a DAG carrega a pasta completa mais recente (idempotente: se ela ja
# esta carregada, nao refaz). Fora da orquestradora diaria. Disparo manual (run_type manual) ignora a checagem do domingo;
# o parametro forcar recarrega mesmo se o mes ja estiver carregado.
import pendulum
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator, ShortCircuitOperator

TZ = pendulum.timezone("America/Sao_Paulo")


def _segundo_domingo(**ctx):
    if ctx["dag_run"].run_type == "manual":
        return True
    d = ctx["data_interval_end"].in_timezone(TZ)
    ok = d.weekday() == 6 and 8 <= d.day <= 14
    print("segundo domingo?", d.to_date_string(), ok)
    return ok


def _descobrir(**ctx):
    import rfb_cnpj

    mes = rfb_cnpj.mes_mais_recente()
    if rfb_cnpj.ja_carregado(mes) and not ctx["params"].get("forcar"):
        print("mes", mes, "ja carregado; nada a fazer")
        return ""
    print("mes a carregar:", mes)
    return mes


def _carregar(tabela, **ctx):
    import rfb_cnpj

    mes = ctx["ti"].xcom_pull(task_ids="descobrir_mes")
    if not mes:
        return
    rfb_cnpj.carregar_raw(tabela, mes)
    rfb_cnpj.tipar_e_publicar(tabela, mes)


def _enriquecer(**ctx):
    import rfb_cnpj

    mes = ctx["ti"].xcom_pull(task_ids="descobrir_mes")
    if not mes:
        return
    ref = rfb_cnpj._ch().query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    rfb_cnpj.enriquecer(ref)


def _retencao(**ctx):
    import rfb_cnpj

    rfb_cnpj.limpar_minio(manter=2)


with DAG(
    dag_id="rfb_cnpj_mensal",
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    schedule="0 4 8-14 * *",
    catchup=False,
    max_active_runs=1,
    max_active_tasks=2,  # 2 downloads no maximo: nao disputa disco e CPU com o resto da VPS
    params={"forcar": Param(False, type="boolean", description="recarregar mesmo que o mes ja esteja carregado")},
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=15)},
    tags=["capital_certo", "receita_federal", "cnpj", "mensal", "ingestao"],
) as dag:
    domingo = ShortCircuitOperator(task_id="e_o_segundo_domingo", python_callable=_segundo_domingo)
    descobrir = PythonOperator(task_id="descobrir_mes", python_callable=_descobrir)
    domingo >> descobrir
    tarefas = []
    for t in ["cnaes", "motivos", "municipios", "naturezas", "paises", "qualificacoes", "simples", "empresas", "socios", "estabelecimentos"]:
        x = PythonOperator(task_id=f"carregar_{t}", python_callable=_carregar, op_kwargs={"tabela": t}, execution_timeout=pendulum.duration(hours=3))
        descobrir >> x
        tarefas.append(x)
    enriquecer = PythonOperator(task_id="enriquecer_clientes", python_callable=_enriquecer)
    retencao = PythonOperator(task_id="retencao_minio", python_callable=_retencao)
    tarefas >> enriquecer >> retencao
