# Laboratorio do motor de credito: compara alvos x conjuntos de features x algoritmos com validacao TEMPORAL
# (treina so com contratos cujo rotulo ja estava fechado na data do corte, testa no mes seguinte).
# Uso: python credit_experimentos.py   (dentro do container do Airflow; escreve o resultado em analytics_ml.motor_experimentos)
import json
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

import credit_features as cf

warnings.filterwarnings("ignore")

CAT_CRM = ["provedor_pagamento", "tipo_pessoa", "segmento", "sexo", "tipo_empresa", "uf"]
CAT_GEO = ["regiao_fiscal_cpf", "uf_ddd", "uf_cep", "uf_cadastro"]
NUM_CONTRATO = ["valor_principal", "valor_parcela", "qtd_parcelas_contratadas", "taxa_juros_pct", "razao_parcela_principal",
                "ordem_contrato_cliente", "valor_contrato_anterior", "salto_valor", "razao_valor_vs_anterior",
                "dias_desde_quitacao_anterior", "maior_salto_valor_historico", "dow_inicio", "pix_agendado", "e_renovacao", "cep_vazio"]
NUM_CLIENTE = ["idade_na_contratacao", "tempo_de_casa_dias", "sem_perfil", "renda_declarada"]
NUM_GEO = ["tem_cep", "uf_ddd_difere_cep", "uf_ddd_difere_cadastro"]
NUM_HIST = ["prev_n_parcelas", "prev_pct_atrasadas", "prev_atraso_max", "prev_contratos"]
NUM_B = ["b3_pct_pagas_no_prazo", "b3_pct_atrasadas", "b3_atraso_max", "b7_pct_pagas_no_prazo", "b7_pct_atrasadas", "b7_atraso_max", "b7_pct_abertas"]


def conjuntos(df):
    rfb = [c for c in df.columns if c.startswith("rfb_")]
    rfb_cat = [c for c in rfb if df[c].dtype == object or str(df[c].dtype) == "category"]
    rfb_num = [c for c in rfb if c not in rfb_cat]
    ficha = [c for c in df.columns if c.startswith("f_")]
    ficha_cat = [c for c in ficha if df[c].dtype == object]
    ficha_num = [c for c in ficha if c not in ficha_cat]
    S = {
        "F0_contrato": (CAT_CRM[:1], NUM_CONTRATO),
        "F1_+cliente": (CAT_CRM, NUM_CONTRATO + NUM_CLIENTE),
        "F2_+geografia": (CAT_CRM + CAT_GEO, NUM_CONTRATO + NUM_CLIENTE + NUM_GEO),
        "F3_+historico": (CAT_CRM + CAT_GEO, NUM_CONTRATO + NUM_CLIENTE + NUM_GEO + NUM_HIST),
        "F4_+receita": (CAT_CRM + CAT_GEO + rfb_cat, NUM_CONTRATO + NUM_CLIENTE + NUM_GEO + NUM_HIST + rfb_num),
    }
    if ficha:
        S["F5_+chatwoot_bureau"] = (CAT_CRM + CAT_GEO + rfb_cat + ficha_cat, NUM_CONTRATO + NUM_CLIENTE + NUM_GEO + NUM_HIST + rfb_num + ficha_num)
    base = S[list(S)[-1]]
    S["B_+primeiros_dias(3,7)"] = (base[0], base[1] + NUM_B)
    return S


