"""Bateria local 2 (10/11/2026): o que faltava da matriz de testes.
 A combinacoes de 4 variaveis (alem de 1 a 3), dentro de cada modo de cobranca, com tom da conversa (LLM), compartilhamento, valor/faturamento e cluster anterior, com FDR
 B matriz de modelos: 12 algoritmos x conjuntos de variaveis x alvos x estratos, validacao temporal (treina so no passado)
 C efeito AJUSTADO de cada variavel (regressao logistica controlando Pix, provedor e mes de inicio): odds ratio ajustado, p e FDR
 D sazonalidade controlada (teste de razao de verossimilhanca de mes, dia da semana e semana do mes, dentro de sem Pix, controlando provedor)
 E estabilidade dos achados fortes por metade do tempo
Uso: python bateria2_local.py <base_com_novas_variaveis.pkl> <base_final_vps.pkl> <pasta_saida>"""
import itertools
import os
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
pd.set_option("display.width", 230)
BASE, VPS, SAIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(SAIDA, exist_ok=True)
LOG = open(os.path.join(SAIDA, "bateria2_log.txt"), "w", encoding="utf-8")


def pr(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


def fdr_bh(p):
    p = np.asarray(p, float)
    ok = ~np.isnan(p)
    q = np.full_like(p, np.nan)
    pv = p[ok]
    o = np.argsort(pv)
    r = pv[o] * len(pv) / (np.arange(len(pv)) + 1)
    r = np.minimum.accumulate(r[::-1])[::-1]
    out = np.empty_like(pv)
    out[o] = np.clip(r, 0, 1)
    q[ok] = out
    return q


# ------------------------------------------------------------------ dados
df = pd.read_pickle(BASE)
v = pd.read_pickle(VPS)
dup = [c for c in v.columns if c in df.columns and c != "contrato_id"]
df = df.drop(columns=dup).merge(v, on="contrato_id", how="left")
df["pix"] = df["pix_agendado"].astype(float)
df["data_inicio"] = pd.to_datetime(df["data_inicio"])
df["e_renovacao"] = (df["ordem_contrato_cliente"] > 1).astype(float)
df["cep_vazio"] = df["uf_cep"].isna().astype(float)
df["valor_log"] = np.log1p(df["valor_principal"].astype(float))
df["dias_lead"] = pd.to_numeric(df["aq_dias_lead_ate_contrato"], errors="coerce").clip(0, 400)
df["pagou_bem"] = df.get("mc_prev_pagou_bem")
df["pib_pc_log"] = np.log(pd.to_numeric(df.get("g_pib_pc"), errors="coerce"))
eng = ["tom_evasivo", "tom_desconfia", "tom_pergunta_taxa", "tom_urgencia", "tom_agressivo"]
df["tom_engajamento"] = df[eng].mean(axis=1)
df["mes"] = df["data_inicio"].dt.to_period("M").astype(str)
df["dom"] = df["data_inicio"].dt.day
df["semana_mes"] = (df["dom"] - 1) // 7 + 1
df["dow"] = df["data_inicio"].dt.dayofweek
ALVOS = {"calote D+1..45": "ye_0", "calote ate D+30": "y_calote_30", "inadimplencia D+1..45": "yi_0", "cluster paga e para": "cluster_ruim"}
for n, a in ALVOS.items():
    pr(f"alvo {n}: n={int(df[a].notna().sum())} taxa={df[a].mean():.3f}")
pr(f"com tom: {int(df['tom_engajamento'].notna().sum())} contratos")


def faixas(d):
    f = pd.DataFrame(index=d.index)
    f["provedor"] = d["provedor_pagamento"].fillna("?")
    f["idade"] = pd.cut(pd.to_numeric(d["idade_na_contratacao"], errors="coerce"), [0, 35, 50, 120], labels=["ate35", "35a50", "mais50"]).astype(object).fillna("idade?")
    f["valor"] = pd.cut(d["valor_principal"].astype(float), [0, 800, 1500, 1e9], labels=["ate800", "801a1500", "mais1500"]).astype(object)
    f["parcelas"] = np.where(d["qtd_parcelas_contratadas"] >= 60, "60+", "<60")
    f["historico"] = np.where(d["ordem_contrato_cliente"] <= 1, "1o contrato", np.where(d.get("mc_prev_pagou_bem") == 1, "renov pagou bem", "renov nao pagou bem"))
    f["cep"] = np.where(d["uf_cep"].isna(), "sem CEP", "com CEP")
    top = d["segmento"].value_counts().head(5).index
    f["segmento"] = d["segmento"].where(d["segmento"].isin(top), "outros").replace("", "sem segmento").fillna("sem segmento")
    f["ramo"] = np.where(d["ramo_cnae_div"].astype(object).isin(["56"]), "alimentacao fora", np.where(d["ramo_cnae_div"].astype(object).isin(["47"]), "varejo",
                         np.where(d["ramo_cnae_div"].notna(), "outros", "ramo?")))
    f["linha_wpp"] = np.where(d["aq_inbox"].astype(str).isin(["7"]), "inbox 7", "outras")
    f["dias_lead"] = pd.cut(d["dias_lead"], [-1, 3, 15, 10000], labels=["ate3", "4a15", "mais15"]).astype(object).fillna("?")
    f["endereco"] = np.where(d["cmp_end"].fillna(0) > 0, "end dividido", "end proprio")
    f["inconsist"] = np.where(d["inconsistencias"].fillna(0) >= 3, "incons 3+", "incons <3")
    f["anterior"] = np.where(d["anterior_paga_e_para"] == 1, "anterior paga e para", np.where(d["anterior_paga_e_para"] == 0, "anterior ok", "sem anterior"))
    te = d["tom_engajamento"]
    f["tom_engaj"] = np.where(te.isna(), "tom?", np.where(te <= te.quantile(0.33), "tom quieto", np.where(te <= te.quantile(0.66), "tom medio", "tom engajado")))
    f["tom_evasivo"] = np.where(d["tom_evasivo"].isna(), "evasivo?", np.where(d["tom_evasivo"] > 0.35, "evasivo alto", "evasivo baixo"))
    f["tom_direto"] = np.where(d["tom_direto"].isna(), "direto?", np.where(d["tom_direto"] > 0.65, "direto", "nao direto"))
    fat = pd.to_numeric(d.get("f_int_vendas_mensais"), errors="coerce")
    r = d["valor_principal"].astype(float) / fat.where(fat > 0)
    f["valor_x_fat"] = np.where(r.isna(), "fat?", np.where(r > r.median(), "valor alto p/ faturamento", "valor ok p/ faturamento"))
    f["origem_cpf"] = d["regiao_fiscal_cpf"].fillna("?").astype(str).str[:10]
    return f


def p_bin(s, n, tot, tot1):
    """teste exato (hipergeometrico) bicaudal aproximado: 2*min(cauda), vetorizado."""
    lo = stats.hypergeom.cdf(s, tot, tot1, n)
    hi = stats.hypergeom.sf(s - 1, tot, tot1, n)
    return np.minimum(1.0, 2 * np.minimum(lo, hi))


# ------------------------------------------------------------------ A. combinacoes 1 a 4 variaveis
pr("\n################ A. COMBINACOES DE 1 A 4 VARIAVEIS (com tom, compartilhamento, valor/faturamento, cluster anterior) ################")
F = faixas(df)
saida = []
for estrato, mask in (("Pix agendado", df["pix"] == 1), ("sem Pix agendado", df["pix"] == 0), ("todos", df["pix"].notna())):
    for nome, a in ALVOS.items():
        d = df[mask & df[a].notna()]
        if len(d) < 80:
            continue
        y = d[a].astype(int)
        base, tot, tot1 = y.mean(), len(d), int(y.sum())
        cols = list(F.columns)
        L = []
        FF = F.loc[d.index]
        for k in (1, 2, 3, 4):
            minimo = 15 if k <= 3 else 12
            for comb in itertools.combinations(cols, k):
                g = pd.concat([FF[list(comb)], y.rename("y")], axis=1).groupby(list(comb))["y"].agg(["count", "sum"])
                g = g[g["count"] >= minimo]
                g = g[g["count"] < tot]
                if not len(g):
                    continue
                n, s = g["count"].values, g["sum"].values
                p = p_bin(s, n, tot, tot1)
                idx = g.index if k > 1 else [(x,) for x in g.index]
                for chave, nn, ss, pp in zip(idx, n, s, p):
                    L.append((estrato, nome, k, " x ".join(comb), " | ".join(str(x) for x in chave), int(nn), ss / nn, base, ss / nn - base, pp))
        r = pd.DataFrame(L, columns=["estrato", "alvo", "n_var", "variaveis", "perfil", "n", "taxa", "base_estrato", "dif", "p"])
        r["q_fdr"] = fdr_bh(r["p"].values)
        saida.append(r)
        sig = r[r["q_fdr"] < 0.10].sort_values("p")
        por_k = r.groupby("n_var").size().to_dict()
        pr(f"\n-- [{estrato}] {nome}: base {base:.2f} (n={tot}); perfis por n de variaveis {por_k}; q<0,10: {len(sig)} ({sig.groupby('n_var').size().to_dict() if len(sig) else {}})")
        for x in sig.head(8).itertuples():
            pr(f"   [{x.variaveis}] {x.perfil}  n={x.n} taxa={x.taxa:.2f} (base {x.base_estrato:.2f}) q={x.q_fdr:.3f}")
pd.concat(saida).to_csv(os.path.join(SAIDA, "combinacoes_1a4.csv"), index=False)

# ------------------------------------------------------------------ B. matriz de modelos
pr("\n################ B. MATRIZ DE MODELOS (12 algoritmos x conjuntos x alvos x estratos, validacao temporal) ################")
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

try:
    from lightgbm import LGBMClassifier
except Exception:  # noqa
    LGBMClassifier = None

P = ["pix", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]
CT = ["valor_log", "qtd_parcelas_contratadas", "razao_parcela_principal", "taxa_juros_pct"]
TOM12 = [c for c in df.columns if c.startswith("tom_") and c != "tom_engajamento"]
CONJ = {
    "A enxuto (6 + provedor)": (["provedor_pagamento"], P),
    "B enxuto + termos do contrato": (["provedor_pagamento"], P + CT),
    "C enxuto + endereco dividido + inconsistencias": (["provedor_pagamento"], P + ["cmp_end", "cmp_qualquer", "inconsistencias"]),
    "D enxuto + cluster do contrato anterior": (["provedor_pagamento"], P + ["anterior_paga_e_para"]),
    "E enxuto + tom engajamento": (["provedor_pagamento"], P + ["tom_engajamento"]),
    "F enxuto + 12 tons": (["provedor_pagamento"], P + TOM12),
    "G enxuto + pib/porte": (["provedor_pagamento"], P + ["pib_pc_log"]),
    "H enxuto + dias lead + pagou bem": (["provedor_pagamento"], P + ["dias_lead", "pagou_bem"]),
    "I enxuto + compart + anterior + tom engaj": (["provedor_pagamento"], P + ["cmp_end", "anterior_paga_e_para", "tom_engajamento"]),
    "J tudo acima": (["provedor_pagamento", "segmento", "sexo", "uf_ddd"], P + CT + ["cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para", "tom_engajamento", "pib_pc_log", "dias_lead", "pagou_bem"] + TOM12),
}
ALGS = ["l1", "l2", "arvore", "rf", "extratrees", "hgb", "lgbm", "nb", "knn", "svm", "mlp"]


def mk(alg, cats, nums):
    pre_t = ColumnTransformer([("n", SimpleImputer(strategy="median", add_indicator=True), nums), ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats)])
    pre_l = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums),
                               ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=10), cats)])
    M = {"l1": (pre_l, LogisticRegression(C=0.1, penalty="l1", solver="liblinear", max_iter=3000)), "l2": (pre_l, LogisticRegression(C=0.1, max_iter=3000)),
         "arvore": (pre_t, DecisionTreeClassifier(max_depth=3, min_samples_leaf=20, random_state=7)),
         "rf": (pre_t, RandomForestClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=4, random_state=7)),
         "extratrees": (pre_t, ExtraTreesClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=4, random_state=7)),
         "hgb": (pre_t, HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=20, l2_regularization=2.0, random_state=7)),
         "nb": (pre_l, GaussianNB()), "knn": (pre_l, KNeighborsClassifier(n_neighbors=25, weights="distance")),
         "svm": (pre_l, SVC(C=0.5, kernel="rbf", probability=True, random_state=7)),
         "mlp": (pre_l, MLPClassifier(hidden_layer_sizes=(16,), alpha=1.0, max_iter=800, random_state=7))}
    if alg == "lgbm" and LGBMClassifier is not None:
        return Pipeline([("p", pre_t), ("m", LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=7, min_child_samples=20, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, verbose=-1, random_state=7, n_jobs=4))])
    pre, m = M[alg if alg in M else "hgb"]
    if alg in ("nb", "knn", "svm", "mlp"):
        pre = pre_l
    return Pipeline([("p", pre), ("m", m)])


