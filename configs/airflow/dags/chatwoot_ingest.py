# Ingestao diaria do Chatwoot do cliente (raw + trusted). Ver spec completa em
# skills/capitalcerto/references/spec_ingestao_chatwoot.md no repo personal-skills.
#
# Um tunel SSH por execucao (nao um por tabela): mais gentil com o servidor do cliente,
# so uma conexao aberta por vez, tabelas processadas em sequencia dentro dela.

import io
import json
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg2
import sshtunnel
from airflow.hooks.base import BaseHook
from airflow.providers.amazon.aws.hooks.s3 import S3Hook

SAFETY_LAG = timedelta(minutes=15)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
RAW_BUCKET = "raw"

# name, version_col (None = full reload), exclude_cols (segredo/PII que nao deve ser replicado)
TABLES = [
    {"name": "accounts", "version_col": "updated_at"},
    {"name": "account_users", "version_col": "updated_at"},
    {"name": "active_storage_attachments", "version_col": "created_at"},
    {"name": "active_storage_blobs", "version_col": "created_at"},
    {"name": "attachments", "version_col": "updated_at"},
    {"name": "audits", "version_col": "created_at"},
    {"name": "automation_rules", "version_col": "updated_at"},
    {"name": "channel_whatsapp", "version_col": "updated_at"},
    {"name": "contact_inboxes", "version_col": "updated_at"},
    {"name": "contacts", "version_col": "updated_at"},
    {"name": "conversation_participants", "version_col": "updated_at"},
    {"name": "conversations", "version_col": "updated_at"},
    {"name": "custom_attribute_definitions", "version_col": "updated_at"},
    {"name": "custom_filters", "version_col": "updated_at"},
    {"name": "inbox_members", "version_col": "updated_at"},
    {"name": "inboxes", "version_col": "updated_at"},
    {"name": "installation_configs", "version_col": "updated_at"},
    {"name": "kanban_funnels", "version_col": "updated_at"},
    {"name": "kanban_items", "version_col": "updated_at"},
    {"name": "kanban_notes", "version_col": "updated_at"},
    {"name": "kanban_stage_transitions", "version_col": "updated_at"},
    {"name": "kanban_stages", "version_col": "updated_at"},
    {"name": "messages", "version_col": "updated_at"},
    {"name": "notes", "version_col": "updated_at"},
    {"name": "notifications", "version_col": "updated_at"},
    {"name": "reporting_events", "version_col": "updated_at"},
    {"name": "taggings", "version_col": "created_at"},
    {"name": "tags", "version_col": None},  # full reload, sem coluna de tempo
    {"name": "team_members", "version_col": "updated_at"},
    {"name": "teams", "version_col": "updated_at"},
    {
        "name": "users",
        "version_col": "updated_at",
        "exclude_cols": [
            "encrypted_password",
            "reset_password_token",
            "confirmation_token",
            "otp_secret",
            "otp_backup_codes",
            "tokens",
        ],
    },
    {"name": "working_hours", "version_col": "updated_at"},
]


def get_ch_client():
    import os

    import clickhouse_connect

    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database="trusted_chatwoot",
    )


def open_tunnel():
    conn = BaseHook.get_connection("chatwoot_ssh_tunnel")
    extra = conn.extra_dejson
    tunnel = sshtunnel.SSHTunnelForwarder(
        (conn.host, conn.port),
        ssh_username=conn.login,
        ssh_pkey=extra["key_file"],
        remote_bind_address=("127.0.0.1", 15432),
        local_bind_address=("127.0.0.1", 0),
    )
    tunnel.start()
    return tunnel


def get_pg_conn(tunnel):
    conn = BaseHook.get_connection("chatwoot_postgres")
    return psycopg2.connect(
        host="127.0.0.1",
        port=tunnel.local_bind_port,
        dbname=conn.schema,
        user=conn.login,
        password=conn.password,
        connect_timeout=15,
    )


def get_target_columns(ch_client, table_name):
    """Le o schema real ja criado no ClickHouse (fonte da verdade), nao reimplementa em Python."""
    rows = ch_client.query(
        "SELECT name, type FROM system.columns WHERE database='trusted_chatwoot' AND table=%(t)s ORDER BY position",
        parameters={"t": table_name},
    ).result_rows
    return rows  # list of (name, type)


def get_watermark(ch_client, table_name):
    result = ch_client.query(
        "SELECT last_watermark FROM trusted_chatwoot._pipeline_control FINAL WHERE table_name=%(t)s",
        parameters={"t": table_name},
    ).result_rows
    if result:
        return result[0][0]
    return EPOCH


def set_control(ch_client, table_name, watermark, started_at, finished_at, status, rows_extracted, rows_loaded):
    ch_client.insert(
        "trusted_chatwoot._pipeline_control",
        [[table_name, watermark, started_at, finished_at, status, rows_extracted, rows_loaded]],
        column_names=[
            "table_name",
            "last_watermark",
            "last_run_started_at",
            "last_run_finished_at",
            "last_run_status",
            "rows_extracted",
            "rows_loaded",
        ],
    )


