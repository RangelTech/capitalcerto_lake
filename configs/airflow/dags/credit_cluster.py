# (1) CLUSTERS DE INADIMPLENCIA: agrupa os contratos pela trajetoria de pagamento (D+1..D+45) e rotula os grupos (paga em dia, paga com atraso, paga e para,
#     quase nao paga); (2) o que, no DIA 0, se associa a cada cluster; (3) TAXA DE INADIMPLENCIA (atraso, nao so calote) como alvo; (4) TODAS as combinacoes de 2 e 3
#     variaveis do dia 0 (discretizadas) com correcao de falsa descoberta, com e sem Pix agendado. Grava em analytics_ml.motor_clusters_inadimplencia e motor_combinacoes_dia0.
import itertools
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

import credit_features as cf
import credit_scan as sc

warnings.filterwarnings("ignore")
pd.set_option("display.width", 220)


def trajetoria(ch, df):
    pa = cf._parcelas(ch)
    pa["venc"] = pd.to_datetime(pa["data_vencimento"])
    pa["pago_em"] = pd.to_datetime(pa["data_pagamento"])
    base = df[["contrato_id", "data_inicio"]].copy()
    base["data_inicio"] = pd.to_datetime(base["data_inicio"])
    p = pa.merge(base, on="contrato_id")
    p["dia"] = (p["venc"] - p["data_inicio"]).dt.days
    hoje = pd.Timestamp.now().normalize()
    linhas = []
    for cid, g in p.groupby("contrato_id"):
        ini = g["data_inicio"].iloc[0]
        if ini + pd.Timedelta(days=45 + 30) > hoje:
            continue  # ainda nao amadureceu
        g = g[(g["dia"] >= 1) & (g["dia"] <= 45)].sort_values("venc")
        if len(g) < 15:
            continue
        pago = g["pago_em"].notna().to_numpy()
        atraso = np.where(pago, (g["pago_em"] - g["venc"]).dt.days.clip(lower=0), (hoje - g["venc"]).dt.days.clip(lower=0))
        n = len(g)
        met = n // 2
        idx_pagas = np.where(pago)[0]
        linhas.append({"contrato_id": cid, "tj_n": n, "tj_pct_no_prazo": float((pago & (atraso <= 0)).mean()), "tj_pct_paga_atrasada": float((pago & (atraso > 3)).mean()),
                       "tj_pct_aberta": float((~pago).mean()), "tj_atraso_medio": float(np.mean(np.where(pago, atraso, 0))), "tj_atraso_max_pagas": float(np.max(np.where(pago, atraso, 0))),
                       "tj_pagou_1a_metade": float(pago[:met].mean()), "tj_pagou_2a_metade": float(pago[met:].mean()), "tj_ultima_paga_frac": float((idx_pagas.max() + 1) / n) if len(idx_pagas) else 0.0})
    return pd.DataFrame(linhas)


def rotula(c):
    """Nome do cluster a partir do centroide (valores medios da trajetoria D+1..D+45)."""
    if c["tj_pct_aberta"] >= 0.60:
        return "paga no comeco e para" if c["tj_pagou_1a_metade"] >= 0.4 else "quase nao paga"
    if c["tj_pct_aberta"] >= 0.30:
        return "paga so parte"
    if c["tj_atraso_medio"] >= 30:
        return "paga com atraso longo (mais de 30 dias)"
    if c["tj_pct_no_prazo"] >= 0.6 and c["tj_atraso_medio"] < 4:
        return "paga em dia"
    return "paga com atraso moderado (~10 dias)"


