# Baixa os anexos do Chatwoot do cliente (so GET, pela API) para o MinIO (raw/chatwoot_anexos) e para o disco local de trabalho,
# conferindo o MD5 do Active Storage, e registra o catalogo em trusted_chatwoot.anexo_arquivo.
# Escopo desta rodada: midia ENVIADA PELO CLIENTE (imagem, audio, video, arquivo) nas conversas ligadas aos contratos, ate 1 dia depois do
# inicio do contrato. A mesma rotina vira a carga diaria e o backfill (spec_lake_nao_estruturado_e_ficha_credito.md).
# Uso: python midia_baixar.py [n_threads]
import base64
import hashlib
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

import credit_features as cf

DIR = "/tmp/midia"
MAX_BYTES = 250 * 1024 * 1024
DDL = """
CREATE TABLE IF NOT EXISTS trusted_chatwoot.anexo_arquivo (
    attachment_id UInt64 COMMENT 'attachments.id no Chatwoot',
    blob_id UInt64 COMMENT 'active_storage_blobs.id',
    message_id UInt64 COMMENT 'mensagem que trouxe o anexo',
    conversation_id UInt64 COMMENT 'conversa',
    remetente LowCardinality(String) COMMENT 'cliente ou empresa',
    file_type UInt8 COMMENT 'enum do Chatwoot: 0 imagem, 1 audio, 2 video, 3 arquivo',
    filename String COMMENT 'nome original do arquivo',
    content_type LowCardinality(String) COMMENT 'tipo MIME',
    byte_size UInt64 COMMENT 'tamanho em bytes segundo o Active Storage',
    md5_origem String COMMENT 'MD5 em base64 gravado pelo Active Storage (checksum)',
    md5_baixado String COMMENT 'MD5 em base64 do arquivo baixado',
    minio_key String COMMENT 'caminho no bucket raw do MinIO',
    service_name_origem LowCardinality(String) COMMENT 'local (disco do servidor do cliente) ou s3_compatible',
    status LowCardinality(String) COMMENT 'ok, erro ou grande_demais',
    erro String COMMENT 'motivo quando status = erro',
    criado_em DateTime64(3, 'UTC') COMMENT 'quando o anexo foi criado no Chatwoot',
    baixado_em DateTime64(3, 'UTC') COMMENT 'quando entrou no lake'
) ENGINE = ReplacingMergeTree(baixado_em) ORDER BY attachment_id
COMMENT 'Catalogo dos anexos do Chatwoot baixados para o lake. Um registro por anexo. Arquivo em raw/<minio_key>.'
"""


def credenciais():
    from airflow.hooks.base import BaseHook

    c = BaseHook.get_connection("chatwoot_api")
    return f"https://{c.host}", c.password


def lista_alvo(ch):
    """Anexos de cliente (imagem, audio, video, arquivo) das conversas dos contratos, criados ate 1 dia depois do inicio."""
    df = ch.query_df("""
        with c as (select distinct fc.contrato_id, p.contato_id, fc.data_inicio
                   from analytics.fato_contrato fc join analytics.ponte_lead_cliente p on p.cliente_id = fc.cliente_id
                   where p.vinculo_valido = 1 and fc.empresa = 'Capital Certo'),
             cv as (select c.contrato_id, c.data_inicio, v.id as conversa_id from c join trusted_chatwoot.conversations v on v.contact_id = c.contato_id)
        select distinct a.id as attachment_id, sa.blob_id as blob_id, a.message_id as message_id, m.conversation_id as conversa_id, m.message_type as message_type,
               a.file_type as file_type, a.created_at as criado_em, b.filename as filename, b.content_type as content_type, b.byte_size as byte_size,
               b.checksum as checksum, b.service_name as service_name
        from cv
        join trusted_chatwoot.messages m on m.conversation_id = cv.conversa_id
        join trusted_chatwoot.attachments a on a.message_id = m.id
        join trusted_chatwoot.active_storage_attachments sa on sa.record_id = a.id and sa.record_type = 'Attachment'
        join trusted_chatwoot.active_storage_blobs b on b.id = sa.blob_id
        where m.message_type = 0 and a.file_type in (0, 1, 2, 3) and m.created_at <= toDateTime(cv.data_inicio) + 86400
    """)
    df.columns = [c.split(".")[-1] for c in df.columns]  # o ClickHouse prefixa com o alias quando o nome e ambiguo
    return df


def _seguro(nome):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nome.split(";")[0])[:80] or "arquivo"