def temporal(d, alvo, H, cats, nums, algs, ultimos=5):
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
            try:
                m = mk(a, cf_, nf)
                m.fit(Xt, tr[alvo].astype(int))
                preds[a] += m.predict_proba(Xs)[:, 1].tolist()
            except Exception:  # noqa
                preds[a] += [0.5] * len(te)
    if not ys or len(set(ys)) < 2:
        return {}
    out = {a: roc_auc_score(ys, preds[a]) for a in algs}
    rk = np.mean([pd.Series(preds[a]).rank().values for a in algs], axis=0)
    out["comite"] = roc_auc_score(ys, rk)
    out["n"] = len(ys)
    return out


linhas = []
for estrato, mask in (("todos", df["pix"].notna()), ("sem Pix", df["pix"] == 0)):
    for nome, a in ALVOS.items():
        H = 30 if a == "y_calote_30" else 45
        for cn, (cs, ns) in CONJ.items():
            ns = [c for c in dict.fromkeys(ns) if c in df.columns]
            if estrato == "sem Pix":
                ns = [c for c in ns if c != "pix"]
            r = temporal(df[mask], a, H, cs, ns, ALGS)
            if not r:
                continue
            for alg, auc in r.items():
                if alg != "n":
                    linhas.append({"estrato": estrato, "alvo": nome, "conjunto": cn, "algoritmo": alg, "auc": auc, "n": r["n"]})
            melhor = max((k for k in r if k != "n"), key=lambda k: r[k])
            pr(f"{estrato:8s} {nome:24s} {cn:46s} melhor={melhor}({r[melhor]:.3f}) comite={r['comite']:.3f} rf={r.get('rf', 0):.3f} l1={r.get('l1', 0):.3f} lgbm={r.get('lgbm', 0):.3f} n={r['n']}")
    pd.DataFrame(linhas).to_csv(os.path.join(SAIDA, "matriz_modelos.csv"), index=False)
