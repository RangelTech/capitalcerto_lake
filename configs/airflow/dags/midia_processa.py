# Processamento dos anexos do Chatwoot ja baixados (ver midia_baixar.py), sem modelo de linguagem generativo:
#   imagem -> trusted_chatwoot.anexo_imagem   (nitidez, brilho, dHash, embedding CLIP de 512 dim e probabilidades zero-shot)
#   pdf    -> trusted_chatwoot.anexo_pdf      (texto, tipo de documento, emissor, periodo, totais de extrato/vendas)
#   audio  -> trusted_chatwoot.anexo_audio    (transcricao Whisper em CPU, ritmo, hesitacao, valores citados)
#   video  -> trusted_chatwoot.anexo_video    (6 quadros guardados no MinIO, embedding CLIP medio, movimento)
# Uso: python midia_processa.py imagem|pdf|audio|video [limite]
# Cada passada processa so o que esta no catalogo (status ok) e ainda nao tem linha na tabela do tipo: pode ser repetida a vontade.
import json
import os
import re
import sys
import time

import numpy as np
import pandas as pd

import credit_features as cf

DIR = "/tmp/midia"
VERSAO = "midia-2026-11-09"
LABELS = {
    "fachada_comercio": "a photo of the front of a small business storefront",
    "casa_residencial": "a photo of a residential house",
    "interior_loja": "a photo of the inside of a shop",
    "documento": "a photo of a printed document or paper",
    "maquininha": "a photo of a credit card payment terminal",
    "rua": "a photo of a street",
    "captura_tela": "a screenshot of a phone app",
    "pessoa": "a photo of a person",
}
DDL = {
    "anexo_imagem": """CREATE TABLE IF NOT EXISTS trusted_chatwoot.anexo_imagem (
        attachment_id UInt64 COMMENT 'anexo (anexo_arquivo.attachment_id)', largura UInt16 COMMENT 'largura em pixels', altura UInt16 COMMENT 'altura em pixels',
        bytes UInt64 COMMENT 'tamanho do arquivo', nitidez Float32 COMMENT 'variancia do Laplaciano (maior = mais nitida)', brilho Float32 COMMENT 'brilho medio 0 a 255',
        dhash UInt64 COMMENT 'hash perceptual de diferenca de 64 bits: imagens iguais ou muito parecidas tem hash igual ou proximo',
        clip_embedding Array(Float32) COMMENT 'embedding CLIP ViT-B/32 de 512 dimensoes (normalizado)',
        p_fachada_comercio Float32 COMMENT 'probabilidade zero-shot: fachada de comercio', p_casa_residencial Float32 COMMENT 'probabilidade: casa residencial',
        p_interior_loja Float32 COMMENT 'probabilidade: interior de loja', p_documento Float32 COMMENT 'probabilidade: documento ou papel', p_maquininha Float32 COMMENT 'probabilidade: maquininha de cartao',
        p_rua Float32 COMMENT 'probabilidade: rua', p_captura_tela Float32 COMMENT 'probabilidade: captura de tela de aplicativo', p_pessoa Float32 COMMENT 'probabilidade: pessoa',
        versao LowCardinality(String) COMMENT 'versao do processamento', processado_em DateTime DEFAULT now() COMMENT 'quando foi processado'
    ) ENGINE = ReplacingMergeTree(processado_em) ORDER BY attachment_id
    COMMENT 'Features de imagem enviada pelo cliente: qualidade, hash perceptual para achar reuso, embedding CLIP e classificacao zero-shot (fachada, residencia, captura de tela...). Sem LLM.'""",
    "anexo_pdf": """CREATE TABLE IF NOT EXISTS trusted_chatwoot.anexo_pdf (
        attachment_id UInt64 COMMENT 'anexo', n_paginas UInt16 COMMENT 'paginas do PDF', chars_texto UInt32 COMMENT 'caracteres de texto extraidos das 6 primeiras paginas',
        tem_texto UInt8 COMMENT '1 se o PDF tem camada de texto (0 = escaneado, so imagem)', tipo_documento LowCardinality(String) COMMENT 'extrato, relatorio_vendas, comprovante, cnpj, outro ou sem_texto',
        emissor LowCardinality(String) COMMENT 'banco ou maquininha identificado no texto', periodo_inicio Nullable(Date) COMMENT 'primeira data do periodo coberto', periodo_fim Nullable(Date) COMMENT 'ultima data do periodo coberto',
        total_entradas Nullable(Float64) COMMENT 'total de entradas do extrato em R$', total_saidas Nullable(Float64) COMMENT 'total de saidas do extrato em R$', total_vendido Nullable(Float64) COMMENT 'total vendido do relatorio de vendas em R$',
        n_vendas Nullable(UInt32) COMMENT 'numero de vendas', ticket_medio Nullable(Float64) COMMENT 'ticket medio em R$', tem_emprestimo UInt8 COMMENT '1 se o texto cita parcela de emprestimo',
        texto_inicio String COMMENT 'primeiros 2000 caracteres do texto', versao LowCardinality(String) COMMENT 'versao do processamento', processado_em DateTime DEFAULT now() COMMENT 'quando foi processado'
    ) ENGINE = ReplacingMergeTree(processado_em) ORDER BY attachment_id
    COMMENT 'Texto e campos extraidos por regra de PDFs enviados pelo cliente (extratos e relatorios de vendas de maquininha).'""",
    "anexo_audio": """CREATE TABLE IF NOT EXISTS trusted_chatwoot.anexo_audio (
        attachment_id UInt64 COMMENT 'anexo', duracao_s Float32 COMMENT 'duracao do audio em segundos', fala_s Float32 COMMENT 'segundos de fala segundo o VAD', n_palavras UInt32 COMMENT 'palavras transcritas',
        palavras_por_s Float32 COMMENT 'ritmo de fala (palavras por segundo de fala)', taxa_hesitacao Float32 COMMENT 'hesitacoes (ne, tipo, ahn, hum) por palavra', texto String COMMENT 'transcricao (Whisper, portugues)',
        valores_citados String COMMENT 'JSON com numeros e valores em R$ citados no audio', cita_faturamento UInt8 COMMENT '1 se cita faturamento, venda ou movimento com valor', idioma LowCardinality(String) COMMENT 'idioma detectado',
        modelo LowCardinality(String) COMMENT 'modelo Whisper', versao LowCardinality(String) COMMENT 'versao do processamento', processado_em DateTime DEFAULT now() COMMENT 'quando foi processado'
    ) ENGINE = ReplacingMergeTree(processado_em) ORDER BY attachment_id
    COMMENT 'Transcricao e features de audio do cliente (Whisper em CPU).'""",
    "anexo_video": """CREATE TABLE IF NOT EXISTS trusted_chatwoot.anexo_video (
        attachment_id UInt64 COMMENT 'anexo', duracao_s Float32 COMMENT 'duracao em segundos', largura UInt16 COMMENT 'largura', altura UInt16 COMMENT 'altura', n_quadros UInt8 COMMENT 'quadros extraidos',
        brilho_medio Float32 COMMENT 'brilho medio dos quadros', nitidez_media Float32 COMMENT 'nitidez media dos quadros', movimento Float32 COMMENT 'diferenca media entre quadros consecutivos (0 a 255)',
        clip_embedding Array(Float32) COMMENT 'embedding CLIP medio dos quadros (512 dim, normalizado)', p_fachada_comercio Float32 COMMENT 'probabilidade media: fachada de comercio',
        p_casa_residencial Float32 COMMENT 'probabilidade media: casa residencial', p_interior_loja Float32 COMMENT 'probabilidade media: interior de loja', p_rua Float32 COMMENT 'probabilidade media: rua',
        p_pessoa Float32 COMMENT 'probabilidade media: pessoa', quadros_minio String COMMENT 'prefixo dos quadros no bucket raw', versao LowCardinality(String) COMMENT 'versao', processado_em DateTime DEFAULT now() COMMENT 'quando foi processado'
    ) ENGINE = ReplacingMergeTree(processado_em) ORDER BY attachment_id
    COMMENT 'Features de video do cliente a partir de 6 quadros espacados (CLIP, brilho, nitidez, movimento).'""",
}
FILE_TYPE = {"imagem": 0, "audio": 1, "video": 2, "pdf": 3}