def clusters(ch, df):
    tj = trajetoria(ch, df)
    X = StandardScaler().fit_transform(tj[[c for c in tj.columns if c.startswith("tj_") and c != "tj_n"]])
    melhor = None
    for k in (3, 4, 5, 6):
        km = KMeans(n_clusters=k, n_init=20, random_state=7).fit(X)
        s = silhouette_score(X, km.labels_)
        print(f"  k={k} silhouette={s:.3f}", flush=True)
        if melhor is None or s > melhor[0] + 0.01:
            melhor = (s, k, km.labels_)
    s, k, lab = melhor
    tj["cluster_id"] = lab
    cent = tj.groupby("cluster_id")[[c for c in tj.columns if c.startswith("tj_") and c != "tj_n"]].mean()
    nomes = {i: rotula(cent.loc[i]) for i in cent.index}
    # nomes repetidos recebem sufixo
    vistos = {}
    for i, nm in list(nomes.items()):
        vistos[nm] = vistos.get(nm, 0) + 1
        if vistos[nm] > 1:
            nomes[i] = f"{nm} ({vistos[nm]})"
    tj["cluster"] = tj["cluster_id"].map(nomes)
    print(f"\nclusters escolhidos: k={k} silhouette={s:.3f}", flush=True)
    resumo = tj.groupby("cluster").agg(contratos=("contrato_id", "size"), no_prazo=("tj_pct_no_prazo", "mean"), paga_atrasada=("tj_pct_paga_atrasada", "mean"), aberta=("tj_pct_aberta", "mean"),
                                       atraso_medio_dias=("tj_atraso_medio", "mean"), pagou_1a_metade=("tj_pagou_1a_metade", "mean"), pagou_2a_metade=("tj_pagou_2a_metade", "mean")).round(2)
    resumo["pct_dos_contratos"] = (resumo["contratos"] / len(tj) * 100).round(1)
    print(resumo.sort_values("aberta").to_string(), flush=True)
    return tj, resumo


def faixas(df):
    d = pd.DataFrame(index=df.index)
    d["cobranca"] = np.where(df["pix_agendado"].astype(float) == 1, "Pix agendado", "sem Pix")
    d["provedor"] = df["provedor_pagamento"].fillna("?")
    d["idade"] = pd.cut(pd.to_numeric(df["idade_na_contratacao"], errors="coerce"), [0, 35, 50, 120], labels=["ate35", "35a50", "mais50"]).astype(object).fillna("idade?")
    d["valor"] = pd.cut(df["valor_principal"].astype(float), [0, 500, 1000, 1500, 1e9], labels=["ate500", "501a1000", "1001a1500", "mais1500"]).astype(object)
    d["historico"] = np.where(df["ordem_contrato_cliente"] <= 1, "1o contrato", np.where(df["mc_prev_pagou_bem"] == 1, "renov pagou bem", "renov nao pagou bem"))
    d["cep"] = np.where(df["uf_cep"].isna(), "sem CEP", "com CEP")
    top = df["segmento"].value_counts().head(7).index
    d["segmento"] = df["segmento"].where(df["segmento"].isin(top), "outros").replace("", "sem segmento").fillna("sem segmento")
    reg = {"SP": "Sudeste", "RJ": "Sudeste", "MG": "Sudeste", "ES": "Sudeste", "PR": "Sul", "SC": "Sul", "RS": "Sul", "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
           "BA": "Nordeste", "SE": "Nordeste", "AL": "Nordeste", "PE": "Nordeste", "PB": "Nordeste", "RN": "Nordeste", "CE": "Nordeste", "PI": "Nordeste", "MA": "Nordeste",
           "PA": "Norte", "AM": "Norte", "AC": "Norte", "RO": "Norte", "RR": "Norte", "AP": "Norte", "TO": "Norte"}
    d["regiao_ddd"] = df["uf_ddd"].map(reg).fillna("?")
    d["origem_cpf"] = df["regiao_fiscal_cpf"].fillna("?").astype(str).str[:12]
    cn = df["ramo_cnae_div"].astype(object)
    d["ramo"] = np.where(cn.isin(["56"]), "alimentacao fora (56)", np.where(cn.isin(["47"]), "varejo (47)", np.where(cn.isin(["95"]), "reparacao (95)", np.where(cn.notna(), "outros ramos", "ramo?"))))
    d["linha_wpp"] = np.where(df["aq_inbox"].astype(str).isin(["7"]), "inbox 7", "outras")
    dl = pd.to_numeric(df["aq_dias_lead_ate_contrato"], errors="coerce")
    d["dias_lead"] = pd.cut(dl, [-1, 3, 15, 10000], labels=["ate3", "4a15", "mais15"]).astype(object).fillna("?")
    d["sexo"] = df["sexo"].replace("", "?").fillna("?")
    d["tipo_pessoa"] = df["tipo_pessoa"].fillna("?")
    return d


