"""Bateria local de testes (10/11/2026), rodada na maquina do Lucas sobre um extrato do lake (base_scan7.pkl + CSVs). Cobre o backlog da spec SPEC_MAPEAMENTO_VARIAVEIS.md:
 1 matriz de associacao entre variaveis (redundancias) e informacao mutua com cada alvo
 2 combinacoes de 2 e 3 variaveis CONDICIONADAS a cobranca (estratos Pix e sem Pix), com FDR
 3 identidade compartilhada (telefone, e-mail, endereco, documento) entre clientes
 4 indice de inconsistencias
 5 falhas de cobranca por contrato (e se explicam o efeito do Pix agendado)
 6 razao valor/faturamento e sazonalidade (dia da semana, dia do mes, mes, feriados), com os limites de poder
 7 cluster do contrato anterior como preditor da renovacao
 8 comite de modelos (L1 + RF + GB) e modelo por classe (CPF x CNPJ), validacao temporal
Uso: python bateria_local.py <pasta com os dados> <pasta de saida>"""
import itertools
import os
import re
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
pd.set_option("display.width", 230)
DADOS = sys.argv[1] if len(sys.argv) > 1 else "."
SAIDA = sys.argv[2] if len(sys.argv) > 2 else "resultados_bateria"
os.makedirs(SAIDA, exist_ok=True)
LOG = open(os.path.join(SAIDA, "bateria_log.txt"), "w", encoding="utf-8")


def pr(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def fdr_bh(p):
    p = np.asarray(p, dtype=float)
    n = len(p)
    o = np.argsort(p)
    q = np.empty(n)
    corrida = 1.0
    for i, idx in enumerate(o[::-1]):
        corrida = min(corrida, p[idx] * n / (n - i))
        q[idx] = corrida
    return q


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def auc_mw(x, y):
    x, y = np.asarray(x, float), np.asarray(y, int)
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    if len(np.unique(y)) < 2 or len(x) < 30:
        return np.nan, np.nan, len(x)
    u = stats.mannwhitneyu(x[y == 1], x[y == 0])
    return u.statistic / ((y == 1).sum() * (y == 0).sum()), u.pvalue, len(x)


# ------------------------------------------------------------------ dados
df = pd.read_pickle(os.path.join(DADOS, "base_scan7.pkl"))
df["pix"] = df["pix_agendado"].astype(float)
df["data_inicio"] = pd.to_datetime(df["data_inicio"])
df["e_renovacao"] = (df["ordem_contrato_cliente"] > 1).astype(float)
df["cep_vazio"] = df["uf_cep"].isna().astype(float)
cl = pd.read_pickle(os.path.join(DADOS, "motor_clusters.pkl"))[["contrato_id", "cluster"]]
df = df.merge(cl, on="contrato_id", how="left")
df["cl_ruim"] = df["cluster"].map(lambda c: np.nan if c != c else float(str(c) in ("paga no comeco e para", "quase nao paga", "paga so parte") or "(3)" in str(c) and False)).astype(float)
# no ultimo rodado os nomes eram 'paga com atraso e quita (n)'/'paga no comeco e para': marca 'paga e para' como ruim
df["cl_ruim"] = df["cluster"].map(lambda c: np.nan if c != c else float("para" in str(c))).astype(float)
ALVOS = {"calote D+1..45 (ye_0)": "ye_0", "calote ate D+30": "y_calote_30", "inadimplencia 30d (atraso)": "y_30", "cluster 'paga e para'": "cl_ruim"}
for nome, a in ALVOS.items():
    pr(f"alvo {nome}: n={int(df[a].notna().sum())} taxa={df[a].mean():.3f}")

# ------------------------------------------------------------------ 1. matriz de associacao e informacao mutua
pr("\n################ 1. MATRIZ DE ASSOCIACAO E INFORMACAO MUTUA ################")
num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().mean() > 0.25 and df[c].nunique() > 2 and not re.match(r"^(y_|ye_|yalt|ysev|pct_|n_parcelas|maduro|e\d+_|b\d+_|atraso_max_|cl_|tj_)", c)
            and c not in ("pix", "ordem_contrato_cliente")]