def pendentes(ch, tipo, limite=None):
    tab = f"anexo_{tipo}"
    ft = FILE_TYPE[tipo]
    extra = "and content_type = 'application/pdf'" if tipo == "pdf" else ""
    q = f"""select a.attachment_id, a.filename, a.minio_key, a.byte_size from trusted_chatwoot.anexo_arquivo a final
            where a.status = 'ok' and a.file_type = {ft} {extra}
              and a.attachment_id not in (select attachment_id from trusted_chatwoot.{tab} final) order by a.attachment_id"""
    df = ch.query_df(q)
    return df.head(limite) if limite else df


def caminho(row):
    seguro = re.sub(r"[^A-Za-z0-9._-]+", "_", row.filename.split(";")[0])[:80] or "arquivo"
    p = f"{DIR}/{int(row.attachment_id)}__{seguro}"
    if not os.path.exists(p):  # o disco local e efemero: o original esta no MinIO
        from airflow.providers.amazon.aws.hooks.s3 import S3Hook

        os.makedirs(DIR, exist_ok=True)
        S3Hook(aws_conn_id="minio_s3").get_conn().download_file("raw", row.minio_key, p)
    return p


def _clip():
    from fastembed import ImageEmbedding, TextEmbedding

    img = ImageEmbedding("Qdrant/clip-ViT-B-32-vision", threads=2)
    txt = TextEmbedding("Qdrant/clip-ViT-B-32-text")
    T = np.array(list(txt.embed(list(LABELS.values()))))
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    return img, T


