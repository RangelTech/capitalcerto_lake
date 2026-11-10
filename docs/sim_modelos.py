"""Simulacao controlada: qual tipo de modelo recupera qual tipo de correlacao, no tamanho de amostra do projeto (n ~ 400 e 900).
Cada cenario tem UMA estrutura verdadeira de sinal (mais 30 variaveis de ruido). Mede o AUC fora da amostra (CV estratificada repetida)
e compara com o AUC do oraculo (probabilidade verdadeira). Roda local (sklearn)."""
import sys
import warnings

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold
from sklearn.naive_bayes import GaussianNB
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import SplineTransformer, StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier

warnings.filterwarnings("ignore")
RUIDO = 30
BASE = 0.30


def logit(p):
    return np.log(p / (1 - p))


def gera(cenario, n, rng):
    X = rng.normal(size=(n, 6 + RUIDO))
    cat8 = rng.random(n) < 0.08
    cat15 = rng.integers(0, 15, n)
    b0 = logit(BASE)
    if cenario == "S1 linear (1 variavel)":
        z = b0 + 0.55 * X[:, 0]
    elif cenario == "S2 degrau (acima do p75)":
        z = b0 - 0.2 + 1.5 * (X[:, 0] > np.quantile(X[:, 0], 0.75))
    elif cenario == "S3 forma de U (nao monotonica)":
        z = b0 - 0.5 + 0.55 * X[:, 0] ** 2
    elif cenario == "S4 categoria rara (8% dos casos)":
        z = b0 + 1.6 * cat8
    elif cenario == "S5 categoria com 15 niveis":
        ef = rng.normal(scale=0.5, size=15)
        z = b0 + ef[cat15]
    elif cenario == "S6 interacao E (a e b juntos)":
        z = b0 - 0.4 + 2.0 * ((X[:, 0] > 0.3) & (X[:, 1] > 0.3))
    elif cenario == "S7 interacao XOR (a ou b, nao os dois)":
        z = b0 - 0.4 + 1.4 * ((X[:, 0] > 0) ^ (X[:, 1] > 0))
    elif cenario == "S8 soma de 5 efeitos fracos":
        z = b0 + 0.28 * (X[:, 0] + X[:, 1] + X[:, 2] + X[:, 3] + X[:, 4])
    elif cenario == "S9 ausencia informa (dado faltante)":
        falt = rng.random(n) < 0.3
        z = b0 + 1.2 * falt
        X[falt, 0] = np.nan
    elif cenario == "S10 so ruido (controle)":
        z = np.full(n, b0)
    else:
        raise ValueError(cenario)
    p = 1 / (1 + np.exp(-z))
    y = (rng.random(n) < p).astype(int)
    Xc = np.column_stack([X, cat8.astype(float), cat15.astype(float)])
    return Xc, y, p


def modelos():
    imp = lambda m: make_pipeline(__import__("sklearn.impute", fromlist=["SimpleImputer"]).SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), m)  # noqa: E731
    from sklearn.impute import SimpleImputer

    return {
        "logistica L2": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), LogisticRegression(C=0.3, max_iter=2000)),
        "logistica L1": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), LogisticRegression(C=0.2, penalty="l1", solver="liblinear", max_iter=2000)),
        "logistica + splines": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), SplineTransformer(n_knots=4, degree=2), StandardScaler(), LogisticRegression(C=0.05, max_iter=3000)),
        "arvore rasa (prof. 3)": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), DecisionTreeClassifier(max_depth=3, min_samples_leaf=15, random_state=0)),
        "random forest": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), RandomForestClassifier(n_estimators=200, min_samples_leaf=8, max_features="sqrt", n_jobs=1, random_state=0)),
        "extra trees": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), ExtraTreesClassifier(n_estimators=200, min_samples_leaf=8, max_features="sqrt", n_jobs=1, random_state=0)),
        "gradient boosting": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=120, min_samples_leaf=20, l2_regularization=2.0, random_state=0),
        "naive bayes": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), GaussianNB()),
        "kNN (25 vizinhos)": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), KNeighborsClassifier(n_neighbors=25, weights="distance")),
        "SVM (RBF)": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), SVC(C=0.5, probability=True, random_state=0)),
        "rede neural (1 camada)": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), MLPClassifier(hidden_layer_sizes=(16,), alpha=1.0, max_iter=600, early_stopping=True, random_state=0)),
    }


CENARIOS = ["S1 linear (1 variavel)", "S2 degrau (acima do p75)", "S3 forma de U (nao monotonica)", "S4 categoria rara (8% dos casos)", "S5 categoria com 15 niveis",
            "S6 interacao E (a e b juntos)", "S7 interacao XOR (a ou b, nao os dois)", "S8 soma de 5 efeitos fracos", "S9 ausencia informa (dado faltante)", "S10 so ruido (controle)"]


def um(cenario, n, rep):
    rng = np.random.default_rng(1000 * rep + n)
    X, y, p = gera(cenario, n, rng)
    oraculo = roc_auc_score(y, p) if 0 < y.sum() < len(y) else np.nan
    out = []
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=1, random_state=rep)
    for nome, fab in modelos().items():
        aucs = []
        for tr, te in cv.split(X, y):
            try:
                m = fab()
                m.fit(X[tr], y[tr])
                aucs.append(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]))
            except Exception:  # noqa
                aucs.append(np.nan)
        out.append({"cenario": cenario, "n": n, "rep": rep, "modelo": nome, "auc": float(np.nanmean(aucs)), "oraculo": oraculo})
    return out


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    tarefas = [(c, n, r) for c in CENARIOS for n in (400, 900) for r in range(reps)]
    res = Parallel(n_jobs=8)(delayed(um)(*t) for t in tarefas)
    df = pd.DataFrame([x for sub in res for x in sub])
    df.to_csv("sim_modelos_resultado.csv", index=False)
    g = df.groupby(["cenario", "n", "modelo"]).agg(auc=("auc", "mean"), oraculo=("oraculo", "mean")).reset_index()
    g["recupera_pct"] = ((g["auc"] - 0.5) / (g["oraculo"] - 0.5) * 100).where(g["oraculo"] > 0.52)
    g.to_csv("sim_modelos_resumo.csv", index=False)
    pd.set_option("display.width", 250)
    for n in (400, 900):
        t = g[g["n"] == n].pivot(index="modelo", columns="cenario", values="auc").round(3)
        print(f"\n=== AUC fora da amostra, n={n} (oraculo no rodape) ===")
        print(t[CENARIOS].to_string())
        print("oraculo:", g[(g["n"] == n)].groupby("cenario")["oraculo"].mean().round(3).reindex(CENARIOS).to_dict())
    print("\n=== MELHOR MODELO POR TIPO DE CORRELACAO (n=900 e n=400) ===")
    for c in CENARIOS:
        for n in (400, 900):
            x = g[(g["cenario"] == c) & (g["n"] == n)].sort_values("auc", ascending=False)
            print(f"{c:42s} n={n}: " + ", ".join(f"{r.modelo} {r.auc:.3f}" for r in x.head(3).itertuples()) + f" | oraculo {x['oraculo'].iloc[0]:.3f} | pior: {x.iloc[-1]['modelo']} {x.iloc[-1]['auc']:.3f}")


if __name__ == "__main__":
    main()
