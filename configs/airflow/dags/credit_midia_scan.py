# Agrega as features de midia (imagem CLIP, PDF, audio, video) POR CONTRATO (so anexos do cliente ate 1 dia depois do inicio) e testa a correlacao
# de cada uma com o calote (varrimento univariado com FDR) e com um modelo parcimonioso fora do tempo. Sem LLM.
import json
import re
import warnings

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

import credit_features as cf
import credit_scan as sc

warnings.filterwarnings("ignore")
ALVOS = ("ye_0", "y_calote_15", "y_calote_30", "y_calote_45")


def _valor_audio(js):
    """Maior valor em R$ citado ('25 mil' -> 25000, '3.500' -> 3500)."""
    try:
        vs = json.loads(js)
    except Exception:  # noqa
        return np.nan
    melhor = np.nan
    for v in vs:
        m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(mil|k)?", v.lower())
        if not m:
            continue
        x = float(m.group(1).replace(".", "").replace(",", ".")) if not re.search(r"\d,\d{1,2}$", m.group(1)) else float(m.group(1).replace(".", "").replace(",", "."))
        if m.group(2):
            x *= 1000
        if x >= 100:
            melhor = x if melhor != melhor else max(melhor, x)
    return melhor


def agrega(ch, base):
    c = ch.query_df("""select distinct fc.contrato_id as contrato_id, v.id as conversa_id, fc.data_inicio as data_inicio
                       from analytics.fato_contrato fc join analytics.ponte_lead_cliente p on p.cliente_id = fc.cliente_id
                       join trusted_chatwoot.conversations v on v.contact_id = p.contato_id
                       where p.vinculo_valido = 1 and fc.empresa = 'Capital Certo'""")
    c["cutoff"] = pd.to_datetime(c["data_inicio"]) + pd.Timedelta(days=1)
    cat = ch.query_df("select attachment_id, conversation_id as conversa_id, file_type, status, criado_em from trusted_chatwoot.anexo_arquivo final")
    cat["criado_em"] = pd.to_datetime(cat["criado_em"]).dt.tz_localize(None)
    m = c.merge(cat, on="conversa_id")
    m = m[m["criado_em"] <= m["cutoff"]]
    out = pd.DataFrame({"contrato_id": base["contrato_id"].unique()})
    # contagens a partir do catalogo (inclui os que deram 404: o anexo existiu)
    for ft, nome in ((0, "imagens"), (1, "audios"), (2, "videos"), (3, "arquivos")):
        n = m[m["file_type"] == ft].groupby("contrato_id")["attachment_id"].nunique().rename(f"m_n_{nome}")
        out = out.merge(n, on="contrato_id", how="left")
    out["m_n_anexos"] = out[[c_ for c_ in out.columns if c_.startswith("m_n_")]].sum(axis=1, min_count=1)
    out["m_tem_midia"] = (out["m_n_anexos"].fillna(0) > 0).astype(float)
    ids = m[m["status"] == "ok"][["contrato_id", "attachment_id", "file_type"]].drop_duplicates()
    # imagem
    im = ch.query_df("select * from trusted_chatwoot.anexo_imagem final")
    im = ids[ids["file_type"] == 0].merge(im, on="attachment_id")
    if len(im):
        pc = [x for x in im.columns if x.startswith("p_")]
        g = im.groupby("contrato_id")
        a = pd.concat([g[pc].mean().add_prefix("m_img_med_"), g["p_fachada_comercio"].max().rename("m_img_max_fachada"), g["p_captura_tela"].max().rename("m_img_max_captura"),
                       g["nitidez"].mean().rename("m_img_nitidez"), g["brilho"].mean().rename("m_img_brilho"), g.size().rename("m_img_n_proc")], axis=1).reset_index()
        a["m_img_tem_fachada"] = (a["m_img_max_fachada"] > 0.5).astype(float)
        a["m_img_tem_captura"] = (a["m_img_max_captura"] > 0.5).astype(float)
        out = out.merge(a, on="contrato_id", how="left")
        # reuso: mesmo dHash (distancia de Hamming <= 3) em contratos de OUTROS clientes
        cli = base[["contrato_id", "cliente_id"]].drop_duplicates()
        h = im[["contrato_id", "attachment_id", "dhash"]].merge(cli, on="contrato_id")
        hv = h["dhash"].astype("uint64").values
        cl = h["cliente_id"].values
        reuso = np.zeros(len(h))
        for i in range(len(h)):
            x = np.bitwise_xor(hv, hv[i])
            dist = np.array([bin(int(v)).count("1") for v in x]) if len(h) < 4000 else None
            reuso[i] = int(((dist <= 3) & (cl != cl[i])).sum())
        h["reuso"] = reuso
        out = out.merge(h.groupby("contrato_id")["reuso"].max().rename("m_img_reuso_outros_clientes").reset_index(), on="contrato_id", how="left")
        out["m_img_reusada"] = (out["m_img_reuso_outros_clientes"] > 0).astype(float).where(out["m_img_reuso_outros_clientes"].notna())
        # embedding medio -> componentes principais (sem rotulo, ajuste nao supervisionado)
        E = np.vstack(im["clip_embedding"].values)
        emb = pd.DataFrame(E, index=im["contrato_id"].values).groupby(level=0).mean()
        k = min(8, emb.shape[0] - 1, emb.shape[1])
        pcs = PCA(n_components=k, random_state=7).fit_transform(emb.values)
        pdf_pcs = pd.DataFrame(pcs, columns=[f"m_img_pc{i + 1}" for i in range(k)])
        pdf_pcs["contrato_id"] = emb.index
        out = out.merge(pdf_pcs, on="contrato_id", how="left")
    # pdf
    pf = ch.query_df("select * from trusted_chatwoot.anexo_pdf final")
    pf = ids[ids["file_type"] == 3].merge(pf, on="attachment_id")
    if len(pf):
        g = pf.groupby("contrato_id")
        a = pd.DataFrame({"m_pdf_n": g.size(), "m_pdf_n_extrato": g.apply(lambda x: int((x["tipo_documento"] == "extrato").sum())),
                          "m_pdf_n_vendas": g.apply(lambda x: int((x["tipo_documento"] == "relatorio_vendas").sum())), "m_pdf_sem_texto": g.apply(lambda x: float((x["tem_texto"] == 0).mean())),
                          "m_pdf_entradas_max": g["total_entradas"].max(), "m_pdf_saidas_max": g["total_saidas"].max(), "m_pdf_vendido_max": g["total_vendido"].max(),
                          "m_pdf_tem_emprestimo": g["tem_emprestimo"].max().astype(float), "m_pdf_paginas_med": g["n_paginas"].mean()}).reset_index()
        a["m_pdf_tem_extrato"] = (a["m_pdf_n_extrato"] > 0).astype(float)
        a["m_pdf_tem_relatorio_vendas"] = (a["m_pdf_n_vendas"] > 0).astype(float)
        a["m_pdf_entradas_log"] = np.log1p(a["m_pdf_entradas_max"])
        top = pf.groupby("contrato_id")["emissor"].agg(lambda s: s[s != ""].mode().iloc[0] if (s != "").any() else "").rename("m_pdf_emissor").reset_index()
        out = out.merge(a, on="contrato_id", how="left").merge(top, on="contrato_id", how="left")
    # audio
    au = ch.query_df("select * from trusted_chatwoot.anexo_audio final")
    au = ids[ids["file_type"] == 1].merge(au, on="attachment_id")
    if len(au):
        au["valor_citado"] = au["valores_citados"].map(_valor_audio)
        g = au.groupby("contrato_id")
        a = pd.DataFrame({"m_aud_n": g.size(), "m_aud_duracao_med": g["duracao_s"].mean(), "m_aud_palavras_por_s": g["palavras_por_s"].mean(), "m_aud_hesitacao": g["taxa_hesitacao"].mean(),
                          "m_aud_palavras_total": g["n_palavras"].sum(), "m_aud_cita_faturamento": g["cita_faturamento"].max().astype(float), "m_aud_valor_max": g["valor_citado"].max()}).reset_index()
        out = out.merge(a, on="contrato_id", how="left")
    # video
    vd = ch.query_df("select * from trusted_chatwoot.anexo_video final")
    vd = ids[ids["file_type"] == 2].merge(vd, on="attachment_id")
    if len(vd):
        g = vd.groupby("contrato_id")
        a = pd.DataFrame({"m_vid_n": g.size(), "m_vid_duracao": g["duracao_s"].mean(), "m_vid_movimento": g["movimento"].mean(), "m_vid_brilho": g["brilho_medio"].mean(),
                          "m_vid_p_fachada": g["p_fachada_comercio"].mean(), "m_vid_p_pessoa": g["p_pessoa"].mean(), "m_vid_p_rua": g["p_rua"].mean()}).reset_index()
        out = out.merge(a, on="contrato_id", how="left")
    return out