def combinacoes(df, alvo, subset_nome, mask, nvars=(2, 3), minn=15):
    F = faixas(df)
    d = df[mask & df[alvo].notna()]
    Fd = F.loc[d.index]
    y = d[alvo].astype(int)
    base = y.mean()
    tot, tot1 = len(d), int(y.sum())
    linhas = []
    colunas = list(F.columns)
    for k in nvars:
        for comb in itertools.combinations(colunas, k):
            g = pd.concat([Fd[list(comb)], y.rename("y")], axis=1).groupby(list(comb))["y"].agg(["count", "sum"])
            g = g[g["count"] >= minn]
            for chave, r in g.iterrows():
                n, s = int(r["count"]), int(r["sum"])
                tab = [[s, n - s], [tot1 - s, tot - n - (tot1 - s)]]
                p = stats.fisher_exact(tab)[1]
                linhas.append({"alvo": alvo, "subconjunto": subset_nome, "n_variaveis": k, "variaveis": " x ".join(comb), "perfil": " | ".join(str(x) for x in (chave if isinstance(chave, tuple) else (chave,))),
                               "n": n, "taxa": s / n, "taxa_base": base, "diferenca": s / n - base, "p": p})
    r = pd.DataFrame(linhas)
    if len(r):
        r["q_fdr"] = sc.fdr_bh(r["p"].values)
    return r


