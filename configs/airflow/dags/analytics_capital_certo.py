# DAG das analytics da Capital Certo (CRM CredHub x Chatwoot) no ClickHouse, banco `analytics`.
# Sem schedule proprio: roda quando a orquestradora_diaria_capital_certo a dispara (depois de chatwoot_diario
# e credhub_diario), ou manualmente. Modelo dimensional completo em analytics_capital_certo_modelo.py.
#
# data_referencia: se o parametro vier vazio, usa o dia da ultima atividade registrada no CRM
# (max(audit_event.created_at)). Assim, enquanto o CRM for uma carga fria parada, o atraso das parcelas nao
# "envelhece" sozinho; quando o banco oficial entrar, a data acompanha o dia real.
import pendulum
from airflow import DAG
from airflow.models.param import Param
from airflow.operators.python import PythonOperator

from analytics_capital_certo_catalogo import ESPECIFICO, GLOSSARIO, TABELAS
from analytics_capital_certo_catalogo_extra import EXTRA
from analytics_capital_certo_modelo import DATABASE, TABLES

SQLS = {n: s for n, _, s in TABLES}  # fora do op_kwargs: o Jinja interpretaria as chaves do SQL


def _ch():
    import os

    import clickhouse_connect

    return clickhouse_connect.get_client(host=os.environ["CLICKHOUSE_HOST"], password=os.environ["CLICKHOUSE_PASSWORD"])


def resolver_data_referencia(**ctx):
    informada = (ctx["params"].get("data_referencia") or "").strip()
    if informada:
        return informada
    ch = _ch()
    d = ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    print("data_referencia automatica (ultima atividade do CRM):", d)
    return d


def extrair_documentos_chatwoot():
    from analytics_documentos_chatwoot import run

    run()


def construir(name, ref):
    sql = SQLS[name]
    esc = lambda s: s.replace("\\", "\\\\").replace("'", "\\'")
    ch = _ch()
    ch.command(f"CREATE DATABASE IF NOT EXISTS {DATABASE}")
    for stmt in sql.format(db=DATABASE, ref=ref).split(";;"):
        if stmt.strip():
            ch.command(stmt, settings={"allow_suspicious_low_cardinality_types": 1})
    if name in TABELAS:
        ch.command(f"ALTER TABLE {DATABASE}.{name} MODIFY COMMENT '{esc(TABELAS[name])}'")
    colunas = [r[0] for r in ch.query(f"SELECT name FROM system.columns WHERE database='{DATABASE}' AND table='{name}'").result_rows]
    sem = []
    for col in colunas:
        txt = ESPECIFICO.get((name, col)) or GLOSSARIO.get(col) or EXTRA.get(col)
        if txt:
            ch.command(f"ALTER TABLE {DATABASE}.{name} COMMENT COLUMN `{col}` '{esc(txt)}'")
        else:
            sem.append(col)
    n = ch.query(f"SELECT count() FROM {DATABASE}.{name}").result_rows[0][0]
    print(f"{DATABASE}.{name}: {n} linhas; colunas sem descricao: {sem}")
    if n == 0:
        raise ValueError(f"{name} ficou vazia")


def validar():
    ch = _ch()
    q = lambda s: ch.query(s).result_rows[0][0]
    checks = {
        "parcelas": (q(f"SELECT count() FROM {DATABASE}.fato_parcela"), q("SELECT count() FROM trusted_credhub.installment")),
        "pagamentos": (q(f"SELECT count() FROM {DATABASE}.fato_pagamento"), q("SELECT count() FROM trusted_credhub.payment")),
        "clientes": (q(f"SELECT count() FROM {DATABASE}.dim_cliente"), q("SELECT count() FROM trusted_credhub.customer")),
        "contratos_dim": (q(f"SELECT count() FROM {DATABASE}.dim_contrato"), q("SELECT count() FROM trusted_credhub.contract")),
        "valor_principal_ativos": (
            float(q(f"SELECT sum(valor_principal) FROM {DATABASE}.fato_contrato")),
            float(q("SELECT sum(principal_amount) FROM trusted_credhub.contract WHERE deleted_at IS NULL")),
        ),
    }
    erros = {k: v for k, v in checks.items() if abs(v[0] - v[1]) > 0.01}
    print("validacao (analytics, trusted):", checks)
    sem_desc = ch.query(
        f"SELECT table, count() FROM system.columns WHERE database='{DATABASE}' AND comment='' AND table NOT LIKE '.inner%' GROUP BY table"
    ).result_rows
    if sem_desc:
        print("AVISO colunas sem descricao:", sem_desc)
    if erros:
        raise ValueError(f"contagem diverge entre analytics e trusted: {erros}")


with DAG(
    dag_id="analytics_capital_certo",
    start_date=pendulum.datetime(2026, 10, 1, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={"data_referencia": Param("", type="string", description="vazio = ultima atividade do CRM; ou AAAA-MM-DD")},
    tags=["capital_certo", "analytics", "clickhouse", "dimensional"],
) as dag:
    ref_task = PythonOperator(task_id="resolver_data_referencia", python_callable=resolver_data_referencia)
    docs_task = PythonOperator(task_id="extrair_documentos_chatwoot", python_callable=extrair_documentos_chatwoot)
    tarefas = {"extrair_documentos_chatwoot": docs_task}
    ref_tpl = "{{ ti.xcom_pull(task_ids='resolver_data_referencia') }}"
    for nome, deps, sql in TABLES:
        tarefas[nome] = PythonOperator(task_id=f"build_{nome}", python_callable=construir, op_kwargs={"name": nome, "ref": ref_tpl})
    for nome, deps, _ in TABLES:
        t = tarefas[nome]
        if not deps:
            ref_task >> t
        for d in deps:
            tarefas[d] >> t
        ref_task >> t
    validar_task = PythonOperator(task_id="validar_analytics", python_callable=validar)
    for nome, _, _ in TABLES:
        tarefas[nome] >> validar_task