num_cols = [c for c in num_cols if df[c].nunique() > 3][:90]
M = df[num_cols].apply(pd.to_numeric, errors="coerce").corr(method="spearman", min_periods=60)
M.round(2).to_csv(os.path.join(SAIDA, "matriz_spearman.csv"))
pares = M.where(np.triu(np.ones(M.shape), 1).astype(bool)).stack().rename("rho").reset_index()
pares.columns = ["var_a", "var_b", "rho"]
red = pares[pares["rho"].abs() >= 0.7].sort_values("rho", key=lambda s: -s.abs())
pr(f"{len(num_cols)} variaveis numericas; {len(red)} pares redundantes (|rho|>=0,7). Maiores:")
pr(red.head(15).round(2).to_string(index=False))
red.to_csv(os.path.join(SAIDA, "pares_redundantes.csv"), index=False)
from sklearn.feature_selection import mutual_info_classif

linhas = []
for nome, a in ALVOS.items():
    d = df[df[a].notna()]
    X = d[num_cols].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median())
    mi = mutual_info_classif(X, d[a].astype(int), discrete_features=False, random_state=0, n_neighbors=5)
    for c, v in zip(num_cols, mi):
        linhas.append({"alvo": nome, "variavel": c, "informacao_mutua": float(v)})
mi = pd.DataFrame(linhas)
mi.to_csv(os.path.join(SAIDA, "informacao_mutua.csv"), index=False)
for nome in ALVOS:
    pr(f"-- maior informacao mutua com {nome}:", [(r.variavel, round(r.informacao_mutua, 3)) for r in mi[mi.alvo == nome].sort_values("informacao_mutua", ascending=False).head(8).itertuples()])

# ------------------------------------------------------------------ faixas para combinacoes
def faixas(d):
    f = pd.DataFrame(index=d.index)
    f["provedor"] = d["provedor_pagamento"].fillna("?")
    f["idade"] = pd.cut(pd.to_numeric(d["idade_na_contratacao"], errors="coerce"), [0, 35, 50, 120], labels=["ate35", "35a50", "mais50"]).astype(object).fillna("idade?")
    f["valor"] = pd.cut(d["valor_principal"].astype(float), [0, 500, 1000, 1500, 1e9], labels=["ate500", "501a1000", "1001a1500", "mais1500"]).astype(object)
    f["parcelas"] = np.where(d["qtd_parcelas_contratadas"] >= 60, "60+", "<60")
    f["historico"] = np.where(d["ordem_contrato_cliente"] <= 1, "1o contrato", np.where(d.get("mc_prev_pagou_bem") == 1, "renov pagou bem", "renov nao pagou bem"))
    f["cep"] = np.where(d["uf_cep"].isna(), "sem CEP", "com CEP")
    top = d["segmento"].value_counts().head(7).index
    f["segmento"] = d["segmento"].where(d["segmento"].isin(top), "outros").replace("", "sem segmento").fillna("sem segmento")
    reg = {"SP": "Sudeste", "RJ": "Sudeste", "MG": "Sudeste", "ES": "Sudeste", "PR": "Sul", "SC": "Sul", "RS": "Sul", "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
           "BA": "Nordeste", "SE": "Nordeste", "AL": "Nordeste", "PE": "Nordeste", "PB": "Nordeste", "RN": "Nordeste", "CE": "Nordeste", "PI": "Nordeste", "MA": "Nordeste", "PA": "Norte", "AM": "Norte",
           "AC": "Norte", "RO": "Norte", "RR": "Norte", "AP": "Norte", "TO": "Norte"}
    f["regiao_ddd"] = d["uf_ddd"].map(reg).fillna("?")
    f["origem_cpf"] = d["regiao_fiscal_cpf"].fillna("?").astype(str).str[:10]
    cn = d["ramo_cnae_div"].astype(object)
    f["ramo"] = np.where(cn.isin(["56"]), "alimentacao fora", np.where(cn.isin(["47"]), "varejo", np.where(cn.isin(["95"]), "reparacao", np.where(cn.notna(), "outros", "ramo?"))))
    f["linha_wpp"] = np.where(d["aq_inbox"].astype(str).isin(["7"]), "inbox 7", "outras")
    dl = pd.to_numeric(d["aq_dias_lead_ate_contrato"], errors="coerce")
    f["dias_lead"] = pd.cut(dl, [-1, 3, 15, 10000], labels=["ate3", "4a15", "mais15"]).astype(object).fillna("?")
    f["sexo"] = d["sexo"].replace("", "?").fillna("?")
    f["tipo_pessoa"] = d["tipo_pessoa"].fillna("?")
    pib = pd.to_numeric(d.get("g_pib_pc"), errors="coerce")
    f["pib_municipio"] = pd.qcut(pib, 4, labels=["pib Q1", "pib Q2", "pib Q3", "pib Q4"]).astype(object).fillna("pib?") if pib.notna().sum() > 40 else "pib?"
    f["porte_municipio"] = d.get("g_porte").fillna("?") if "g_porte" in d else "?"
    return f


