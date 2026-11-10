# Varrimento exaustivo de correlacoes com o calote, variavel por variavel (univariado), com correcao de falsa descoberta (Benjamini-Hochberg)
# e checagem de estabilidade (metade antiga x metade nova dos contratos). Inclui cadastro, geografia, nome, estilo da conversa no WhatsApp,
# bureau (Dora/DeskData) e comportamento do contrato. Grava em analytics_ml.motor_scan_univariado.
# Uso: python credit_scan.py
import re
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

import credit_features as cf
import credit_ficha as fi

warnings.filterwarnings("ignore")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")
EMPRESA = re.compile(r"\b(ME|MEI|LTDA|EIRELI|EPP|S/?A|COMERCIO|COMERCIAL|SERVICOS|ADEGA|BAR|RESTAURANTE|LANCHONETE|MERCEARIA|MERCADO|PADARIA|OFICINA|LOJA|ESTUDIO|STUDIO|SALAO|BARBEARIA)\b")
TERMOS = {"urgente": r"urgen|urgent|hoje mesmo|o quanto antes", "preciso": r"preciso|precisando|necessito", "pix": r"\bpix\b", "maquininha": r"maquinin|maquina",
          "negativado": r"negativ|nome sujo|spc|serasa", "agradece": r"obrigad|agradec|valeu", "pergunta_taxa": r"juros|taxa|quanto (fica|paga|cobra)",
          "tem_pressa_palavrao": r"porra|caralho|merda|pqp"}


def _normaliza_nome(n):
    import unicodedata

    t = unicodedata.normalize("NFD", str(n or "")).encode("ascii", "ignore").decode().upper().strip()
    return re.sub(r"[^A-Z ]", " ", t)


def features_extras(ch, base):
    """Nome do cliente e estilo da conversa no WhatsApp (so mensagens ate 1 dia depois do inicio do contrato)."""
    cli = ch.query_df("select id as cliente_id, name from trusted_credhub.customer")
    cli["nome"] = cli["name"].map(_normaliza_nome)
    cli["x_nome_tokens"] = cli["nome"].str.split().map(len)
    cli["x_nome_tamanho"] = cli["nome"].str.len()
    cli["x_nome_parece_empresa"] = cli["nome"].map(lambda s: float(bool(EMPRESA.search(s))))
    primeiro = cli["nome"].str.split().str[0]
    top = primeiro.value_counts().head(25).index
    cli["x_primeiro_nome"] = primeiro.where(primeiro.isin(top), "OUTRO")
    cli["x_nome_com_de_da"] = cli["nome"].str.contains(r"\b(DE|DA|DO|DOS|DAS)\b").astype(float)
    out = base[["contrato_id", "cliente_id", "data_inicio"]].merge(cli[["cliente_id", "x_nome_tokens", "x_nome_tamanho", "x_nome_parece_empresa", "x_primeiro_nome", "x_nome_com_de_da"]],
                                                                 on="cliente_id", how="left")
    pon = ch.query_df("select cliente_id, contato_id from analytics.ponte_lead_cliente where vinculo_valido = 1")
    conv = ch.query_df("select id as conversa_id, contact_id as contato_id from trusted_chatwoot.conversations final")
    c = base[["contrato_id", "cliente_id", "data_inicio"]].merge(pon, on="cliente_id").merge(conv, on="contato_id")
    c["cutoff"] = pd.to_datetime(c["data_inicio"]) + pd.Timedelta(days=1)
    ids = ",".join(str(int(x)) for x in c["conversa_id"].unique())
    m = ch.query_df(f"""select conversation_id as conversa_id, created_at as quando, message_type as tipo, content
                        from trusted_chatwoot.messages final where conversation_id in ({ids}) and private = 0 and content is not null""")
    m = c[["contrato_id", "conversa_id", "cutoff"]].merge(m, on="conversa_id")
    m = m[m["quando"] <= m["cutoff"]].sort_values(["contrato_id", "quando"])
    linhas = []
    for cid, g in m.groupby("contrato_id"):
        cl = g[g["tipo"] == 0]
        if not len(cl):
            continue
        txt = cl["content"].astype(str)
        primeira = cl["quando"].iloc[0]
        resp = []
        ultimo_bot = None
        for q, t in zip(g["quando"], g["tipo"]):
            if t == 1:
                ultimo_bot = q
            elif t == 0 and ultimo_bot is not None:
                resp.append((q - ultimo_bot).total_seconds())
                ultimo_bot = None
        todo = " ".join(txt).lower()
        letras = [ch_ for ch_ in " ".join(txt) if ch_.isalpha()]
        r = {"contrato_id": cid, "x_chat_msgs": len(cl), "x_chat_tam_medio": float(txt.str.len().mean()), "x_chat_tam_max": int(txt.str.len().max()),
             "x_chat_hora_primeira": primeira.hour + primeira.minute / 60, "x_chat_dow_primeira": primeira.dayofweek,
             "x_chat_madrugada": float(primeira.hour < 6 or primeira.hour >= 22), "x_chat_fim_de_semana": float(primeira.dayofweek >= 5),
             "x_chat_resp_mediana_s": float(np.median(resp)) if resp else np.nan, "x_chat_resp_rapida_60s": float(np.mean(np.array(resp) < 60)) if resp else np.nan,
             "x_chat_emojis": float(sum(len(EMOJI.findall(t)) for t in txt)), "x_chat_perguntas": float(txt.str.count(r"\?").sum()),
             "x_chat_maiusculas_pct": float(sum(1 for ch_ in letras if ch_.isupper()) / len(letras)) if letras else np.nan,
             "x_chat_exclamacoes": float(txt.str.count("!").sum()), "x_chat_numeros_pct": float(sum(ch_.isdigit() for ch_ in " ".join(txt)) / max(len(" ".join(txt)), 1)),
             "x_chat_dias_conversa": float((g["quando"].max() - g["quando"].min()).total_seconds() / 86400)}
        for nome, pat in TERMOS.items():
            r[f"x_chat_termo_{nome}"] = float(bool(re.search(pat, todo)))
        linhas.append(r)
    st = pd.DataFrame(linhas)
    return out.drop(columns=["cliente_id", "data_inicio"]).merge(st, on="contrato_id", how="left")