M = pd.DataFrame(linhas)
M.to_csv(os.path.join(SAIDA, "matriz_modelos.csv"), index=False)
pr("\n-- AUC medio por algoritmo (todos os alvos/conjuntos/estratos):")
pr(M.groupby("algoritmo")["auc"].agg(["mean", "max", "count"]).round(3).sort_values("mean", ascending=False).to_string())
pr("\n-- AUC medio por conjunto:")
pr(M.groupby("conjunto")["auc"].agg(["mean", "max"]).round(3).sort_values("mean", ascending=False).to_string())
pr("\n-- melhor combinacao por alvo e estrato:")
for (e, n), g in M.groupby(["estrato", "alvo"]):
    b = g.sort_values("auc", ascending=False).iloc[0]
    pr(f"   {e:8s} {n:24s} {b.algoritmo:10s} {b.conjunto:46s} AUC={b.auc:.3f} (n={b.n})")

# ------------------------------------------------------------------ C. efeito ajustado (controla Pix, provedor, mes)
pr("\n################ C. EFEITO AJUSTADO POR PIX, PROVEDOR E MES (regressao logistica) ################")
import statsmodels.api as sm

cand = {"endereco dividido": "cmp_end", "algum dado dividido": "cmp_qualquer", "inconsistencias": "inconsistencias", "anterior paga e para": "anterior_paga_e_para", "tom engajamento": "tom_engajamento",
        "tom evasivo": "tom_evasivo", "tom desconfia": "tom_desconfia", "tom pergunta taxa": "tom_pergunta_taxa", "tom urgencia": "tom_urgencia", "tom agressivo": "tom_agressivo", "tom direto": "tom_direto",
        "tom cordial": "tom_cordial", "tom negocio ativo": "tom_negocio_ativo", "tom motivo claro": "tom_motivo_claro", "tom divida": "tom_divida", "tom recusa": "tom_recusa", "tom terceiro": "tom_terceiro",
        "tamanho do texto (log)": "tam_texto_log", "idade": "idade_na_contratacao", "valor (log)": "valor_log", "parcelas 60+": "p60", "razao valor/faturamento": "razao_fat", "dias lead": "dias_lead",
        "pib municipio (log)": "pib_pc_log", "renovacao": "e_renovacao", "cep vazio": "cep_vazio", "dia do mes": "dom", "dia da semana": "dow", "pagou bem anterior": "pagou_bem",
        "historico atraso anterior": "prev_pct_atrasadas"}
