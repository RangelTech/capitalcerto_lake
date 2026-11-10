# Depois do motor diario: (1) chance de cada contrato cair em CADA cluster de trajetoria; (2) score do contrato em cada etapa que ele ja atravessou (D+0, D+3, D+7...), para
# achar quem PIOROU; (3) recria as views Gold. Uso: python credit_pos_motor.py
#   analytics.score_cluster_contrato   long: contrato_id x cluster -> probabilidade (regressao logistica multinomial sobre o comportamento de pagamento ate a etapa)
#   analytics.score_contrato_etapas    long: contrato_id x etapa -> prob_calote do modelo daquela etapa
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

import credit_engine as ce
import credit_experimentos as ex
import credit_features as cf
import gold_motor

PARC_D0 = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "e_renovacao", "cep_vazio", "cmp_end", "anterior_paga_e_para"]
DDL1 = """CREATE TABLE IF NOT EXISTS analytics.score_cluster_contrato (
  data_referencia Date COMMENT 'dia da execucao', contrato_id String COMMENT 'contrato', etapa Int8 COMMENT 'etapa do motor usada (maior idade k <= idade do contrato)',
  cluster String COMMENT 'cluster de trajetoria de pagamento (paga em dia, atraso moderado, atraso longo, paga no comeco e para...)',
  probabilidade Float32 COMMENT 'chance do contrato terminar neste cluster (as chances dos clusters de um contrato somam 1)',
  cluster_observado String COMMENT 'cluster em que o contrato realmente caiu (so contratos maduros); vazio se ainda nao se sabe',
  fora_da_amostra UInt8 COMMENT '1 = probabilidade calculada sem usar o proprio contrato no treino (contrato maduro); 0 = contrato ainda em andamento'
) ENGINE = MergeTree ORDER BY (data_referencia, contrato_id, cluster) COMMENT 'Chance de cada contrato cair em cada cluster de inadimplencia, por dia. Regressao logistica multinomial sobre o comportamento de pagamento conhecido ate a etapa.'"""
DDL2 = """CREATE TABLE IF NOT EXISTS analytics.score_contrato_etapas (
  data_referencia Date COMMENT 'dia da execucao', contrato_id String COMMENT 'contrato', etapa Int8 COMMENT 'etapa D+k',
  prob_calote Float32 COMMENT 'probabilidade calibrada de calote que o modelo daquela etapa daria, usando so o que se sabia em D+k'
) ENGINE = MergeTree ORDER BY (data_referencia, contrato_id, etapa) COMMENT 'Score do contrato em cada etapa que ele ja atravessou: mostra se o risco subiu ou desceu com o tempo.'"""


def etapa_de(idade):
    return max([k for k in cf.ETAPAS if k <= idade], default=-1)


def probs_cluster(df, hoje):
    d = df.copy()
    d["idade_dias"] = (hoje - pd.to_datetime(d["data_inicio"])).dt.days
    d["etapa"] = d["idade_dias"].map(etapa_de)
    saidas, resumo = [], []
    conhecido = d["cluster"].notna()
    for k, g in d[d["etapa"] >= 0].groupby("etapa"):
        cols = [c for c in (PARC_D0 if k == 0 else ex.colunas_etapa(df, k)) if c in df.columns and pd.to_numeric(df[c], errors="coerce").notna().sum() > 20]
        if not cols:
            continue
        # treino: TODOS os contratos com cluster conhecido, olhando so o comportamento ate D+k (as colunas e{k}_ existem para qualquer contrato antigo)
        tr = d[conhecido & (d["idade_dias"] >= k)]
        if len(tr) < 60 or tr["cluster"].nunique() < 2:
            continue
        Xtr = tr[cols].apply(pd.to_numeric, errors="coerce").astype(float)
        y = tr["cluster"].astype(str)
        pipe = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
        cv = StratifiedKFold(n_splits=max(2, min(5, int(y.value_counts().min()))), shuffle=True, random_state=7)
        oof = pd.DataFrame(cross_val_predict(pipe, Xtr, y, cv=cv, method="predict_proba"), index=tr.index)
        pipe.fit(Xtr, y)
        cls = list(pipe.classes_)
        oof.columns = cls
        P = pd.DataFrame(pipe.predict_proba(g[cols].apply(pd.to_numeric, errors="coerce").astype(float)), index=g.index, columns=cls)
        com = g.index.intersection(oof.index)
        P.loc[com, cls] = oof.loc[com, cls].values        # contrato maduro: probabilidade fora da amostra
        obs = g["cluster"].notna()
        acc = float((np.array(cls)[oof.values.argmax(axis=1)] == y.values).mean())
        resumo.append((k, len(tr), round(acc, 3), round(float(y.value_counts(normalize=True).max()), 3)))
        for c in cls:
            saidas.append(pd.DataFrame({"contrato_id": g["contrato_id"].values, "etapa": k, "cluster": c, "probabilidade": P[c].values,
                                        "cluster_observado": g["cluster"].astype(object).where(obs, "").values, "fora_da_amostra": obs.astype(int).values}))
    print("cluster multinomial (etapa, n, acuracia fora da amostra, acuracia de chutar o maior cluster):", resumo, flush=True)
    return pd.concat(saidas) if saidas else pd.DataFrame()


def score_etapas(df, modelos, hoje):
    d = df.copy()
    d["idade_dias"] = (hoje - pd.to_datetime(d["data_inicio"])).dt.days
    linhas = []
    for k, mod in modelos.items():
        g = d[d["idade_dias"] >= k]
        if not len(g):
            continue
        X = ce._limpa(g, mod["cats"], mod["nums"])
        p = ce.aplicar_calibrador(mod["cal"], mod["pipe"].predict_proba(X)[:, 1])
        linhas.append(pd.DataFrame({"contrato_id": g["contrato_id"].values, "etapa": k, "prob_calote": p}))
    return pd.concat(linhas) if linhas else pd.DataFrame()


def main():
    import pendulum

    data_ref = pd.Timestamp(pendulum.now("America/Sao_Paulo").to_date_string())
    ch = cf.conecta()
    ch.command(DDL1)
    ch.command(DDL2)
    df = cf.montar(ch, hoje=data_ref)
    modelos = ce.carregar_modelos_anteriores()
    dr = data_ref.date()
    pc = probs_cluster(df, data_ref)
    if len(pc):
        pc.insert(0, "data_referencia", dr)
        ch.command(f"ALTER TABLE analytics.score_cluster_contrato DELETE WHERE data_referencia = '{dr}'", settings={"mutations_sync": 1})
        ch.insert_df("analytics.score_cluster_contrato", pc[["data_referencia", "contrato_id", "etapa", "cluster", "probabilidade", "cluster_observado", "fora_da_amostra"]])
        print("score_cluster_contrato", len(pc), flush=True)
    se = score_etapas(df, modelos, data_ref)
    if len(se):
        se.insert(0, "data_referencia", dr)
        ch.command(f"ALTER TABLE analytics.score_contrato_etapas DELETE WHERE data_referencia = '{dr}'", settings={"mutations_sync": 1})
        ch.insert_df("analytics.score_contrato_etapas", se[["data_referencia", "contrato_id", "etapa", "prob_calote"]])
        print("score_contrato_etapas", len(se), flush=True)
    gold_motor.main()


if __name__ == "__main__":
    main()