# ------------------------------------------------------------------ 2. combinacoes condicionadas a cobranca
pr("\n################ 2. COMBINACOES CONDICIONADAS A COBRANCA ################")
F = faixas(df)
saida_comb = []
for estrato, mask in (("Pix agendado", df["pix"] == 1), ("sem Pix agendado", df["pix"] == 0), ("sem Pix e desde jun/26", (df["pix"] == 0) & (df["data_inicio"] >= "2026-06-01"))):
    for nome, a in ALVOS.items():
        d = df[mask & df[a].notna()]
        if len(d) < 80:
            continue
        y = d[a].astype(int)
        base, tot, tot1 = y.mean(), len(d), int(y.sum())
        cols = list(F.columns)
        L = []
        for k in (1, 2, 3):
            for comb in itertools.combinations(cols, k):
                g = pd.concat([F.loc[d.index, list(comb)], y.rename("y")], axis=1).groupby(list(comb))["y"].agg(["count", "sum"])
                g = g[g["count"] >= 15]
                for chave, r in g.iterrows():
                    n, s = int(r["count"]), int(r["sum"])
                    if n == tot:
                        continue
                    p = stats.fisher_exact([[s, n - s], [tot1 - s, tot - n - (tot1 - s)]])[1]
                    L.append({"estrato": estrato, "alvo": nome, "n_var": k, "variaveis": " x ".join(comb), "perfil": " | ".join(str(x) for x in (chave if isinstance(chave, tuple) else (chave,))),
                              "n": n, "taxa": s / n, "base_estrato": base, "dif": s / n - base, "p": p})
        r = pd.DataFrame(L)
        if not len(r):
            continue
        r["q_fdr"] = fdr_bh(r["p"].values)
        saida_comb.append(r)
        sig = r[r["q_fdr"] < 0.10].sort_values("p")
        pr(f"\n-- [{estrato}] {nome}: base {base:.2f} (n={tot}); {len(r)} perfis testados; q<0,10: {len(sig)}")
        for x in sig.head(10).itertuples():
            pr(f"   [{x.variaveis}] {x.perfil}  n={x.n} taxa={x.taxa:.2f} (base {x.base_estrato:.2f}) q={x.q_fdr:.3f}")
pd.concat(saida_comb).to_csv(os.path.join(SAIDA, "combinacoes_condicionadas.csv"), index=False)

# ------------------------------------------------------------------ 3. identidade compartilhada
pr("\n################ 3. IDENTIDADE COMPARTILHADA ################")
cc = pd.read_csv(os.path.join(DADOS, "clientes_contato.csv"), dtype=str).fillna("")
pt = pd.read_csv(os.path.join(DADOS, "ponte_contato.csv"), dtype=str).fillna("")
dig = lambda s: re.sub(r"\D", "", s)  # noqa: E731
cc["tel"] = cc["phone"].map(dig).str[-8:]
cc["email_n"] = cc["email"].str.lower().str.strip()
cc["end"] = (cc["zipcode"].map(dig) + "|" + cc["number"].str.strip().str.lower() + "|" + cc["street"].str.lower().str.strip()).where(cc["zipcode"].map(dig).str.len() == 8)
cc["resp"] = cc["responsible_cpf"].map(dig)
cc["doc"] = cc["cpf_cnpj"].map(dig)
pt["tel_wpp"] = pt["phone_chatwoot"].map(dig).str[-8:]
def conta(col, vazio=("", None)):
    s = cc[col]
    vc = s[~s.isin(vazio)].value_counts()
    return cc["cliente_id"].map(lambda c: 0).where(True) if False else s.map(lambda v: int(vc.get(v, 1)) - 1 if v not in vazio else 0)