df["tam_texto_log"] = np.log1p(df["tam_texto"])
df["p60"] = (df["qtd_parcelas_contratadas"] >= 60).astype(float)
fat = pd.to_numeric(df.get("f_int_vendas_mensais"), errors="coerce")
df["razao_fat"] = np.log(df["valor_principal"].astype(float) / fat.where(fat > 0))
ctrl = pd.get_dummies(df[["provedor_pagamento", "mes"]].fillna("?"), drop_first=True).astype(float)
ctrl["pix"] = df["pix"]
linhas = []
for nome, a in ALVOS.items():
    for rot, c in cand.items():
        x = pd.to_numeric(df[c], errors="coerce")
        d = pd.concat([df[a], x.rename("x"), ctrl], axis=1).dropna()
        if len(d) < 100 or d["x"].nunique() < 2 or d[a].nunique() < 2:
            continue
        d["x"] = (d["x"] - d["x"].mean()) / (d["x"].std() or 1)
        keep = [col for col in ctrl.columns if d[col].nunique() > 1]
        try:
            res = sm.Logit(d[a].astype(int), sm.add_constant(d[["x"] + keep]), missing="drop").fit(disp=0, maxiter=200, method="bfgs")
            lo, hi = np.exp(res.conf_int().loc["x"])
            linhas.append({"alvo": nome, "variavel": rot, "n": len(d), "odds_ratio_por_dp": float(np.exp(res.params["x"])), "ic95_inf": float(lo), "ic95_sup": float(hi), "p": float(res.pvalues["x"])})
        except Exception:  # noqa
            continue
