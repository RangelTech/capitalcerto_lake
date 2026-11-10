"""Bateria focada no DIA 0 (10/11/2026): modelos separados por modo de cobranca (Pix agendado, sem Pix) e dentro de "sem Pix" por canal (provedor), com a variavel de
produto "contrato curto (menos de 60 parcelas)" e as demais informacoes do dia 0. Validacao temporal (treina so no passado, testa nos 5 meses mais recentes).
Pergunta: com essas informacoes, o acerto do dia 0 passa de 0,63 onde o score pode mudar a decisao?
Uso: python bateria_dia0.py <base_motor_vps.pkl> <base_com_novas_variaveis.pkl> <pasta_saida>"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
BASE, LOCAL, SAIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(SAIDA, exist_ok=True)
LOG = open(os.path.join(SAIDA, "dia0_log.txt"), "w", encoding="utf-8")


def pr(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler  # noqa: E402

df = pd.read_pickle(BASE)
loc = pd.read_pickle(LOCAL)
extra = [c for c in ("contrato_id", "aq_dias_lead_ate_contrato", "mc_prev_pagou_bem", "f_int_vendas_mensais") if c in loc.columns]
df = df.merge(loc[extra].drop_duplicates("contrato_id"), on="contrato_id", how="left")
df["data_inicio"] = pd.to_datetime(df["data_inicio"])
df["contrato_curto"] = (df["qtd_parcelas_contratadas"].astype(float) < 60).astype(float)
df["valor_log"] = np.log1p(df["valor_principal"].astype(float))
df["dias_lead"] = pd.to_numeric(df.get("aq_dias_lead_ate_contrato"), errors="coerce").clip(0, 400)
df["tom_engajamento"] = df[["tom_evasivo", "tom_desconfia", "tom_pergunta_taxa", "tom_urgencia", "tom_agressivo"]].mean(axis=1)
fat = pd.to_numeric(df.get("f_int_vendas_mensais"), errors="coerce")
df["razao_fat"] = np.log(df["valor_principal"].astype(float) / fat.where(fat > 0))
df["taxa"] = pd.to_numeric(df["taxa_juros_pct"].astype(str), errors="coerce")
P = ["idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]
CONJ = {
    "provedor": (["provedor_pagamento"], []),
    "provedor + contrato curto": (["provedor_pagamento"], ["contrato_curto"]),
    "provedor + contrato curto + valor": (["provedor_pagamento"], ["contrato_curto", "valor_log", "taxa"]),
    "enxuto (idade, historico, renovacao, CEP) + provedor": (["provedor_pagamento"], P),
    "enxuto + contrato curto + valor": (["provedor_pagamento"], P + ["contrato_curto", "valor_log", "taxa"]),
    "+ endereco dividido, contrato anterior, inconsistencias": (["provedor_pagamento"], P + ["contrato_curto", "valor_log", "taxa", "cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para"]),
    "+ tom da conversa": (["provedor_pagamento"], P + ["contrato_curto", "valor_log", "taxa", "cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para", "tom_engajamento", "tom_agressivo", "tom_recusa"]),
    "+ valor sobre faturamento e dias ate contratar": (["provedor_pagamento"], P + ["contrato_curto", "valor_log", "taxa", "cmp_end", "inconsistencias", "tom_engajamento", "razao_fat", "dias_lead"]),
}
ALGS = ["l1", "l2", "rf", "extratrees", "hgb"]


def mk(alg, cats, nums):
    pre_t = ColumnTransformer([("n", SimpleImputer(strategy="median", add_indicator=True), nums), ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats)])
    pre_l = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums),
                               ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=8), cats)])
    if alg == "l1":
        return Pipeline([("p", pre_l), ("m", LogisticRegression(C=0.3, penalty="l1", solver="liblinear", max_iter=3000))])
    if alg == "l2":
        return Pipeline([("p", pre_l), ("m", LogisticRegression(C=0.3, max_iter=3000))])
    if alg == "rf":
        return Pipeline([("p", pre_t), ("m", RandomForestClassifier(n_estimators=300, min_samples_leaf=6, max_features="sqrt", n_jobs=4, random_state=7))])
    if alg == "extratrees":
        return Pipeline([("p", pre_t), ("m", ExtraTreesClassifier(n_estimators=300, min_samples_leaf=6, max_features="sqrt", n_jobs=4, random_state=7))])
    return Pipeline([("p", pre_t), ("m", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=120, min_samples_leaf=15, l2_regularization=2.0, random_state=7))])


def temporal(d, alvo, cats, nums, ultimos=5, minimo_treino=60):
    d = d[d[alvo].notna()].copy()
    d["mes_"] = d["data_inicio"].dt.to_period("M")
    gap = pd.Timedelta(days=45 + 30)
    preds = {a: [] for a in ALGS}
    ys = []
    for mes in sorted(d["mes_"].unique())[-ultimos:]:
        te, tr = d[d["mes_"] == mes], d[d["data_inicio"] <= mes.start_time - gap]
        if len(te) < 10 or len(tr) < minimo_treino or tr[alvo].nunique() < 2:
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
        for a in ALGS:
            try:
                m = mk(a, cf_, nf)
                m.fit(Xt, tr[alvo].astype(int))
                preds[a] += m.predict_proba(Xs)[:, 1].tolist()
            except Exception:  # noqa
                preds[a] += [0.5] * len(te)
    if not ys or len(set(ys)) < 2:
        return None
    return roc_auc_score_dict(np.array(ys), {a: np.array(v) for a, v in preds.items()})


def roc_auc_score_dict(y, p):
    out = {a: roc_auc_score(y, v) for a, v in p.items()}
    out["comite"] = roc_auc_score(y, np.mean([pd.Series(v).rank().values for v in p.values()], axis=0))
    out["n"] = len(y)
    out["taxa"] = float(y.mean())
    return out


ESTRATOS = {
    "todos": df["pix_agendado"].notna(),
    "sem Pix agendado": df["pix_agendado"] == 0,
    "Pix agendado": df["pix_agendado"] == 1,
    "sem Pix, canal PLUGGY": (df["pix_agendado"] == 0) & (df["provedor_pagamento"] == "PLUGGY"),
    "sem Pix, canal MANUAL": (df["pix_agendado"] == 0) & (df["provedor_pagamento"] == "MANUAL"),
    "sem Pix, 60 parcelas": (df["pix_agendado"] == 0) & (df["contrato_curto"] == 0),
}
linhas = []
for est, m in ESTRATOS.items():
    for nome_alvo, alvo in (("calote ate o dia 45", "ye_0"), ("calote em 30 dias", "y_calote_30")):
        d = df[m]
        for cn, (cs, ns) in CONJ.items():
            ns = [c for c in ns if c in d.columns]
            if est != "todos" and est.startswith("sem Pix,"):
                cs = [c for c in cs if c != "provedor_pagamento"] if "canal" in est else cs
            r = temporal(d, alvo, cs, ns, minimo_treino=40 if est != "todos" else 80)
            if not r:
                continue
            melhor = max((k for k in r if k not in ("n", "taxa")), key=lambda k: r[k])
            linhas.append({"estrato": est, "alvo": nome_alvo, "conjunto": cn, "melhor_algoritmo": melhor, "auc": r[melhor], "auc_comite": r["comite"], "auc_l1": r["l1"], "auc_rf": r["rf"], "n": r["n"], "taxa": r["taxa"]})
            pr(f"{est:24s} {nome_alvo:20s} {cn:58s} melhor={melhor:10s}{r[melhor]:.3f} comite={r['comite']:.3f} l1={r['l1']:.3f} rf={r['rf']:.3f} n={r['n']} taxa={r['taxa']:.2f}")
R = pd.DataFrame(linhas)
R.to_csv(os.path.join(SAIDA, "dia0_modelos.csv"), index=False)
pr("\n-- melhor por estrato e alvo:")
for (e, a), g in R.groupby(["estrato", "alvo"]):
    b = g.sort_values("auc", ascending=False).iloc[0]
    base = g[g["conjunto"] == "provedor"]["auc"].max() if (g["conjunto"] == "provedor").any() else np.nan
    pr(f"   {e:24s} {a:20s} melhor {b.auc:.3f} ({b.melhor_algoritmo}, {b.conjunto}); so provedor {base:.3f}; n={b.n}; taxa {b.taxa:.2f}")
pr("\nFIM DO DIA 0")
