# Etapas do credhub_diario que trazem o backup do portal para dentro da VPS:
#   esperar_backup -> baixar_e_restaurar (dump por tenant num banco de staging do credhub-postgres) -> validar_manifest
# O snapshot da trusted (credhub_ingest.run_pipeline) le os bancos de staging e junta os tenants.
import gzip
import json
import os
import subprocess
import zipfile

import pendulum

import credhub_portal as portal

TMP = "/tmp/credhub_backup"
LANDING_BUCKET = "landing"


def dia_alvo(context):
    """Dia do backup: `data` no conf do disparo (para teste com backup antigo) ou hoje em Brasilia."""
    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    return conf.get("data") or pendulum.now("America/Sao_Paulo").to_date_string()


def _pg():
    from airflow.hooks.base import BaseHook

    c = BaseHook.get_connection("credhub_postgres")
    return c.host, c.port or 5432, c.login, c.password


def banco_staging(nome_tenant):
    return "stg_" + portal.slug(nome_tenant)


def _backups_do_dia(s, dia):
    """[(tenant_id, nome, arquivos{formato: (nome, tamanho)}, stale)] para o dia pedido; levanta se algum tenant nao tem."""
    saida = []
    for t in portal.listar(s):
        d = next((x for x in t["days"] if x["date"] == dia), None)
        if not d:
            raise LookupError(f"{t['tenantName']}: sem backup do dia {dia} (ultimo: {t['days'][0]['date'] if t['days'] else '-'})")
        arq = {f["format"]: (f["file"], f["size"]) for f in d["files"]}
        if "zip" not in arq or "sql.gz" not in arq:
            raise LookupError(f"{t['tenantName']}: backup de {dia} incompleto ({list(arq)})")
        saida.append((t["tenantId"], t["tenantName"], arq, t.get("stale", False), d["generatedAt"]))
    return saida


def esperar_backup(**context):
    """Sensor: True quando todos os tenants ja tem o backup do dia. Falha so por timeout do sensor."""
    dia = dia_alvo(context)
    s = portal.login()
    try:
        b = _backups_do_dia(s, dia)
    except LookupError as e:
        print("ainda nao:", e)
        return False
    for _, nome, _, stale, gerado in b:
        print(f"{nome}: backup de {dia} pronto (gerado {gerado}, stale={stale})")
    return True


def baixar_e_restaurar(**context):
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook
    import psycopg2

    dia = dia_alvo(context)
    s = portal.login()
    backups = _backups_do_dia(s, dia)
    host, porta, usuario, senha = _pg()
    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    pasta = f"{TMP}/{dia}"
    os.makedirs(pasta, exist_ok=True)
    resumo = []
    for tenant_id, nome, arq, _, _ in backups:
        sl = portal.slug(nome)
        zip_p, dump_p = f"{pasta}/{sl}.zip", f"{pasta}/{sl}.sql.gz"
        portal.baixar(s, tenant_id, dia, "zip", zip_p, esperado=arq["zip"][1])
        portal.baixar(s, tenant_id, dia, "sql.gz", dump_p, esperado=arq["sql.gz"][1])
        # integridade dos arquivos baixados
        with gzip.open(dump_p, "rb") as f:
            while f.read(1 << 24):
                pass
        bad = zipfile.ZipFile(zip_p).testzip()
        if bad:
            raise RuntimeError(f"{nome}: zip corrompido em {bad}")
        # historico bruto no MinIO (landing)
        for p, fmt in ((zip_p, "zip"), (dump_p, "sql.gz")):
            s3.upload_file(p, LANDING_BUCKET, f"crm/credhub/{sl}/{dia}.{fmt}")
        # restaura num banco de staging proprio do tenant
        stg = banco_staging(nome)
        conn = psycopg2.connect(host=host, port=porta, dbname="postgres", user=usuario, password=senha, connect_timeout=15)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS "{stg}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{stg}"')
        conn.close()
        env = {**os.environ, "PGPASSWORD": senha}
        # `\restrict`/`\unrestrict` sao meta-comandos de seguranca do pg_dump novo; um psql mais velho os recusaria
        cmd = (f"gunzip -c '{dump_p}' | sed -e '/^\\\\restrict /d' -e '/^\\\\unrestrict /d' | "
               f"psql -q -v ON_ERROR_STOP=1 -h {host} -p {porta} -U {usuario} -d {stg}")
        r = subprocess.run(["bash", "-c", "set -o pipefail; " + cmd], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"restauracao de {nome} falhou: {r.stderr[-600:]}")
        resumo.append((nome, stg, os.path.getsize(dump_p)))
        print(f"{nome}: restaurado em {stg} ({os.path.getsize(dump_p) / 1e6:.1f} MB gz)", flush=True)
    context["ti"].xcom_push(key="staging", value=[x[1] for x in resumo])
    return [x[1] for x in resumo]


def validar_manifest(**context):
    """Contagem de cada tabela do staging tem de ser igual a do manifest.json do .zip do mesmo dia."""
    import psycopg2

    dia = dia_alvo(context)
    host, porta, usuario, senha = _pg()
    pasta = f"{TMP}/{dia}"
    erros, avisos = [], []
    for zip_p in sorted(p for p in os.listdir(pasta) if p.endswith(".zip")):
        sl = zip_p[:-4]
        manifest = json.loads(zipfile.ZipFile(f"{pasta}/{zip_p}").read("manifest.json"))
        conn = psycopg2.connect(host=host, port=porta, dbname=f"stg_{sl}", user=usuario, password=senha, connect_timeout=15)
        cur = conn.cursor()
        for t in manifest["tables"]:
            cur.execute(f'select count(*) from public."{t["table"]}"')
            n = cur.fetchone()[0]
            if n != t["rows"]:
                erros.append(f"{sl}.{t['table']}: banco {n} != manifest {t['rows']}")
        conn.close()
        print(f"{sl}: {len(manifest['tables'])} tabelas conferidas, snapshot {manifest['snapshotAt']}, migracao {manifest['lastMigration']}")
    if erros:
        raise RuntimeError("contagem diferente do manifest: " + "; ".join(erros[:10]))
    return "ok"


def limpar_staging(**context):
    import psycopg2

    dia = dia_alvo(context)
    host, porta, usuario, senha = _pg()
    pasta = f"{TMP}/{dia}"
    conn = psycopg2.connect(host=host, port=porta, dbname="postgres", user=usuario, password=senha, connect_timeout=15)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("select datname from pg_database where datname like 'stg\\_%'")
    for (db,) in cur.fetchall():
        cur.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        print("staging removido:", db)
    conn.close()
    if os.path.isdir(pasta):
        for f in os.listdir(pasta):
            os.remove(f"{pasta}/{f}")
        os.rmdir(pasta)