C = pd.DataFrame(linhas)
C["q_fdr"] = fdr_bh(C["p"].values)
C.to_csv(os.path.join(SAIDA, "efeito_ajustado.csv"), index=False)
pr(f"{len(C)} testes; q<0,10: {(C.q_fdr < 0.10).sum()}; p<0,05: {(C.p < 0.05).sum()} (acaso ~{0.05 * len(C):.0f})")
pr(C.sort_values("p").head(20).round(3).to_string(index=False))

# ------------------------------------------------------------------ D. sazonalidade controlada
pr("\n################ D. SAZONALIDADE CONTROLADA (razao de verossimilhanca) ################")
linhas = []
for estrato, mask in (("sem Pix", df["pix"] == 0), ("todos (controla Pix)", df["pix"].notna())):
    for nome, a in ALVOS.items():
        d0 = df[mask & df[a].notna()].copy()
        if len(d0) < 120:
            continue
        base_x = pd.get_dummies(d0[["provedor_pagamento"]].fillna("?"), drop_first=True).astype(float)
        if estrato.startswith("todos"):
            base_x["pix"] = d0["pix"]
        for tipo, col in (("dia da semana", "dow"), ("semana do mes", "semana_mes"), ("mes de inicio (safra)", "mes")):
            dm = pd.get_dummies(d0[col].astype(str), drop_first=True, prefix=col).astype(float)
            try:
                m0 = sm.Logit(d0[a].astype(int), sm.add_constant(base_x), missing="drop").fit(disp=0, method="bfgs", maxiter=200)
                m1 = sm.Logit(d0[a].astype(int), sm.add_constant(pd.concat([base_x, dm], axis=1)), missing="drop").fit(disp=0, method="bfgs", maxiter=200)
                lr = 2 * (m1.llf - m0.llf)
                p = float(stats.chi2.sf(lr, dm.shape[1]))
                linhas.append({"estrato": estrato, "alvo": nome, "efeito": tipo, "n": len(d0), "p_lr": p})
                pr(f"   {estrato:22s} {nome:24s} {tipo:22s} n={len(d0)} p={p:.4f}")
            except Exception as e:  # noqa
                pr("   falhou", estrato, nome, tipo, str(e)[:60])
pd.DataFrame(linhas).to_csv(os.path.join(SAIDA, "sazonalidade_controlada.csv"), index=False)

# ------------------------------------------------------------------ E. estabilidade dos achados fortes por metade do tempo
pr("\n################ E. ESTABILIDADE DOS ACHADOS (metade antiga x metade nova; sem Pix) ################")
meio = df["data_inicio"].median()
alvo = "ye_0"
d = df[(df["pix"] == 0) & df[alvo].notna()]
for rot, c in (("endereco dividido", "cmp_end"), ("tom engajamento", "tom_engajamento"), ("tom evasivo", "tom_evasivo"), ("tom desconfia", "tom_desconfia"), ("valor/faturamento", "razao_fat"), ("anterior paga e para", "anterior_paga_e_para"),
               ("idade", "idade_na_contratacao"), ("inconsistencias", "inconsistencias")):
    r = []
    for nm, s in (("antiga", d[d.data_inicio <= meio]), ("nova", d[d.data_inicio > meio])):
        s = s[[c, alvo]].dropna()
        if s[alvo].nunique() < 2 or len(s) < 30:
            r.append((nm, np.nan, len(s)))
            continue
        x, y = pd.to_numeric(s[c], errors="coerce"), s[alvo]
        r.append((nm, stats.mannwhitneyu(x[y == 1], x[y == 0])[0] / ((y == 1).sum() * (y == 0).sum()), len(s)))
    pr(f"   {rot:24s} " + " | ".join(f"{nm}: AUC={a:.3f} n={n}" for nm, a, n in r))
pr("\nFIM DA BATERIA 2")