def main():
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan2.pkl")
    mid = agrega(ch, df)
    df = df.merge(mid, on="contrato_id", how="left")
    df.to_pickle("/tmp/base_scan3.pkl")
    mcols = [c for c in df.columns if c.startswith("m_")]
    print(f"{len(df)} contratos | {len(mcols)} variaveis de midia | com midia: {int(df['m_tem_midia'].fillna(0).sum())}", flush=True)
    num = [c for c in mcols if pd.api.types.is_numeric_dtype(df[c])]
    cat = [c for c in mcols if c not in num and df[c].nunique() <= 40]
    todos = []
    for a in ALVOS:
        r = sc.testes(df, a, num, cat)
        todos.append(r)
        sig = r[r["q_fdr"] < 0.10].sort_values("p")
        print(f"\n=== alvo {a}: {len(r)} testes de midia, n={int(df[a].notna().sum())}, {len(sig)} com q<0.10", flush=True)
        for x in sig.head(15).itertuples():
            print(f"  {x.variavel} [{x.nivel}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} antigo={x.auc_antigo:+.3f} novo={x.auc_novo:+.3f} estavel={x.estavel}", flush=True)
        print("  -- menores p:", [(x.variavel, x.nivel[:12], round(x.p, 4), x.n) for x in r.sort_values("p").head(6).itertuples()], flush=True)
    pd.concat(todos).to_pickle("/tmp/motor_scan_midia.pkl")


if __name__ == "__main__":
    main()