cc["cmp_tel"] = conta("tel")
cc["cmp_email"] = conta("email_n")
cc["cmp_end"] = conta("end")
cc["cmp_resp"] = conta("resp")
cc["cmp_qualquer"] = ((cc[["cmp_tel", "cmp_email", "cmp_end", "cmp_resp"]] > 0).sum(axis=1)).astype(float)
# telefone do WhatsApp diferente do telefone do CRM
ptt = pt.merge(cc[["cliente_id", "tel"]], on="cliente_id", how="left")
ptt["tel_dif"] = ((ptt["tel_wpp"] != "") & (ptt["tel"] != "") & (ptt["tel_wpp"] != ptt["tel"])).astype(float)
dif = ptt.groupby("cliente_id")["tel_dif"].max().rename("cmp_tel_wpp_diferente").reset_index()
n_ctt = ptt.groupby("cliente_id")["contato_id"].nunique().rename("cmp_n_contatos_chatwoot").reset_index()
cc = cc.merge(dif, on="cliente_id", how="left").merge(n_ctt, on="cliente_id", how="left")
df = df.merge(cc[["cliente_id", "cmp_tel", "cmp_email", "cmp_end", "cmp_resp", "cmp_qualquer", "cmp_tel_wpp_diferente", "cmp_n_contatos_chatwoot"]], on="cliente_id", how="left")
for c in ("cmp_tel", "cmp_email", "cmp_end", "cmp_resp", "cmp_qualquer"):
    pr(f"{c}: clientes com compartilhamento = {int((cc[c] > 0).sum())} de {len(cc)}")
for nome, a in ALVOS.items():
    for c in ("cmp_tel", "cmp_email", "cmp_end", "cmp_resp", "cmp_qualquer", "cmp_tel_wpp_diferente", "cmp_n_contatos_chatwoot"):
        d = df[df[a].notna() & df[c].notna()]
        if (d[c] > 0).sum() >= 10 and d[a].nunique() > 1:
            x = (d[c] > 0).astype(int)
            tab = [[int(((x == 1) & (d[a] == 1)).sum()), int(((x == 1) & (d[a] == 0)).sum())], [int(((x == 0) & (d[a] == 1)).sum()), int(((x == 0) & (d[a] == 0)).sum())]]
            p = stats.fisher_exact(tab)[1]
            pr(f"   {nome:28s} {c:24s} n_com={int(x.sum())} calote_com={d.loc[x == 1, a].mean():.2f} sem={d.loc[x == 0, a].mean():.2f} p={p:.4f}")

# ------------------------------------------------------------------ 4. indice de inconsistencias
pr("\n################ 4. INDICE DE INCONSISTENCIAS ################")
flags = pd.DataFrame(index=df.index)
flags["identidade_divergente"] = df.get("f_int_identidade").isin(["DIVERGENCIA", "IMPOSSIVEL", "NOME"]).astype(float) if "f_int_identidade" in df else 0.0
flags["maquininha_terceiro"] = (df.get("f_int_vinculo_maquininha") == "terceiro").astype(float) if "f_int_vinculo_maquininha" in df else 0.0
flags["sem_cep"] = df["cep_vazio"]
flags["sem_idade"] = df["idade_na_contratacao"].isna().astype(float)
flags["ddd_difere_cep"] = df.get("uf_ddd_difere_cep", pd.Series(0.0, index=df.index)).astype(float).fillna(0)
flags["cnpj_vinculado_negativado"] = pd.to_numeric(df.get("f_dora_cnpj_vinc_negativado"), errors="coerce").fillna(0)
flags["compartilha_dado"] = (df["cmp_qualquer"].fillna(0) > 0).astype(float)
flags["tel_wpp_diferente"] = df["cmp_tel_wpp_diferente"].fillna(0)
df["inconsistencias"] = flags.sum(axis=1)
pr("distribuicao do indice:", df["inconsistencias"].value_counts().sort_index().to_dict())
for nome, a in ALVOS.items():
    d = df[df[a].notna()]
    auc, p, n = auc_mw(d["inconsistencias"], d[a])
    pr(f"   {nome:28s} AUC={auc:.3f} p={p:.4f} n={n} | calote por nivel:", d.groupby(d["inconsistencias"].clip(upper=4))[a].agg(["count", "mean"]).round(2).values.tolist())
    for estrato, m in (("so sem Pix", d["pix"] == 0), ("so Pix", d["pix"] == 1)):
        s = d[m]
        auc, p, n = auc_mw(s["inconsistencias"], s[a])
        pr(f"        {estrato}: AUC={auc:.3f} p={p:.4f} n={n}")