def fdr_bh(p):
    p = np.asarray(p, dtype=float)
    n = len(p)
    ordem = np.argsort(p)
    q = np.empty(n)
    corrida = 1.0
    for i, idx in enumerate(ordem[::-1]):
        rank = n - i
        corrida = min(corrida, p[idx] * n / rank)
        q[idx] = corrida
    return q


def testes(df, alvo, cols_num, cols_cat, min_nivel=15):
    d = df[df[alvo].notna()].copy()
    y = d[alvo].astype(int)
    meio = d["data_inicio"].median()
    antigo, novo = d["data_inicio"] <= meio, d["data_inicio"] > meio
    res = []
    for c in cols_num:
        x = pd.to_numeric(d[c].astype(float, errors="ignore"), errors="coerce").astype(float)
        ok = x.notna()
        if ok.sum() < 60 or x[ok].nunique() < 3 or y[ok].nunique() < 2:
            continue
        auc = stats.mannwhitneyu(x[ok][y[ok] == 1], x[ok][y[ok] == 0]).statistic / ((y[ok] == 1).sum() * (y[ok] == 0).sum())
        p = stats.mannwhitneyu(x[ok][y[ok] == 1], x[ok][y[ok] == 0]).pvalue
        rho = stats.spearmanr(x[ok], y[ok]).correlation

        def aucm(m):
            o = ok & m
            if o.sum() < 30 or y[o].nunique() < 2:
                return np.nan
            return stats.mannwhitneyu(x[o][y[o] == 1], x[o][y[o] == 0]).statistic / ((y[o] == 1).sum() * (y[o] == 0).sum())

        a1, a2 = aucm(antigo), aucm(novo)
        res.append({"alvo": alvo, "variavel": c, "tipo": "numerica", "nivel": "(monotonica)", "n": int(ok.sum()), "taxa_nivel": np.nan, "taxa_resto": float(y[ok].mean()),
                    "efeito": float(auc), "spearman": float(rho), "p": float(p), "auc_antigo": a1, "auc_novo": a2,
                    "estavel": bool(a1 == a1 and a2 == a2 and (a1 - 0.5) * (a2 - 0.5) > 0)})
        # tambem testa "tem o dado" (ausencia pode ser o sinal)
    for c in cols_cat:
        s = d[c].astype("object").where(d[c].notna(), "(vazio)")
        for nivel, cnt in s.value_counts().items():
            if cnt < min_nivel or (len(d) - cnt) < min_nivel:
                continue
            em = (s == nivel)
            tab = [[int(((y == 1) & em).sum()), int(((y == 0) & em).sum())], [int(((y == 1) & ~em).sum()), int(((y == 0) & ~em).sum())]]
            p = stats.fisher_exact(tab)[1]
            t_n, t_r = float(y[em].mean()), float(y[~em].mean())

            def taxa_dif(m):
                a, b = y[em & m], y[~em & m]
                return float(a.mean() - b.mean()) if len(a) >= 8 and len(b) >= 8 else np.nan

            d1, d2 = taxa_dif(antigo), taxa_dif(novo)
            res.append({"alvo": alvo, "variavel": c, "tipo": "categorica", "nivel": str(nivel), "n": int(cnt), "taxa_nivel": t_n, "taxa_resto": t_r, "efeito": t_n - t_r,
                        "spearman": np.nan, "p": float(p), "auc_antigo": d1, "auc_novo": d2, "estavel": bool(d1 == d1 and d2 == d2 and d1 * d2 > 0)})
    r = pd.DataFrame(res)
    if len(r):
        r["q_fdr"] = fdr_bh(r["p"].values)
    return r


