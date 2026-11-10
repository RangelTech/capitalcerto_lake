# Ingestao diaria do CRM CredHub: Postgres -> raw (CSV no MinIO) + trusted (ClickHouse trusted_credhub).
# Fonte: conexao Airflow `credhub_postgres`. Hoje aponta para o Postgres local com o dump de 2026-10-01
# (servico credhub-postgres no compose); quando o banco oficial for liberado, basta editar a conexao.
#
# Estrategia: snapshot completo por execucao com troca atomica (EXCHANGE TABLES). O banco inteiro tem ~30 MB,
# e snapshot pega hard delete e correcao retroativa sem logica de watermark. Se o volume crescer, as
# tabelas de evento (AuditEvent, *WebhookEvent, Reconciliation) sao as candidatas a carga incremental por createdAt.
import csv
import io
import json
import os
import re

import pendulum

from credhub_catalog import COL_DESC, GENERIC_COL, NORMALIZE, SKIP_COLS, SKIP_TABLES, TABLE_DESC

RAW_BUCKET = "raw"
TRUSTED_DB = "trusted_credhub"


def snake(s):
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", s)
    return re.sub(r"(?<=[A-Z])([A-Z][a-z])", r"_\1", s).lower()


def esc(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def ch_type(udt, dtype, nullable):
    if dtype == "USER-DEFINED":
        return "LowCardinality(Nullable(String))" if nullable else "LowCardinality(String)"
    if udt in ("int2", "int4"):
        t = "Int32"
    elif udt == "int8":
        t = "Int64"
    elif udt == "numeric":
        t = "Decimal(18, 4)"
    elif udt == "bool":
        t = "Bool"
    elif udt in ("timestamp", "timestamptz"):
        t = "DateTime64(3, 'UTC')"
    elif udt == "float8":
        t = "Float64"
    else:
        t = "String"
    return f"Nullable({t})" if nullable else t


def norm(rule, v):
    if v is None:
        return None
    if rule == "digits":
        return re.sub(r"\D", "", v)
    if rule == "lower_trim":
        return v.strip().lower()
    if rule == "upper_trim":
        return v.strip().upper()
    if rule == "trim":
        return re.sub(r"\s+", " ", v).strip()
    return v


def col_comment(tbl, col, udt, dtype, enums):
    texto = COL_DESC.get(f"{tbl}.{col}") or GENERIC_COL.get(col) or ""
    rule = NORMALIZE.get((tbl, col))
    partes = [texto] if texto else []
    if rule:
        partes.append(f"normalizado na trusted ({rule})")
    if dtype == "USER-DEFINED":
        partes.append(f"valores: {enums.get(udt, '')}")
    partes.append(f"origem: {tbl}.{col}")
    return " | ".join(partes)


def get_clients(bancos=None):
    """`bancos`: lista de bancos de staging (um por tenant, restaurados do backup do portal). Sem ela, le o banco da conexao."""
    import clickhouse_connect
    import psycopg2
    from airflow.hooks.base import BaseHook

    c = BaseHook.get_connection("credhub_postgres")
    pgs = [psycopg2.connect(host=c.host, port=c.port or 5432, dbname=b, user=c.login, password=c.password, connect_timeout=15)
           for b in (bancos or [c.schema])]
    ch = clickhouse_connect.get_client(host=os.environ["CLICKHOUSE_HOST"], password=os.environ["CLICKHOUSE_PASSWORD"])
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    return pgs, ch, s3


def run_pipeline(data=None, bancos=None):
    data = data or pendulum.now("America/Sao_Paulo").to_date_string()
    tag = f"snapshot_{data}"
    pgs, ch, s3 = get_clients(bancos)
    cur = pgs[0].cursor()
    ch.command(f"CREATE DATABASE IF NOT EXISTS {TRUSTED_DB}")
    ch.command(f"""CREATE TABLE IF NOT EXISTS {TRUSTED_DB}._pipeline_control (
        table_name String, ultima_carga DateTime DEFAULT now(), linhas UInt64, origem_carga String
    ) ENGINE = ReplacingMergeTree(ultima_carga) ORDER BY table_name COMMENT 'controle da ingestao do CredHub: ultima carga e contagem por tabela'""")
    cur.execute("select t.typname, string_agg(e.enumlabel, ',' order by e.enumsortorder) from pg_type t join pg_enum e on e.enumtypid=t.oid group by 1")
    enums = dict(cur.fetchall())
    cur.execute("select table_name, column_name, data_type, udt_name, is_nullable from information_schema.columns "
                "where table_schema='public' order by table_name, ordinal_position")
    schema = {}
    for t, c, d, u, n in cur.fetchall():
        schema.setdefault(t, []).append((c, d, u, n == "YES"))
    falhas = []
    for tbl, cols in schema.items():
        if tbl in SKIP_TABLES:
            continue
        try:
            cols = [c for c in cols if (tbl, c[0]) not in SKIP_COLS]
            tname = snake(tbl)
            sql_sel = "select " + ", ".join(f'"{c[0]}"' for c in cols) + f' from public."{tbl}"'
            rows = []
            for pg_i in pgs:  # um banco por tenant (os dois tenants da Capital Certo nao compartilham nenhum id: verificado)
                cur_i = pg_i.cursor()
                cur_i.execute(sql_sel)
                rows += cur_i.fetchall()
            # raw: copia fiel da origem, 1 arquivo por dia
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow([c[0] for c in cols])
            for r in rows:
                w.writerow(["" if v is None else (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for v in r])
            s3.put_object(Bucket=RAW_BUCKET, Key=f"credhub/{tname}/{data}.csv", Body=buf.getvalue().encode("utf-8"))
            # trusted: tabela nova + troca atomica
            defs = [f"`{snake(c)}` {ch_type(u, d, n)} COMMENT '{esc(col_comment(tbl, c, u, d, enums))}'" for c, d, u, n in cols]
            defs.append("`_origem_carga` LowCardinality(String) COMMENT 'snapshot que gerou a linha (snapshot_AAAA-MM-DD)'")
            defs.append("`_carregado_em` DateTime DEFAULT now() COMMENT 'quando a linha entrou no ClickHouse'")
            order = "id" if any(c[0] == "id" for c in cols) else snake(cols[0][0])
            desc = TABLE_DESC.get(tbl, f"Tabela {tbl} do CRM CredHub.") + f" Carga: snapshot completo diario. Origem: public.\"{tbl}\"."
            novo = f"{TRUSTED_DB}.`{tname}__novo`"
            ch.command(f"DROP TABLE IF EXISTS {novo}")
            ch.command(f"CREATE TABLE {novo} ({', '.join(defs)}) ENGINE = MergeTree ORDER BY `{order}` COMMENT '{esc(desc)}'")
            data_rows = []
            for r in rows:
                row = []
                for v, (c, d, u, n) in zip(r, cols):
                    if isinstance(v, (dict, list)):
                        v = json.dumps(v, ensure_ascii=False)
                    elif u == "uuid" and v is not None:
                        v = str(v)
                    rule = NORMALIZE.get((tbl, c))
                    if rule:
                        v = norm(rule, v)
                    row.append(v)
                row.append(tag)
                data_rows.append(row)
            for i in range(0, len(data_rows), 50000):
                ch.insert(novo, data_rows[i:i + 50000], column_names=[snake(c[0]) for c in cols] + ["_origem_carga"])
            got = ch.query(f"select count() from {novo}").result_rows[0][0]
            if got != len(rows):
                raise ValueError(f"{tbl}: origem {len(rows)} != destino {got}")
            existe = ch.query(f"select count() from system.tables where database='{TRUSTED_DB}' and name='{tname}'").result_rows[0][0]
            if existe:
                ch.command(f"EXCHANGE TABLES {TRUSTED_DB}.`{tname}` AND {novo}")
                ch.command(f"DROP TABLE {novo}")
            else:
                ch.command(f"RENAME TABLE {novo} TO {TRUSTED_DB}.`{tname}`")
            ch.insert(f"{TRUSTED_DB}._pipeline_control", [[tname, pendulum.now("UTC").naive(), got, tag]],
                      column_names=["table_name", "ultima_carga", "linhas", "origem_carga"])
            print(f"{tbl}: {got} linhas", flush=True)
        except Exception as e:  # uma tabela com problema nao derruba as outras
            falhas.append((tbl, str(e)[:300]))
            print("FALHA", tbl, e, flush=True)
    if bancos:
        # o export oficial do portal nao traz webhooks: as tabelas ficam como estavam (congeladas) e o catalogo avisa
        for t in ("efi_webhook_event", "pluggy_webhook_event", "webhook_event"):
            try:
                ch.command(f"ALTER TABLE {TRUSTED_DB}.`{t}` MODIFY COMMENT 'DESCONTINUADA em 2026-11-09: o export oficial do portal CredHub nao traz mais eventos de webhook; dados congelados no dump de 2026-10-01.'")
            except Exception as e:  # noqa
                print("aviso ao marcar tabela descontinuada", t, str(e)[:120])
    if falhas:
        raise RuntimeError(f"tabelas com falha: {falhas}")
