# Teste do cluster do "fluxo de proposta": o cluster 3 do k-means (k=8) sobre o embedding das conversas reune quem escolheu cadencia/parcelas na
# tela da proposta. Ele paga melhor, mas tambem entrou quase todo em setembro/26 (ainda sem tempo de atrasar). Este teste compara com o
# resto SO dentro do mesmo mes de entrada (mesma maturidade), com p por permutacao dentro do mes.
import numpy as np

import estudo_massivo as e

df = e.montar_dados("2026-10-01")
d = df[df["tem_conversa"]].reset_index(drop=True)
P = d[[f"emb_pc{i+1}" for i in range(20)]].values
a, C = e.kmeans(P[:, :10], 8)
d["cluster"] = a
alvo_c = int(d.groupby("cluster")["q_paga_em_dia"].mean().idxmax())
d["fluxo_novo"] = d["cluster"] == alvo_c
print("cluster do fluxo novo:", alvo_c, "| clientes:", int(d["fluxo_novo"].sum()))
print("meses de entrada do fluxo novo:", d[d["fluxo_novo"]]["meses_desde_out25"].value_counts().to_dict())


def comparar(x, rotulo):
    print("")
    print(f"--- {rotulo}: {len(x)} clientes, fluxo novo = {int(x['fluxo_novo'].sum())}")
    for col, nome in [("q_paga_em_dia", "paga em dia"), ("q_pagou_e_parou", "pagou e parou"), ("yatraso", "inadimplente hoje")]:
        z = x[x[col].notna()]
        y = z[col].values.astype(float)
        f = z["fluxo_novo"].values
        if f.sum() < 5 or (~f).sum() < 5:
            print(f"  {nome}: amostra pequena")
            continue
        obs = y[f].mean() - y[~f].mean()
        rng = np.random.default_rng(1)
        est = z["meses_desde_out25"].values
        nulo = []
        for _ in range(3000):
            ff = f.copy()
            for m in np.unique(est):
                ix = np.where(est == m)[0]
                ff[ix] = rng.permutation(f[ix])
            nulo.append(y[ff].mean() - y[~ff].mean())
        p = (np.sum(np.abs(np.array(nulo)) >= abs(obs)) + 1) / 3001
        print(f"  {nome}: fluxo novo {y[f].mean():.3f} (n={int(f.sum())}) | resto {y[~f].mean():.3f} (n={int((~f).sum())}) | dif {obs:+.3f} | p dentro do mes {p:.4f}")


comparar(d, "todos")
comparar(d[d["meses_desde_out25"] >= 10], "entrada de ago/26 em diante")
comparar(d[d["meses_desde_out25"] == 11], "entrada em set/26 (mesmo mes, mesma maturidade)")
comparar(d[d["meses_desde_out25"] == 10], "entrada em ago/26")