def _probs(v, T):
    s = 100 * T @ v
    p = np.exp(s - s.max())
    return p / p.sum()


def _metricas_img(path):
    import cv2

    im = cv2.imread(path)
    if im is None:
        return None
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    d = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (d[:, 1:] > d[:, :-1]).flatten()
    h = 0
    for b in bits:
        h = (h << 1) | int(b)
    return {"altura": im.shape[0], "largura": im.shape[1], "nitidez": float(cv2.Laplacian(g, cv2.CV_64F).var()), "brilho": float(g.mean()), "dhash": int(h)}, g


def imagem(ch, limite=None):
    ch.command(DDL["anexo_imagem"])
    pend = pendentes(ch, "imagem", limite)
    print(f"imagens a processar: {len(pend)}", flush=True)
    emb, T = _clip()
    linhas, t0 = [], time.time()
    for i, row in enumerate(pend.itertuples(), 1):
        try:
            p = caminho(row)
            m = _metricas_img(p)
            if m is None:
                continue
            met, _ = m
            v = np.array(list(emb.embed([p])))[0]
            v = v / np.linalg.norm(v)
            pr = _probs(v, T)
            linhas.append({"attachment_id": int(row.attachment_id), "largura": min(met["largura"], 65535), "altura": min(met["altura"], 65535), "bytes": int(row.byte_size),
                           "nitidez": met["nitidez"], "brilho": met["brilho"], "dhash": met["dhash"], "clip_embedding": v.astype(np.float32).tolist(),
                           **{f"p_{k}": float(x) for k, x in zip(LABELS, pr)}, "versao": VERSAO})
        except Exception as e:  # noqa
            print("falha imagem", row.attachment_id, str(e)[:100], flush=True)
        if len(linhas) >= 100 or (i == len(pend) and linhas):
            ch.insert_df("trusted_chatwoot.anexo_imagem", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(pend)} imagens | {time.time() - t0:.0f}s", flush=True)
    if linhas:
        ch.insert_df("trusted_chatwoot.anexo_imagem", pd.DataFrame(linhas))