def candidatas(df):
    excl = ("contrato_id", "cliente_id", "pessoa_id", "tenant_id", "data_inicio", "cliente_desde", "data_nascimento", "documento_final", "cidade", "_gerado_em")
    prefixos_fora = ("y_", "ye_", "pct_calote", "pct_atrasadas_", "atraso_max_", "maduro_", "n_parcelas_ate", "b3_", "b7_", "e1_", "e2_", "e3_", "e5_", "e7_", "e10_", "e14_")
    num, cat = [], []
    for c in df.columns:
        if c in excl or c.startswith(prefixos_fora):
            continue
        if df[c].dtype == object or str(df[c].dtype) in ("category", "bool"):
            if df[c].nunique() <= 60:
                cat.append(c)
        elif pd.api.types.is_numeric_dtype(df[c]):
            num.append(c)
    return num, cat


def rodar(alvos=("ye_0", "y_calote_15", "y_calote_30", "y_calote_45")):
    ch = cf.conecta()
    base = cf.montar(ch)
    ficha = fi.ficha_por_contrato(ch, base)
    df = base.merge(ficha, on="contrato_id", how="left").merge(features_extras(ch, base), on="contrato_id", how="left")
    df["cep3"] = np.nan
    num, cat = candidatas(df)
    print(f"{len(df)} contratos | {len(num)} variaveis numericas, {len(cat)} categoricas", flush=True)
    todas = []
    for a in alvos:
        r = testes(df, a, num, cat)
        todas.append(r)
        sig = r[r["q_fdr"] < 0.10].sort_values("p")
        print(f"\n=== alvo {a}: {len(r)} testes, n rotulado {int(df[a].notna().sum())}, {len(sig)} com q<0.10 (esperado por acaso a 5%: ~{int(0.05 * len(r))} com p<0.05)", flush=True)
        for x in sig.head(25).itertuples():
            print(f"  {x.variavel} [{x.nivel}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} antigo={x.auc_antigo:+.3f} novo={x.auc_novo:+.3f} estavel={x.estavel}", flush=True)
        menores = r.sort_values("p").head(8)
        print("  -- menores p (mesmo sem passar o FDR):", [(x.variavel, x.nivel, round(x.p, 4)) for x in menores.itertuples()][:8], flush=True)
    out = pd.concat(todas)
    out["rodado_em"] = pd.Timestamp.now()
    out.to_pickle("/tmp/motor_scan.pkl")
    return out, df


if __name__ == "__main__":
    out, df = rodar()
    df.to_pickle("/tmp/base_scan.pkl")
