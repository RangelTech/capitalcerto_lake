# O tom (LLM) ajuda o dia 0 fora do tempo? (a) confunde com tamanho do texto? (b) AUC temporal do modelo parcimonioso com e sem tom. Uso: python credit_tom_modelo.py /tmp/llm_tom_api.pkl
import sys

import numpy as np
import pandas as pd
from scipy import stats

import credit_embed as ce
import credit_experimentos as ex
import credit_features as cf

tom = pd.read_pickle(sys.argv[1])
ch = cf.conecta()
df = cf.montar(ch)
tm, _ = ce.textos(ch, df)
df["tam_texto"] = df["contrato_id"].map(tm.str.len())
d = df.merge(tom, on="contrato_id", how="inner")
cols = [c for c in tom.columns if c.startswith("tom_")]
eng = ["tom_evasivo", "tom_desconfia", "tom_pergunta_taxa", "tom_urgencia", "tom_agressivo"]
d["tom_engajamento"] = d[eng].mean(axis=1)
d["tom_eng_sem_tamanho"] = d["tom_engajamento"] - np.poly1d(np.polyfit(np.log1p(d["tam_texto"].fillna(0)), d["tom_engajamento"], 1))(np.log1p(d["tam_texto"].fillna(0)))
for a in ("ye_0", "yi_0", "cluster_ruim"):
    for nome, m in (("sem Pix", d["pix_agendado"] == 0), ("todos", d["pix_agendado"].notna())):
        s = d[m & d[a].notna()]
        for v in ("tam_texto", "tom_engajamento", "tom_eng_sem_tamanho"):
            x, y = s[v], s[a]
            auc = stats.mannwhitneyu(x[y == 1], x[y == 0])[0] / ((y == 1).sum() * (y == 0).sum())
            print(f"{a:13s} {nome:8s} {v:20s} AUC={auc:.3f} p={stats.mannwhitneyu(x[y == 1], x[y == 0])[1]:.4f} n={len(s)}")
parc = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "e_renovacao", "cep_vazio", "cmp_end"]
for a in ("ye_0", "yi_0"):
    for nome, nums in (("parcimonioso", parc), ("+ engajamento", parc + ["tom_engajamento"]), ("+ 12 tons", parc + cols), ("+ engaj sem tamanho", parc + ["tom_eng_sem_tamanho"])):
        for modelo in ("rf", "logistica_L1"):
            o = ex.avalia_temporal(d, a, cf.HORIZONTE_ALVO, ["provedor_pagamento"], nums, modelo, ultimos_meses=5)
            m = ex.metricas(o) if len(o) else {}
            print(f"MODELO {a} {nome:20s} {modelo:12s} AUC={m.get('auc')} [{m.get('auc_ic_lo')}-{m.get('auc_ic_hi')}] n={m.get('n')}")
