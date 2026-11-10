# Testa as features de TOM (LLM, 12 perguntas sim/nao) e de OCR de prints contra calote, inadimplencia e cluster ruim, com a mesma regua dos outros testes:
# AUC de Mann-Whitney por variavel, p-valor, correcao FDR (Benjamini-Hochberg), estabilidade nas duas metades do tempo, e um modelo temporal com e sem tom.
# Uso: python credit_tom_teste.py /tmp/llm_tom_api.pkl   (grava /tmp/tom_resultado.csv)
import sys

import numpy as np
import pandas as pd
from scipy import stats

import credit_features as cf


def auc_mw(x, y):
    d = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": y}).dropna()
    if d["y"].nunique() < 2 or len(d) < 30 or d["x"].nunique() < 2:
        return np.nan, np.nan, len(d)
    a, b = d.loc[d.y == 1, "x"], d.loc[d.y == 0, "x"]
    u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
    return u / (len(a) * len(b)), p, len(d)


def fdr(p):
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


def main():
    tom = pd.read_pickle(sys.argv[1] if len(sys.argv) > 1 else "/tmp/llm_tom_api.pkl")
    df = cf.montar()
    d = df.merge(tom, on="contrato_id", how="inner")
    d["e_renovacao_f"] = (d["ordem_contrato_cliente"] > 1).astype(float)
    cols = [c for c in tom.columns if c.startswith("tom_")]
    alvos = {"calote D+1..45": "ye_0", "calote 30d": "y_calote_30", "inadimplencia D+1..45": "yi_0", "cluster paga e para": "cluster_ruim"}
    meio = d["data_inicio"].median()
    linhas = []
    for nome, a in alvos.items():
        for estrato, m in (("todos", d["pix_agendado"].notna()), ("sem Pix", d["pix_agendado"] == 0), ("renovacao", d["e_renovacao_f"] == 1)):
            for c in cols:
                s = d[m & d[a].notna()]
                auc, p, n = auc_mw(s[c], s[a])
                h1, _, n1 = auc_mw(s.loc[s.data_inicio <= meio, c], s.loc[s.data_inicio <= meio, a])
                h2, _, n2 = auc_mw(s.loc[s.data_inicio > meio, c], s.loc[s.data_inicio > meio, a])
                linhas.append({"alvo": nome, "estrato": estrato, "variavel": c, "n": n, "auc": auc, "p": p, "auc_1a_metade": h1, "auc_2a_metade": h2})
    r = pd.DataFrame(linhas)
    r["q_fdr"] = fdr(r["p"].values)
    r["estavel"] = ((r["auc_1a_metade"] - 0.5) * (r["auc_2a_metade"] - 0.5) > 0)
    r.to_csv("/tmp/tom_resultado.csv", index=False)
    print(f"contratos com tom: {len(d)} | testes: {len(r)} | q<0,10: {(r.q_fdr < 0.10).sum()} | p<0,05: {(r.p < 0.05).sum()} (esperado ao acaso ~{0.05 * r.p.notna().sum():.0f})")
    print(r.sort_values("p").head(15).round(3).to_string(index=False))
    print("\nmedia do tom: renovacao x primeiro")
    print(d.groupby("e_renovacao_f")[cols].mean().round(2).T.to_string())


if __name__ == "__main__":
    main()