EMISSORES = {"InfinitePay": r"infinite ?pay|cloudwalk", "PagBank": r"pagbank|pagseguro|pag seguro", "MercadoPago": r"mercado ?pago", "Cielo": r"\bcielo\b", "Rede": r"\bredecard\b|userede|\brede\b",
             "Stone": r"\bstone\b", "Ton": r"\bton\b", "CNPay": r"cnpay", "Sicredi": r"sicredi", "Sicoob": r"sicoob", "Nubank": r"nubank|nu pagamentos", "Itau": r"ita[uú]", "Bradesco": r"bradesco",
             "BancoDoBrasil": r"banco do brasil", "Caixa": r"caixa econ", "Inter": r"banco inter", "C6": r"\bc6\b", "Santander": r"santander", "Getnet": r"getnet", "Sumup": r"sumup", "Cora": r"\bcora\b",
             "Asaas": r"asaas", "PicPay": r"picpay", "Safra": r"safra"}
R_VALOR = r"([+\-]?\s*R?\$?\s*[\d\.]*\d,\d{2})"


def _valor(txt, rotulo):
    m = re.search(rotulo + r"[^\d\-+R]{0,40}" + R_VALOR, txt, re.I)
    if not m:
        return None
    s = re.sub(r"[^\d,\-]", "", m.group(1)).replace(",", ".")
    try:
        return abs(float(s))
    except ValueError:
        return None


def _datas(txt):
    ds = re.findall(r"\b(\d{2})/(\d{2})/(20\d{2})\b", txt)
    out = []
    for d, m, a in ds:
        try:
            out.append(pd.Timestamp(int(a), int(m), int(d)).date())
        except Exception:  # noqa
            pass
    meses = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}
    for d, m, a in re.findall(r"\b(\d{1,2})\s+(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-z]*\.?,?\s+(20\d{2})", txt, re.I):
        try:
            out.append(pd.Timestamp(int(a), meses[m.lower()[:3]], int(d)).date())
        except Exception:  # noqa
            pass
    return (min(out), max(out)) if out else (None, None)


def classifica_pdf(t):
    tl = t.lower()
    em = next((k for k, pat in EMISSORES.items() if re.search(pat, tl)), "")
    if re.search(r"comprovante de inscri[cç][aã]o|cadastro nacional da pessoa jur", tl):
        tipo = "cnpj"
    elif re.search(r"extrato|movimenta[cç][oõ]es|saldo (final|inicial|do dia)|total de (entradas|sa[ií]das)", tl):
        tipo = "extrato"
    elif re.search(r"relat[oó]rio de vendas|total vendido|vendas realizadas|consolidado de vendas|recebimentos|hist[oó]rico de vendas|o que vendi|o que recebi", tl):
        tipo = "relatorio_vendas"
    elif re.search(r"comprovante", tl):
        tipo = "comprovante"
    else:
        tipo = "outro"
    return tipo, em