# ------------------------------------------------------------------ 5. falhas de cobranca
pr("\n################ 5. FALHAS DE COBRANCA POR CONTRATO ################")
ev = pd.read_csv(os.path.join(DADOS, "eventos_contrato.csv"))
for acao in ev["acao"].unique():
    e = ev[ev["acao"] == acao].set_index("contrato_id")
    for k, col in (("3d", "n_3d"), ("7d", "n_7d"), ("total", "n_total")):
        df[f"ev_{acao[:14].lower()}_{k}"] = df["contrato_id"].map(e[col]).fillna(0)
evcols = [c for c in df.columns if c.startswith("ev_") and c.endswith(("_3d", "_7d"))]
for a, nome in (("ye_0", "calote D+1..45"), ("y_calote_30", "calote 30d"), ("cl_ruim", "cluster paga e para")):
    for c in evcols:
        d = df[df[a].notna()]
        auc, p, n = auc_mw(d[c], d[a])
        if not np.isnan(auc) and p < 0.1:
            pr(f"   {nome:22s} {c:36s} AUC={auc:.3f} p={p:.4f} n={n} | media falhas Pix={d.loc[d.pix == 1, c].mean():.1f} sem Pix={d.loc[d.pix == 0, c].mean():.1f}")
fal = [c for c in evcols if "payment_fail" in c and c.endswith("7d")]
if fal:
    df["falhas_7d"] = df[fal].sum(axis=1)
    d = df[df["ye_0"].notna()]
    pr("falhas de cobranca nos 7 primeiros dias por modo de cobranca (media):", d.groupby("pix")["falhas_7d"].mean().round(1).to_dict())
    try:
        import statsmodels.api as sm

        z = d[["ye_0", "pix", "falhas_7d"]].dropna()
        m0 = sm.Logit(z["ye_0"], sm.add_constant(z[["pix"]])).fit(disp=0)
        m1 = sm.Logit(z["ye_0"], sm.add_constant(z[["pix", "falhas_7d"]])).fit(disp=0)
        pr(f"efeito do Pix agendado (log-odds) sem controle={m0.params['pix']:.2f}; controlando as falhas de cobranca={m1.params['pix']:.2f}; falhas={m1.params['falhas_7d']:+.3f}")
    except Exception as e:  # noqa
        pr("statsmodels ausente:", str(e)[:60])