def processa_conversa(args):
    conv, g, base, token, s3, ja = args
    H = {"api_access_token": token}
    linhas = []
    for tentativa in range(3):
        try:
            r = requests.get(f"{base}/api/v1/accounts/1/conversations/{int(conv)}/attachments", headers=H, timeout=60)
            r.raise_for_status()
            itens = r.json().get("payload", [])
            break
        except Exception as e:  # noqa
            itens = None
            erro_lista = str(e)[:200]
            time.sleep(2 * (tentativa + 1))
    for row in g.itertuples():
        if int(row.attachment_id) in ja:
            continue
        reg = {"attachment_id": int(row.attachment_id), "blob_id": int(row.blob_id), "message_id": int(row.message_id), "conversation_id": int(conv),
               "remetente": "cliente" if row.message_type == 0 else "empresa", "file_type": int(row.file_type), "filename": row.filename[:200],
               "content_type": row.content_type, "byte_size": int(row.byte_size), "md5_origem": row.checksum, "md5_baixado": "", "minio_key": "",
               "service_name_origem": row.service_name, "status": "erro", "erro": "", "criado_em": row.criado_em, "baixado_em": pd.Timestamp.now(tz="UTC")}
        if itens is None:
            reg["erro"] = "lista da conversa falhou: " + erro_lista
            linhas.append(reg)
            continue
        cand = [i for i in itens if i.get("message_id") == int(row.message_id) and i.get("file_size") == int(row.byte_size)]
        if not cand:
            reg["erro"] = "anexo nao encontrado na API (message_id + tamanho)"
            linhas.append(reg)
            continue
        if int(row.byte_size) > MAX_BYTES:
            reg["status"], reg["erro"] = "grande_demais", f"{row.byte_size} bytes"
            linhas.append(reg)
            continue
        local = f"{DIR}/{int(row.attachment_id)}__{_seguro(row.filename)}"
        try:
            md5 = hashlib.md5()
            with requests.get(cand[0]["data_url"], timeout=300, stream=True) as rr:
                rr.raise_for_status()
                with open(local, "wb") as f:
                    for bloco in rr.iter_content(1 << 20):
                        f.write(bloco)
                        md5.update(bloco)
            reg["md5_baixado"] = base64.b64encode(md5.digest()).decode()
            if reg["md5_baixado"] != row.checksum:
                reg["erro"] = "MD5 diferente do Active Storage"
                os.remove(local)
            else:
                ts = pd.Timestamp(row.criado_em)
                reg["minio_key"] = f"chatwoot_anexos/{ts.year}/{ts.month:02d}/{int(row.attachment_id)}__{_seguro(row.filename)}"
                s3.upload_file(local, "raw", reg["minio_key"])
                reg["status"] = "ok"
        except Exception as e:  # noqa
            reg["erro"] = str(e)[:200]
        linhas.append(reg)
    return linhas


def lista_recentes(ch, dias=3):
    """Anexos de cliente (imagem, audio, video, arquivo) criados nos ultimos `dias` em QUALQUER conversa (carga diaria)."""
    df = ch.query_df(f"""
        select distinct a.id as attachment_id, sa.blob_id as blob_id, a.message_id as message_id, m.conversation_id as conversa_id, m.message_type as message_type,
               a.file_type as file_type, a.created_at as criado_em, b.filename as filename, b.content_type as content_type, b.byte_size as byte_size,
               b.checksum as checksum, b.service_name as service_name
        from trusted_chatwoot.attachments a
        join trusted_chatwoot.messages m on m.id = a.message_id
        join trusted_chatwoot.active_storage_attachments sa on sa.record_id = a.id and sa.record_type = 'Attachment'
        join trusted_chatwoot.active_storage_blobs b on b.id = sa.blob_id
        where m.message_type = 0 and a.file_type in (0, 1, 2, 3) and a.created_at >= now() - interval {int(dias)} day
    """)
    df.columns = [c.split(".")[-1] for c in df.columns]
    return df


def rodar(n_threads=4, escopo="contratos", dias=3):
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    ch = cf.conecta()
    ch.command(DDL)
    os.makedirs(DIR, exist_ok=True)
    base, token = credenciais()
    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    alvo = lista_alvo(ch) if escopo == "contratos" else lista_recentes(ch, dias)
    dj = ch.query_df("select attachment_id from trusted_chatwoot.anexo_arquivo final where status in ('ok', 'grande_demais')")
    ja = set(dj["attachment_id"].tolist()) if "attachment_id" in dj.columns else set()
    alvo = alvo[~alvo["attachment_id"].isin(ja)]
    print(f"a baixar: {len(alvo)} anexos em {alvo.conversa_id.nunique()} conversas ({alvo.groupby('file_type').size().to_dict()})", flush=True)
    grupos = [(c, g, base, token, s3, ja) for c, g in alvo.groupby("conversa_id")]
    lock = threading.Lock()
    acumulado, feito, t0 = [], 0, time.time()

    def grava(force=False):
        nonlocal acumulado
        if acumulado and (force or len(acumulado) >= 100):
            ch.insert_df("trusted_chatwoot.anexo_arquivo", pd.DataFrame(acumulado))
            acumulado = []

    with ThreadPoolExecutor(n_threads) as ex:
        for linhas in ex.map(processa_conversa, grupos):
            with lock:
                acumulado += linhas
                feito += 1
                grava()
                if feito % 50 == 0:
                    print(f"{feito}/{len(grupos)} conversas | {time.time() - t0:.0f}s", flush=True)
    grava(force=True)
    res = ch.query_df("select status, file_type, count() n from trusted_chatwoot.anexo_arquivo final group by 1,2 order by 1,2")
    print(res.to_string(), flush=True)


if __name__ == "__main__":
    rodar(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