def pdf(ch, limite=None):
    from pypdf import PdfReader

    ch.command(DDL["anexo_pdf"])
    pend = pendentes(ch, "pdf", limite)
    print(f"pdfs a processar: {len(pend)}", flush=True)
    linhas, t0 = [], time.time()
    for i, row in enumerate(pend.itertuples(), 1):
        try:
            p = caminho(row)
            rd = PdfReader(p)
            n = len(rd.pages)
            texto = "\n".join((rd.pages[k].extract_text() or "") for k in range(min(6, n)))
            tem = len(texto.strip()) > 80
            tipo, em = classifica_pdf(texto) if tem else ("sem_texto", "")
            ini, fim = _datas(texto) if tem else (None, None)
            linhas.append({"attachment_id": int(row.attachment_id), "n_paginas": min(n, 65535), "chars_texto": len(texto), "tem_texto": int(tem), "tipo_documento": tipo, "emissor": em,
                           "periodo_inicio": ini, "periodo_fim": fim, "total_entradas": _valor(texto, r"total de entradas|entradas"), "total_saidas": _valor(texto, r"total de sa[ií]das|sa[ií]das"),
                           "total_vendido": _valor(texto, r"total vendido|total de vendas|valor total vendido|vendas brutas|valor bruto"), "n_vendas": None,
                           "ticket_medio": _valor(texto, r"ticket m[eé]dio"), "tem_emprestimo": int(bool(re.search(r"empr[eé]stimo|parcela do empr", texto, re.I))),
                           "texto_inicio": texto[:2000], "versao": VERSAO})
            m = re.search(r"total de vendas\D{0,20}(\d{1,6})\b", texto, re.I)
            if m:
                linhas[-1]["n_vendas"] = int(m.group(1))
        except Exception as e:  # noqa
            linhas.append({"attachment_id": int(row.attachment_id), "n_paginas": 0, "chars_texto": 0, "tem_texto": 0, "tipo_documento": "erro", "emissor": "", "periodo_inicio": None, "periodo_fim": None,
                           "total_entradas": None, "total_saidas": None, "total_vendido": None, "n_vendas": None, "ticket_medio": None, "tem_emprestimo": 0, "texto_inicio": str(e)[:200], "versao": VERSAO})
        if len(linhas) >= 100 or (i == len(pend) and linhas):
            ch.insert_df("trusted_chatwoot.anexo_pdf", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(pend)} pdfs | {time.time() - t0:.0f}s", flush=True)
    if linhas:
        ch.insert_df("trusted_chatwoot.anexo_pdf", pd.DataFrame(linhas))


HESITA = re.compile(r"\b(n[eé]|tipo|ahn?|hum+|ent[aã]o|a[ií]|assim)\b", re.I)


def audio(ch, limite=None, modelo="base"):
    from faster_whisper import WhisperModel

    ch.command(DDL["anexo_audio"])
    pend = pendentes(ch, "audio", limite)
    print(f"audios a processar: {len(pend)}", flush=True)
    wm = WhisperModel(modelo, device="cpu", compute_type="int8", cpu_threads=3)
    linhas, t0 = [], time.time()
    for i, row in enumerate(pend.itertuples(), 1):
        try:
            p = caminho(row)
            segs, info = wm.transcribe(p, language="pt", vad_filter=True, beam_size=1)
            segs = list(segs)
            texto = " ".join(s.text.strip() for s in segs).strip()
            fala = float(sum(s.end - s.start for s in segs))
            palavras = len(texto.split())
            vals = re.findall(r"R?\$?\s?\d+(?:[\.,]\d+)?\s*(?:mil|reais|real|k)?", texto, re.I)
            vals = [v.strip() for v in vals if re.search(r"\d", v)][:12]
            cita = int(bool(re.search(r"(fatur|vend|movimento|mil por m[eê]s|reais por m[eê]s)", texto, re.I)) and bool(vals))
            linhas.append({"attachment_id": int(row.attachment_id), "duracao_s": float(info.duration), "fala_s": fala, "n_palavras": palavras, "palavras_por_s": palavras / fala if fala > 0 else 0.0,
                           "taxa_hesitacao": len(HESITA.findall(texto)) / max(palavras, 1), "texto": texto[:4000], "valores_citados": json.dumps(vals, ensure_ascii=False), "cita_faturamento": cita,
                           "idioma": info.language, "modelo": modelo, "versao": VERSAO})
        except Exception as e:  # noqa
            print("falha audio", row.attachment_id, str(e)[:100], flush=True)
        if len(linhas) >= 20 or (i == len(pend) and linhas):
            ch.insert_df("trusted_chatwoot.anexo_audio", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(pend)} audios | {time.time() - t0:.0f}s", flush=True)
    if linhas:
        ch.insert_df("trusted_chatwoot.anexo_audio", pd.DataFrame(linhas))