# ------------------------------------------------------------------ 6. valor/faturamento e sazonalidade
pr("\n################ 6. VALOR/FATURAMENTO E SAZONALIDADE ################")
fat = pd.to_numeric(df.get("f_int_vendas_mensais"), errors="coerce")
df["razao_valor_faturamento"] = df["valor_principal"].astype(float) / fat.where(fat > 0)
df["razao_parcela_mes_faturamento"] = df["valor_parcela"].astype(float) * 22 / fat.where(fat > 0) if "valor_parcela" in df else np.nan
df["dow"] = df["data_inicio"].dt.dayofweek
df["dom"] = df["data_inicio"].dt.day
df["mes"] = df["data_inicio"].dt.month
df["semana_mes"] = ((df["dom"] - 1) // 7 + 1)
feriados = pd.to_datetime(["2026-01-01", "2026-02-16", "2026-02-17", "2026-04-03", "2026-04-21", "2026-05-01", "2026-06-04", "2026-09-07", "2026-10-12", "2026-11-02", "2026-11-15"])
df["perto_feriado"] = df["data_inicio"].map(lambda t: float(np.min(np.abs((feriados - t).days)) <= 3))
for a, nome in (("ye_0", "calote D+1..45"), ("y_calote_30", "calote 30d"), ("y_30", "inadimplencia 30d")):
    for c in ("razao_valor_faturamento", "razao_parcela_mes_faturamento", "dow", "dom", "semana_mes", "perto_feriado"):
        for estrato, m in (("todos", df["pix"].notna()), ("sem Pix", df["pix"] == 0)):
            d = df[m & df[a].notna()]
            auc, p, n = auc_mw(d[c], d[a])
            if not np.isnan(auc) and p < 0.15:
                pr(f"   {nome:20s} {c:30s} [{estrato}] AUC={auc:.3f} p={p:.4f} n={n}")
for a in ("y_calote_30",):
    d = df[df[a].notna() & (df["pix"] == 0)]
    pr("calote 30d sem Pix por dia da semana de inicio:", d.groupby("dow")[a].agg(["count", "mean"]).round(2).values.tolist())
    pr("calote 30d sem Pix por mes de inicio:", d.groupby("mes")[a].agg(["count", "mean"]).round(2).to_dict("index"))
pr("LIMITE: ~10 meses de dados, Pix agendado mudou de 3% para 94% entre maio e outubro e a safra muda a cada mes: sazonalidade NAO e separavel de safra/coorte. Os resultados acima sao exploratorios.")

# ------------------------------------------------------------------ 7. cluster do contrato anterior
pr("\n################ 7. CLUSTER DO CONTRATO ANTERIOR ################")
o = df.sort_values(["pessoa_id", "data_inicio"]).copy()
o["cluster_anterior"] = o.groupby("pessoa_id")["cluster"].shift(1)
o["anterior_paga_e_para"] = o["cluster_anterior"].map(lambda c: np.nan if c != c else float("para" in str(c)))
o["anterior_paga_em_dia"] = o["cluster_anterior"].map(lambda c: np.nan if c != c else float("em dia" in str(c) or "(3)" in str(c)))
df = df.merge(o[["contrato_id", "anterior_paga_e_para", "anterior_paga_em_dia"]], on="contrato_id", how="left")
for a, nome in (("ye_0", "calote D+1..45"), ("y_calote_30", "calote 30d"), ("cl_ruim", "cluster paga e para")):
    d = df[df[a].notna() & df["anterior_paga_e_para"].notna()]
    if len(d) > 20:
        for c in ("anterior_paga_e_para",):
            t = d.groupby(c)[a].agg(["count", "mean"]).round(2)
            p = stats.fisher_exact([[int(((d[c] == 1) & (d[a] == 1)).sum()), int(((d[c] == 1) & (d[a] == 0)).sum())], [int(((d[c] == 0) & (d[a] == 1)).sum()), int(((d[c] == 0) & (d[a] == 0)).sum())]])[1] if d[c].nunique() > 1 else np.nan
            pr(f"   {nome:22s} {c}: {t.values.tolist()} (n={len(d)}) p={p:.4f}")

# ------------------------------------------------------------------ 8. comite de modelos e modelo por classe
pr("\n################ 8. COMITE DE MODELOS E MODELO POR CLASSE ################")
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

df["dias_lead"] = pd.to_numeric(df["aq_dias_lead_ate_contrato"], errors="coerce").clip(0, 400)
df["pagou_bem"] = df.get("mc_prev_pagou_bem")
df["valor_log"] = np.log1p(df["valor_principal"].astype(float))
df["pib_pc_log"] = np.log(pd.to_numeric(df.get("g_pib_pc"), errors="coerce"))
P = ["pix", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]
CT = ["valor_log", "qtd_parcelas_contratadas", "razao_parcela_principal", "taxa_juros_pct"]
conjuntos = {
    "P enxuto atual": (["provedor_pagamento"], P),
    "P + termos do contrato": (["provedor_pagamento"], P + CT),
    "P + termos + inconsistencias + compartilhamento": (["provedor_pagamento"], P + CT + ["inconsistencias", "cmp_qualquer"]),
    "P + termos + pib/porte do municipio": (["provedor_pagamento"], P + CT + ["pib_pc_log"]),
    "P + termos + dias_lead + pagou_bem": (["provedor_pagamento"], P + CT + ["dias_lead", "pagou_bem"]),
}


def mk(alg, cats, nums):
    pre_t = ColumnTransformer([("n", SimpleImputer(strategy="median", add_indicator=True), nums), ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats)])
    pre_l = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums), ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=10), cats)])
    if alg == "rf":
        return Pipeline([("p", pre_t), ("m", RandomForestClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=4, random_state=7))])
    if alg == "gb":
        return Pipeline([("p", pre_t), ("m", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=20, l2_regularization=2.0, random_state=7))])
    return Pipeline([("p", pre_l), ("m", LogisticRegression(C=0.1, penalty="l1", solver="liblinear", max_iter=3000))])