def pipeline(modelo, cats, nums):
    if modelo == "logistica":
        pre = ColumnTransformer([
            ("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums),
            ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=15), cats),
        ])
        return Pipeline([("p", pre), ("m", LogisticRegression(C=0.05, penalty="l2", max_iter=2000))])
    enc = ColumnTransformer([
        ("n", "passthrough", nums),
        ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats),
    ])
    if modelo == "hgb":
        m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=25, l2_regularization=2.0,
                                           categorical_features=list(range(len(nums), len(nums) + len(cats))), random_state=7)
        return Pipeline([("p", enc), ("m", m)])
    if modelo == "rf":
        from sklearn.ensemble import RandomForestClassifier

        pre_rf = ColumnTransformer([("n", SimpleImputer(strategy="median", add_indicator=True), nums), ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats)])
        return Pipeline([("p", pre_rf), ("m", RandomForestClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=2, random_state=7))])
    if modelo == "extratrees":
        from sklearn.ensemble import ExtraTreesClassifier

        pre_et = ColumnTransformer([("n", SimpleImputer(strategy="median", add_indicator=True), nums), ("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), cats)])
        return Pipeline([("p", pre_et), ("m", ExtraTreesClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=2, random_state=7))])
    if modelo == "logistica_L1":
        pre_l1 = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums),
                                    ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=15), cats)])
        return Pipeline([("p", pre_l1), ("m", LogisticRegression(C=0.1, penalty="l1", solver="liblinear", max_iter=3000))])
    if modelo == "lightgbm":
        import lightgbm as lgb

        m = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=8, max_depth=4, min_child_samples=25, subsample=0.8,
                               subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, random_state=7, verbose=-1, n_jobs=2)
        return Pipeline([("p", enc), ("m", m)])
    raise ValueError(modelo)


def ks(y, p):
    from scipy.stats import ks_2samp

    return float(ks_2samp(p[y == 1], p[y == 0]).statistic) if 0 < y.sum() < len(y) else np.nan


