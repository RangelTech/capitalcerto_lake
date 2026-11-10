"""Bateria local 3 (10/11/2026): matriz de modelos POR IDADE DO CONTRATO (D+1 a D+14).
As informacoes novas (endereco dividido, contrato anterior, tom da conversa, valor/faturamento, termos do contrato) melhoram o modelo de comportamento de pagamento?
Para cada idade k e cada alvo (calote ye_k, inadimplencia yi_k): 6 conjuntos de variaveis x 9 algoritmos, validacao temporal (5 meses, treina so no passado),
e para cada conjunto o ganho de AUC sobre "so comportamento" com intervalo de bootstrap (ganho so vale se o IC95 fica acima de zero).
Uso: python bateria3_local.py <base_motor_vps.pkl> <base_com_novas_variaveis.pkl> <pasta_saida>"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
BASE, LOCAL, SAIDA = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(SAIDA, exist_ok=True)
LOG = open(os.path.join(SAIDA, "bateria3_log.txt"), "w", encoding="utf-8")


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
from sklearn.neural_network import MLPClassifier  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler  # noqa: E402
from sklearn.svm import SVC  # noqa: E402

try:
    from lightgbm import LGBMClassifier
except Exception:  # noqa
    LGBMClassifier = None

df = pd.read_pickle(BASE)
loc = pd.read_pickle(LOCAL)
extra = [c for c in ("contrato_id", "aq_dias_lead_ate_contrato", "mc_prev_pagou_bem", "f_int_vendas_mensais", "g_pib_pc") if c in loc.columns]
df = df.merge(loc[extra].drop_duplicates("contrato_id"), on="contrato_id", how="left")
df["data_inicio"] = pd.to_datetime(df["data_inicio"])
df["valor_log"] = np.log1p(df["valor_principal"].astype(float))
df["dias_lead"] = pd.to_numeric(df.get("aq_dias_lead_ate_contrato"), errors="coerce").clip(0, 400)
df["pagou_bem"] = df.get("mc_prev_pagou_bem")
fat = pd.to_numeric(df.get("f_int_vendas_mensais"), errors="coerce")
df["razao_fat"] = np.log(df["valor_principal"].astype(float) / fat.where(fat > 0))
df["tom_engajamento"] = df[["tom_evasivo", "tom_desconfia", "tom_pergunta_taxa", "tom_urgencia", "tom_agressivo"]].mean(axis=1)
P = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]
CT = ["valor_log", "qtd_parcelas_contratadas", "razao_parcela_principal", "taxa_juros_pct"]
TOM = ["tom_engajamento", "tom_agressivo", "tom_recusa"]
ALGS = ["l1", "l2", "rf", "extratrees", "hgb", "lgbm", "mlp", "svm"]
ETAPAS = [1, 2, 3, 5, 7, 10, 14]
NB = 300


def mk(alg, cats, nums):
    pre_t = ColumnTransformer([("n", SimpleImputer(strategy="median", add_indicator=True), nums), ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats)])
    pre_l = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums),
                               ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=10), cats)])
    if alg == "l1":
        return Pipeline([("p", pre_l), ("m", LogisticRegression(C=0.3, penalty="l1", solver="liblinear", max_iter=3000))])
    if alg == "l2":
        return Pipeline([("p", pre_l), ("m", LogisticRegression(C=0.3, max_iter=3000))])
    if alg == "rf":
        return Pipeline([("p", pre_t), ("m", RandomForestClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=4, random_state=7))])
    if alg == "extratrees":
        return Pipeline([("p", pre_t), ("m", ExtraTreesClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=4, random_state=7))])
    if alg == "hgb":
        return Pipeline([("p", pre_t), ("m", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=20, l2_regularization=2.0, random_state=7))])
    if alg == "lgbm" and LGBMClassifier is not None:
        return Pipeline([("p", pre_t), ("m", LGBMClassifier(n_estimators=150, learning_rate=0.05, num_leaves=7, min_child_samples=20, subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0, verbose=-1, random_state=7, n_jobs=4))])
    if alg == "mlp":
        return Pipeline([("p", pre_l), ("m", MLPClassifier(hidden_layer_sizes=(16,), alpha=1.0, max_iter=800, random_state=7))])
    return Pipeline([("p", pre_l), ("m", SVC(C=0.5, kernel="rbf", probability=True, random_state=7))])


def temporal(d, alvo, cats, nums, ultimos=5):
    d = d[d[alvo].notna()].copy()
    d["mes_"] = d["data_inicio"].dt.to_period("M")
    gap = pd.Timedelta(days=45 + 30)
    preds = {a: [] for a in ALGS}
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
        for a in ALGS:
            try:
                m = mk(a, cf_, nf)
                m.fit(Xt, tr[alvo].astype(int))
                preds[a] += m.predict_proba(Xs)[:, 1].tolist()
            except Exception:  # noqa
                preds[a] += [0.5] * len(te)
    if not ys or len(set(ys)) < 2:
        return None
    y = np.array(ys)
    pr_ = {a: np.array(v) for a, v in preds.items()}
    pr_["comite"] = np.mean([pd.Series(v).rank().values for v in preds.values()], axis=0)
    return y, pr_


def boot_delta(y, p_novo, p_base, n=NB, seed=7):
    rng = np.random.RandomState(seed)
    ds = []
    for _ in range(n):
        i = rng.randint(0, len(y), len(y))
        if len(set(y[i])) < 2:
            continue
        ds.append(roc_auc_score(y[i], p_novo[i]) - roc_auc_score(y[i], p_base[i]))
    return float(np.mean(ds)), float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5))


linhas, ganhos = [], []
for k in ETAPAS:
    ek = [c for c in df.columns if c.startswith(f"e{k}_")]
    CONJ = {
        "1 so comportamento": ([], ek),
        "2 comportamento + cadastro enxuto": (["provedor_pagamento"], ek + P),
        "3 + endereco dividido e inconsistencias": (["provedor_pagamento"], ek + P + ["cmp_end", "cmp_qualquer", "inconsistencias"]),
        "4 + contrato anterior (paga e para)": (["provedor_pagamento"], ek + P + ["anterior_paga_e_para"]),
        "5 + tom da conversa": (["provedor_pagamento"], ek + P + TOM),
        "6 tudo (termos, valor/faturamento, dias lead, compart., anterior, tom)": (["provedor_pagamento"], ek + P + CT + ["cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para", "razao_fat", "dias_lead", "pagou_bem"] + TOM),
    }
    for tipo, alvo in (("calote", f"ye_{k}"), ("inadimplencia", f"yi_{k}")):
        res = {}
        for cn, (cs, ns) in CONJ.items():
            ns = [c for c in dict.fromkeys(ns) if c in df.columns]
            r = temporal(df, alvo, cs, ns)
            if r is None:
                continue
            res[cn] = r
            y, pp = r
            aucs = {a: roc_auc_score(y, v) for a, v in pp.items()}
            for a, v in aucs.items():
                linhas.append({"idade": k, "alvo": tipo, "conjunto": cn, "algoritmo": a, "auc": v, "n": len(y)})
            b = max(aucs, key=aucs.get)
            pr(f"D+{k:<2d} {tipo:13s} {cn:70s} melhor={b}({aucs[b]:.3f}) l1={aucs['l1']:.3f} rf={aucs['rf']:.3f} lgbm={aucs.get('lgbm', 0):.3f} comite={aucs['comite']:.3f} n={len(y)}")
        base_nome = "1 so comportamento"
        if base_nome in res:
            yb, pb = res[base_nome]
            for cn, (y, pp) in res.items():
                if cn == base_nome:
                    continue
                for a in ("l1", "l2", "rf", "lgbm", "comite"):
                    d0, lo, hi = boot_delta(y, pp[a], pb[a])
                    ganhos.append({"idade": k, "alvo": tipo, "conjunto": cn, "algoritmo": a, "ganho_auc": d0, "ic95_inf": lo, "ic95_sup": hi, "significativo": int(lo > 0)})
    pd.DataFrame(linhas).to_csv(os.path.join(SAIDA, "matriz_por_idade.csv"), index=False)
    pd.DataFrame(ganhos).to_csv(os.path.join(SAIDA, "ganho_sobre_comportamento.csv"), index=False)

M = pd.DataFrame(linhas)
G = pd.DataFrame(ganhos)
M.to_csv(os.path.join(SAIDA, "matriz_por_idade.csv"), index=False)
G.to_csv(os.path.join(SAIDA, "ganho_sobre_comportamento.csv"), index=False)
pr("\n-- AUC medio por algoritmo (todas as idades, alvos e conjuntos):")
pr(M.groupby("algoritmo")["auc"].agg(["mean", "max"]).round(3).sort_values("mean", ascending=False).to_string())
pr("\n-- AUC medio por conjunto:")
pr(M.groupby("conjunto")["auc"].agg(["mean", "max"]).round(3).sort_values("mean", ascending=False).to_string())
pr("\n-- GANHO sobre 'so comportamento' (media entre algoritmos; significativo = IC95 acima de zero):")
pr(G.groupby("conjunto").agg(ganho_medio=("ganho_auc", "mean"), n_significativos=("significativo", "sum"), testes=("significativo", "count")).round(4).to_string())
sig = G[G["significativo"] == 1].sort_values("ganho_auc", ascending=False)
pr(f"\n{len(sig)} de {len(G)} comparacoes com ganho significativo; as maiores:")
pr(sig.head(15).round(3).to_string(index=False))
pr("\nFIM DA BATERIA 3")
