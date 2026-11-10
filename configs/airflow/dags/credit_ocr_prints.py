# OCR dos prints de aplicativo de maquininha/banco enviados pelo cliente (imagens que o CLIP classificou como captura de tela). Extrai por regra: total vendido, numero de
# vendas, ticket medio, periodo e se parece print de vendas. Grava trusted_chatwoot.anexo_print_vendas. Uso: python credit_ocr_prints.py [limite]
import os
import re
import sys
import time

import pandas as pd

import credit_features as cf
import midia_processa as mp

DDL = """CREATE TABLE IF NOT EXISTS trusted_chatwoot.anexo_print_vendas (
 attachment_id UInt64 COMMENT 'anexo (imagem)', n_blocos UInt16 COMMENT 'blocos de texto lidos pelo OCR', e_print_de_vendas UInt8 COMMENT '1 se o texto lembra tela de vendas de maquininha/banco',
 total_vendido Nullable(Float64) COMMENT 'total vendido no periodo (R$)', n_vendas Nullable(UInt32) COMMENT 'numero de vendas', ticket_medio Nullable(Float64) COMMENT 'ticket medio (R$)',
 total_a_receber Nullable(Float64) COMMENT 'total a receber (R$)', texto String COMMENT 'primeiros 600 caracteres lidos', versao LowCardinality(String) COMMENT 'versao', processado_em DateTime DEFAULT now() COMMENT 'quando'
) ENGINE = ReplacingMergeTree(processado_em) ORDER BY attachment_id COMMENT 'Campos de faturamento lidos por OCR em prints de aplicativo de maquininha enviados pelo cliente.'"""
R = r"R\$?\s*([\d\.]+,\d{2})"


def num(txt, rotulo):
    m = re.search(rotulo + r"[^\d]{0,40}" + R, txt, re.I)
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def main():
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    ch = cf.conecta()
    ch.command(DDL)
    q = """select a.attachment_id as attachment_id, a.filename as filename, a.minio_key as minio_key, a.byte_size as byte_size
           from trusted_chatwoot.anexo_arquivo a final join trusted_chatwoot.anexo_imagem i final on i.attachment_id = a.attachment_id
           where a.status = 'ok' and i.p_captura_tela > 0.5 and a.attachment_id not in (select attachment_id from trusted_chatwoot.anexo_print_vendas final)"""
    pend = ch.query_df(q)
    pend.columns = [c.split(".")[-1] for c in pend.columns]
    if lim:
        pend = pend.head(lim)
    print(f"prints a ler: {len(pend)}", flush=True)
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    linhas, t0 = [], time.time()
    for i, r in enumerate(pend.itertuples(), 1):
        try:
            p = mp.caminho(r)
            res, _ = ocr(p)
            blocos = [x[1] for x in (res or [])]
            txt = " ".join(blocos)
            tl = txt.lower()
            e = int(bool(re.search(r"total vendido|minhas vendas|total de vendas|vendas por forma|ticket m[eé]dio|resumo", tl)))
            nv = re.search(r"total de vendas\D{0,10}(\d{1,6})", tl)
            linhas.append({"attachment_id": int(r.attachment_id), "n_blocos": len(blocos), "e_print_de_vendas": e, "total_vendido": num(txt, r"total vendido"), "n_vendas": int(nv.group(1)) if nv else None,
                           "ticket_medio": num(txt, r"ticket m[eé]dio"), "total_a_receber": num(txt, r"total a receber"), "texto": txt[:600], "versao": "ocr-2026-11-10"})
        except Exception as ex:  # noqa
            print("falha", r.attachment_id, str(ex)[:80], flush=True)
        if len(linhas) >= 25 or (i == len(pend) and linhas):
            ch.insert_df("trusted_chatwoot.anexo_print_vendas", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(pend)} | {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