def temporal(d, alvo, H, cats, nums, algs=("l1", "rf", "gb"), ultimos=5):
    d = d[d[alvo].notna()].copy()
    d["mes_"] = d["data_inicio"].dt.to_period("M")
    gap = pd.Timedelta(days=H + 30)
    preds = {a: [] for a in algs}
    ys = []
    for mes in sorted(d["mes_"].unique())[-ultimos:]:
        te, tr = d[d["mes_"] == mes], d[d["data_inicio"] <= mes.start_time - gap]
        if len(te) < 15 or len(tr) < 80 or tr[alvo].nunique() < 2:
            continue
        nf = [c for c in nums if pd.to_numeric(tr[c], errors="coerce").notna().sum() >= 8 and pd.to_numeric(tr[c], errors="coerce").nunique() > 1]
        cf_ = [c for c in cats if tr[c].notna().sum() >= 8 and tr[c].nunique() > 1]
        if not nf and not cf_:
            continue
        Xt, Xs = tr[nf + cf_].copy(), te[nf + cf_].copy()
        for c in nf:
            Xt[c], Xs[c] = pd.to_numeric(Xt[c], errors="coerce").astype(float), pd.to_numeric(Xs[c], errors="coerce").astype(float)
        for c in cf_:
            Xt[c], Xs[c] = Xt[c].astype(object).where(Xt[c].notna(), "__nan__"), Xs[c].astype(object).where(Xs[c].notna(), "__nan__")
        ys += te[alvo].astype(int).tolist()
        for a in algs:
            m = mk(a, cf_, nf)
            m.fit(Xt, tr[alvo].astype(int))
            preds[a] += m.predict_proba(Xs)[:, 1].tolist()
    if not ys or len(set(ys)) < 2:
        return {}
    out = {a: roc_auc_score(ys, preds[a]) for a in algs}
    rk = np.mean([pd.Series(preds[a]).rank().values for a in algs], axis=0)
    out["comite (media dos ranks)"] = roc_auc_score(ys, rk)
    out["n"] = len(ys)
    return out


linhas = []
for alvo, H in (("ye_0", 45), ("y_calote_30", 30), ("y_30", 30)):
    for cn, (cs, ns) in conjuntos.items():
        ns = [c for c in dict.fromkeys(ns) if c in df.columns]
        r = temporal(df, alvo, H, cs, ns)
        if r:
            linhas.append({"alvo": alvo, "classe": "todas", "conjunto": cn, **r})
            pr(f"{alvo:11s}| {cn:50s}| " + " ".join(f"{k}={v:.3f}" if k != "n" else f"n={v}" for k, v in r.items()))
    for cls in ("CPF", "CNPJ"):
        d = df[df["tipo_pessoa"] == cls]
        r = temporal(d, alvo, H, ["provedor_pagamento"], P)
        if r:
            linhas.append({"alvo": alvo, "classe": cls, "conjunto": "P enxuto atual (modelo so da classe)", **r})
            pr(f"{alvo:11s}| modelo so da classe {cls:5s}| " + " ".join(f"{k}={v:.3f}" if k != "n" else f"n={v}" for k, v in r.items()))
pd.DataFrame(linhas).to_csv(os.path.join(SAIDA, "comite_e_classes.csv"), index=False)
df.to_pickle(os.path.join(SAIDA, "base_com_novas_variaveis.pkl"))
pr("\nFIM DA BATERIA LOCAL")