def lift_decil(y, p):
    n = max(len(y) // 10, 1)
    o = np.argsort(-p)[:n]
    return float(y[o].mean() / y.mean()) if y.mean() > 0 else np.nan


def avalia_temporal(df, alvo, H, cats, nums, modelo, min_teste=25, ultimos_meses=None):
    """Treina so com contratos iniciados ate (inicio do mes de teste - H - CURA) e testa no mes. Devolve as previsoes fora do tempo."""
    d = df[df[alvo].notna()].copy()
    d["mes"] = d["data_inicio"].dt.to_period("M")
    gap = pd.Timedelta(days=H + cf.CURA_DIAS)
    saidas = []
    meses = sorted(d["mes"].unique())
    if ultimos_meses:
        meses = meses[-ultimos_meses:]  # so os meses recentes: o perfil da carteira muda (ex.: Pix agendado), mes antigo engana
    for mes in meses:
        ini = mes.start_time
        teste = d[d["mes"] == mes]
        treino = d[d["data_inicio"] <= ini - gap]
        if len(teste) < min_teste or len(treino) < 80 or treino[alvo].nunique() < 2:
            continue
        # coluna sem informacao no treino (toda vazia ou constante) so atrapalha: sai desta dobra
        nums_f = [c for c in nums if pd.to_numeric(treino[c], errors="coerce").notna().sum() >= 8 and pd.to_numeric(treino[c], errors="coerce").nunique() > 1]
        cats_f = [c for c in cats if treino[c].notna().sum() >= 8 and treino[c].nunique() > 1]
        if not nums_f and not cats_f:  # nenhuma coluna com informacao no treino desta dobra: preve a taxa media do treino
            saidas.append(pd.DataFrame({"contrato_id": teste["contrato_id"].values, "mes": str(mes), "y": teste[alvo].astype(int).values,
                                        "p": float(treino[alvo].mean()), "n_treino": len(treino)}))
            continue
        pipe = pipeline(modelo, cats_f, nums_f)
        nums_o, cats_o = nums, cats
        nums, cats = nums_f, cats_f
        X = lambda x: x[nums + cats]  # noqa: E731
        Xt = X(treino).copy()
        Xs = X(teste).copy()
        for c in cats:
            Xt[c] = Xt[c].astype(object).where(Xt[c].notna(), np.nan)
            Xs[c] = Xs[c].astype(object).where(Xs[c].notna(), np.nan)
        for c in nums:
            Xt[c] = pd.to_numeric(Xt[c], errors="coerce").astype(float)
            Xs[c] = pd.to_numeric(Xs[c], errors="coerce").astype(float)
        pipe.fit(Xt, treino[alvo].astype(int))
        p = pipe.predict_proba(Xs)[:, 1]
        saidas.append(pd.DataFrame({"contrato_id": teste["contrato_id"].values, "mes": str(mes), "y": teste[alvo].astype(int).values, "p": p,
                                    "n_treino": len(treino)}))
        nums, cats = nums_o, cats_o
    return pd.concat(saidas) if saidas else pd.DataFrame()


def metricas(o):
    y, p = o["y"].values, np.clip(o["p"].values, 1e-4, 1 - 1e-4)
    if len(np.unique(y)) < 2:
        return {}
    rng = np.random.default_rng(1)
    aucs = []
    for _ in range(200):
        i = rng.integers(0, len(y), len(y))
        if len(np.unique(y[i])) == 2:
            aucs.append(roc_auc_score(y[i], p[i]))
    return {"n": len(y), "taxa": round(float(y.mean()), 3), "auc": round(roc_auc_score(y, p), 4), "auc_ic_lo": round(float(np.percentile(aucs, 2.5)), 3),
            "auc_ic_hi": round(float(np.percentile(aucs, 97.5)), 3), "pr_auc": round(average_precision_score(y, p), 4), "ks": round(ks(y, p), 4),
            "brier": round(brier_score_loss(y, p), 4), "brier_base": round(float(y.mean() * (1 - y.mean())), 4), "logloss": round(log_loss(y, p), 4),
            "lift_top_decil": round(lift_decil(y, p), 2)}


def rodar(df=None, alvos=("y_calote_30",), modelos=("logistica", "hgb", "lightgbm")):
    df = df if df is not None else cf.montar()
    S = conjuntos(df)
    linhas = []
    for alvo in alvos:
        H = int(alvo.split("_")[-1])
        for nome, (cats, nums) in S.items():
            nums = [c for c in nums if c in df.columns]
            cats = [c for c in cats if c in df.columns]
            for modelo in modelos:
                o = avalia_temporal(df, alvo, H, cats, nums, modelo)
                if not len(o):
                    continue
                m = metricas(o)
                linhas.append({"alvo": alvo, "features": nome, "modelo": modelo, **m})
                print(f"{alvo} | {nome:24s} | {modelo:9s} | n={m.get('n')} taxa={m.get('taxa')} AUC={m.get('auc')} [{m.get('auc_ic_lo')}-{m.get('auc_ic_hi')}] "
                      f"PR={m.get('pr_auc')} KS={m.get('ks')} Brier={m.get('brier')} (base {m.get('brier_base')}) lift={m.get('lift_top_decil')}", flush=True)
    return pd.DataFrame(linhas)


def colunas_etapa(df, k):
    return [c for c in df.columns if c.startswith(f"e{k}_")]


def rodar_etapas(df=None, modelos=("logistica", "lightgbm"), etapas=None):
    """Um modelo por idade do contrato (k dias). Alvo ye_k = calote nas parcelas DEPOIS de k, ate 45 dias (sem vazamento)."""
    df = df if df is not None else cf.montar()
    cats, nums_base = CAT_CRM + CAT_GEO, NUM_CONTRATO + NUM_CLIENTE + NUM_GEO + NUM_HIST
    linhas = []
    for k in (etapas if etapas is not None else cf.ETAPAS):
        alvo = f"ye_{k}"
        ek = colunas_etapa(df, k)
        conjuntos_k = {"so_base": (cats, nums_base)} if k == 0 else {"so_comportamento": ([], ek), "base+comportamento": (cats, nums_base + ek)}
        for nome, (cs, ns) in conjuntos_k.items():
            for modelo in modelos:
                o = avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, [c for c in cs if c in df.columns], [c for c in ns if c in df.columns], modelo)
                if not len(o):
                    continue
                m = metricas(o)
                linhas.append({"etapa": k, "features": nome, "modelo": modelo, **m})
                print(f"etapa D+{k:<2d} | {nome:20s} | {modelo:9s} | n={m.get('n')} taxa={m.get('taxa')} AUC={m.get('auc')} [{m.get('auc_ic_lo')}-{m.get('auc_ic_hi')}] "
                      f"PR={m.get('pr_auc')} KS={m.get('ks')} Brier={m.get('brier')} (base {m.get('brier_base')}) lift={m.get('lift_top_decil')}", flush=True)
    return pd.DataFrame(linhas)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "etapas":
        rodar_etapas().to_csv("/tmp/motor_experimentos_etapas.csv", index=False)
        sys.exit(0)
    alvos = sys.argv[1].split(",") if len(sys.argv) > 1 else ["y_calote_30"]
    res = rodar(alvos=tuple(alvos))
    res.to_csv("/tmp/motor_experimentos.csv", index=False)