def main():
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan6.pkl")
    df["pix"] = df["pix_agendado"].astype(float)
    # ---------------- (1) clusters
    print("######## CLUSTERS DE INADIMPLENCIA (trajetoria D+1..D+45) ########", flush=True)
    tj, resumo = clusters(ch, df)
    df = df.merge(tj[["contrato_id", "cluster"]], on="contrato_id", how="left")
    for nome in resumo.index:
        df["cl_" + nome[:12]] = (df["cluster"] == nome).astype(float).where(df["cluster"].notna())
    # clusters x tempo e x cobranca
    d = df[df["cluster"].notna()].copy()
    d["mes"] = d["data_inicio"].dt.to_period("M").astype(str)
    print("\ncluster por modo de cobranca (linhas somam 100%):", flush=True)
    print((pd.crosstab(np.where(d["pix"] == 1, "Pix agendado", "sem Pix"), d["cluster"], normalize="index") * 100).round(0).to_string(), flush=True)
    print("\ncluster por provedor:", flush=True)
    print((pd.crosstab(d["provedor_pagamento"], d["cluster"], normalize="index") * 100).round(0).to_string(), flush=True)
    # ---------------- (2) o que no dia 0 se associa ao cluster
    F = faixas(df)
    print("\n######## DIA 0 x CLUSTER (qui-quadrado por variavel, FDR) ########", flush=True)
    res = []
    dd = df[df["cluster"].notna()]
    for c in F.columns:
        tab = pd.crosstab(F.loc[dd.index, c], dd["cluster"])
        tab = tab[tab.sum(axis=1) >= 8]
        if tab.shape[0] >= 2 and tab.shape[1] >= 2:
            chi = stats.chi2_contingency(tab)
            res.append((c, chi[0], chi[2], chi[1], int(tab.values.sum())))
    r = pd.DataFrame(res, columns=["variavel", "chi2", "gl", "p", "n"])
    r["q"] = sc.fdr_bh(r["p"].values)
    print(r.sort_values("p").round(4).to_string(index=False), flush=True)
    # ---------------- (3) taxa de inadimplencia como alvo (atraso >3 dias em >=30% das parcelas ate D+15/30/45)
    print("\n######## TAXA DE INADIMPLENCIA (atraso) x variaveis do dia 0 ########", flush=True)
    ncol = [c for c in ("idade_na_contratacao", "valor_principal", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "tempo_de_casa_dias", "razao_parcela_principal", "taxa_juros_pct", "qtd_parcelas_contratadas",
                         "aq_dias_lead_ate_contrato", "f_dora_score_pf", "f_int_vendas_mensais", "rfb_idade_empresa_meses", "mc_prev_pagou_bem", "mc_salto_pct") if c in df.columns]
    df["e_renovacao"] = (df["ordem_contrato_cliente"] > 1).astype(float)
    ccol = [c for c in ("provedor_pagamento", "segmento", "uf_ddd", "regiao_fiscal_cpf", "sexo", "tipo_pessoa", "ramo_cnae_div", "aq_inbox") if c in df.columns] + ["pix"]
    todas = []
    for alvo in ("y_15", "y_30", "y_45"):
        rr = sc.testes(df, alvo, [c for c in ncol if c in df.columns] + ["pix"], [c for c in ccol if c != "pix"], min_nivel=10)
        rr["bloco"] = "inadimplencia (atraso)"
        todas.append(rr)
        sig = rr[rr["q_fdr"] < 0.10].sort_values("p")
        print(f"\n== alvo {alvo} (inadimplencia): n={int(df[alvo].notna().sum())} taxa={df[alvo].mean():.2f}; {len(rr)} testes; q<0.10: {len(sig)}", flush=True)
        for x in sig.head(10).itertuples():
            print(f"   {x.variavel} [{x.nivel[:14]}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} estavel={x.estavel}", flush=True)
    # cluster "ruim" como alvo
    ruins = [c for c in df.columns if c.startswith("cl_") and any(t in c for t in ("nao paga", "paga no come", "paga so part"))]
    for cc in ruins:
        rr = sc.testes(df, cc, [c for c in ncol if c in df.columns] + ["pix"], [c for c in ccol if c != "pix"], min_nivel=10)
        if len(rr):
            rr["bloco"] = "cluster:" + cc
            todas.append(rr)
            sig = rr[rr["q_fdr"] < 0.10].sort_values("p")
            print(f"\n== alvo {cc}: n={int(df[cc].notna().sum())} taxa={df[cc].mean():.2f}; q<0.10: {len(sig)}", flush=True)
            for x in sig.head(6).itertuples():
                print(f"   {x.variavel} [{x.nivel[:14]}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f}", flush=True)
    # ---------------- (4) combinacoes
    print("\n######## COMBINACOES DE 2 E 3 VARIAVEIS NO DIA 0 ########", flush=True)
    todas_comb = []
    for alvo in ("y_calote_30", "ye_0", "y_30"):
        for nome, mask in (("todos os contratos", df["pix"].notna() | df["pix"].isna()), ("so SEM Pix agendado", df["pix"] == 0), ("so desde jun/26", df["data_inicio"] >= "2026-06-01")):
            rc = combinacoes(df, alvo, nome, mask)
            todas_comb.append(rc)
            if not len(rc):
                continue
            sig = rc[rc["q_fdr"] < 0.10].sort_values("p")
            print(f"\n-- {alvo} | {nome}: {len(rc)} perfis testados (n>=15); com q<0.10: {len(sig)}", flush=True)
            for x in sig.head(8).itertuples():
                print(f"   [{x.variaveis}] {x.perfil}  n={x.n} taxa={x.taxa:.2f} (base {x.taxa_base:.2f}) q={x.q_fdr:.3f}", flush=True)
    pd.concat(todas_comb).to_pickle("/tmp/motor_combinacoes.pkl")
    pd.concat(todas).to_pickle("/tmp/motor_scan_inadimplencia.pkl")
    tj.to_pickle("/tmp/motor_clusters.pkl")
    resumo.to_pickle("/tmp/motor_clusters_resumo.pkl")


if __name__ == "__main__":
    main()