def extract_table_csv(pg_conn, table_cfg, watermark_from, watermark_to):
    name = table_cfg["name"]
    exclude = table_cfg.get("exclude_cols", [])
    version_col = table_cfg["version_col"]

    cur = pg_conn.cursor()
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
        (name,),
    )
    all_cols = [r[0] for r in cur.fetchall() if r[0] not in exclude]
    col_list = ", ".join(f'"{c}"' for c in all_cols)

    if version_col is None:
        where_clause = ""
    else:
        where_clause = cur.mogrify(
            f' WHERE "{version_col}" > %s AND "{version_col}" <= %s',
            (watermark_from, watermark_to),
        ).decode()

    query = f'SELECT {col_list} FROM "{name}"{where_clause}'
    buf = io.StringIO()
    cur.copy_expert(f"COPY ({query}) TO STDOUT WITH (FORMAT csv, HEADER true)", buf)
    cur.close()
    buf.seek(0)
    return buf, all_cols


def coerce_dataframe(df: pd.DataFrame, target_cols) -> pd.DataFrame:
    target_map = dict(target_cols)
    for col in df.columns:
        ch_type = target_map.get(col, "")
        if "Bool" in ch_type:
            df[col] = df[col].map({"t": True, "f": False}).astype("boolean")
        elif "DateTime64" in ch_type:
            # format="mixed": sem isso o pandas as vezes trava um formato inferido dos
            # primeiros valores (com microssegundo) e falha silenciosamente (vira NaT) num
            # timestamp exatamente no segundo, sem fracao. Achado ao vivo em reporting_events
            # em 2026-09-30 (1 linha em 211 mil quebrava assim).
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True, format="mixed").dt.tz_localize(None)
        elif "Int32" in ch_type:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int32")
        elif "Int64" in ch_type or "UInt64" in ch_type:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif "Float64" in ch_type:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        # String/UUID/Nullable(String): mantem como veio do CSV (texto), NaN vira None no insert
    return df


def run_table(pg_conn, ch_client, s3_hook, table_cfg, run_started_at):
    name = table_cfg["name"]
    version_col = table_cfg["version_col"]
    watermark_from = get_watermark(ch_client, name) if version_col else None
    watermark_to = (run_started_at - SAFETY_LAG) if version_col else None

    if version_col and watermark_from >= watermark_to:
        print(f"[{name}] nada pendente (watermark {watermark_from} >= teto seguro {watermark_to})")
        return

    csv_buf, cols = extract_table_csv(pg_conn, table_cfg, watermark_from, watermark_to)
    csv_text = csv_buf.getvalue()

    ingested_at = run_started_at.strftime("%Y%m%dT%H%M%S")
    raw_key = f"chatwoot/{name}/ingested_at={ingested_at}/part-0000.csv"
    s3_hook.load_string(csv_text, key=raw_key, bucket_name=RAW_BUCKET, replace=True)

    df = pd.read_csv(io.StringIO(csv_text), dtype=str, keep_default_na=False, na_values=[""])
    rows_extracted = len(df)
    print(f"[{name}] extraidas {rows_extracted} linhas")

    if rows_extracted == 0:
        if version_col:
            set_control(ch_client, name, watermark_to, run_started_at, datetime.utcnow(), "ok_sem_dado", 0, 0)
        return

    target_cols = get_target_columns(ch_client, name)
    df = coerce_dataframe(df, target_cols)

    schema_drift = set(df.columns) - {c for c, _ in target_cols}
    if schema_drift:
        raise RuntimeError(f"[{name}] coluna nova na origem, nao existe na trusted ainda: {schema_drift}")

    if version_col is None:
        ch_client.command(f"TRUNCATE TABLE trusted_chatwoot.{name}")

    ch_client.insert_df(name, df)
    print(f"[{name}] carregadas {len(df)} linhas na trusted")

    if version_col:
        new_watermark = watermark_to
        set_control(ch_client, name, new_watermark, run_started_at, datetime.utcnow(), "ok", rows_extracted, len(df))


def run_pipeline():
    run_started_at = datetime.utcnow()
    ch_client = get_ch_client()
    s3_hook = S3Hook(aws_conn_id="minio_s3")
    if not s3_hook.check_for_bucket(RAW_BUCKET):
        s3_hook.create_bucket(RAW_BUCKET)

    tunnel = open_tunnel()
    try:
        pg_conn = get_pg_conn(tunnel)
        try:
            errors = {}
            for table_cfg in TABLES:
                try:
                    run_table(pg_conn, ch_client, s3_hook, table_cfg, run_started_at)
                except Exception as exc:  # nao deixa 1 tabela travar as outras
                    print(f"[{table_cfg['name']}] FALHOU: {exc}")
                    errors[table_cfg["name"]] = str(exc)
                time.sleep(1)  # respiro entre tabelas, nao emenda scan em cima de scan
            if errors:
                raise RuntimeError(f"tabelas com falha: {json.dumps(errors)}")
        finally:
            pg_conn.close()
    finally:
        tunnel.stop()


if __name__ == "__main__":
    run_pipeline()