def video(ch, limite=None):
    import av
    import cv2
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    ch.command(DDL["anexo_video"])
    pend = pendentes(ch, "video", limite)
    print(f"videos a processar: {len(pend)}", flush=True)
    emb, T = _clip()
    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    os.makedirs("/tmp/midia_quadros", exist_ok=True)
    linhas, t0 = [], time.time()
    for i, row in enumerate(pend.itertuples(), 1):
        try:
            p = caminho(row)
            quadros = []
            with av.open(p) as c:
                st = c.streams.video[0]
                dur = float(c.duration) / 1e6 if c.duration else 0.0
                w, h = st.codec_context.width, st.codec_context.height
                alvo = [max(dur, 1.0) * (k + 0.5) / 6 for k in range(6)]
                for ts in alvo:
                    c.seek(int(ts * 1e6))
                    for fr in c.decode(video=0):
                        quadros.append(fr.to_ndarray(format="bgr24"))
                        break
            if not quadros:
                continue
            prefixo = f"chatwoot_derivados/quadros/{int(row.attachment_id)}"
            caminhos = []
            for k, q in enumerate(quadros):
                f = f"/tmp/midia_quadros/{int(row.attachment_id)}_{k}.jpg"
                cv2.imwrite(f, q, [cv2.IMWRITE_JPEG_QUALITY, 85])
                s3.upload_file(f, "raw", f"{prefixo}/{k}.jpg")
                caminhos.append(f)
            E = np.array(list(emb.embed(caminhos)))
            E /= np.linalg.norm(E, axis=1, keepdims=True)
            v = E.mean(axis=0)
            v /= np.linalg.norm(v)
            P = np.array([_probs(e, T) for e in E]).mean(axis=0)
            gs = [cv2.cvtColor(q, cv2.COLOR_BGR2GRAY) for q in quadros]
            mov = float(np.mean([np.abs(cv2.resize(a, (64, 64)).astype(float) - cv2.resize(b, (64, 64)).astype(float)).mean() for a, b in zip(gs[:-1], gs[1:])])) if len(gs) > 1 else 0.0
            for f in caminhos:
                os.remove(f)
            ks = list(LABELS)
            linhas.append({"attachment_id": int(row.attachment_id), "duracao_s": dur, "largura": min(w, 65535), "altura": min(h, 65535), "n_quadros": len(quadros),
                           "brilho_medio": float(np.mean([g.mean() for g in gs])), "nitidez_media": float(np.mean([cv2.Laplacian(g, cv2.CV_64F).var() for g in gs])), "movimento": mov,
                           "clip_embedding": v.astype(np.float32).tolist(), "p_fachada_comercio": float(P[ks.index("fachada_comercio")]), "p_casa_residencial": float(P[ks.index("casa_residencial")]),
                           "p_interior_loja": float(P[ks.index("interior_loja")]), "p_rua": float(P[ks.index("rua")]), "p_pessoa": float(P[ks.index("pessoa")]), "quadros_minio": prefixo, "versao": VERSAO})
        except Exception as e:  # noqa
            print("falha video", row.attachment_id, str(e)[:100], flush=True)
        if len(linhas) >= 25 or (i == len(pend) and linhas):
            ch.insert_df("trusted_chatwoot.anexo_video", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(pend)} videos | {time.time() - t0:.0f}s", flush=True)
    if linhas:
        ch.insert_df("trusted_chatwoot.anexo_video", pd.DataFrame(linhas))


if __name__ == "__main__":
    tipo = sys.argv[1]
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    {"imagem": imagem, "pdf": pdf, "audio": audio, "video": video}[tipo](cf.conecta(), lim)
