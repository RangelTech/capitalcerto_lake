# Varrimento 3 (pedidos do Lucas em 10/11): contrato repetido e padrao de golpe, valor emprestado x atributos, ramo (CNAE) com intervalo,
# por que o bureau nao separa (efeito de selecao) e COMPARACAO DE ALGORITMOS por alvo e conjunto de features (validacao temporal).
# Uso: python credit_scan3.py (le /tmp/base_scan3.pkl gerado por credit_midia_scan.py)
import re
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats

import credit_features as cf
import credit_scan as sc

warnings.filterwarnings("ignore")
pd.set_option("display.width", 220)


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def prepara(df, ch):
    """Contrato repetido: estado do contrato anterior do mesmo cliente (pessoa_id) NO INICIO do atual."""
    fc = ch.query_df("""select contrato_id, pessoa_id, data_inicio, quitado_em, ultimo_vencimento_em, parcelas_pagas_antecipadas, pct_parcelas_pagas, dias_ate_quitar,
                               qtd_parcelas_contratadas, ordem_contrato_cliente, valor_principal, maior_atraso_pago_dias
                        from analytics.fato_contrato where empresa = 'Capital Certo'""")
    fc["data_inicio"] = pd.to_datetime(fc["data_inicio"])
    fc["quitado_em"] = pd.to_datetime(fc["quitado_em"], errors="coerce")
    fc["ultimo_vencimento_em"] = pd.to_datetime(fc["ultimo_vencimento_em"], errors="coerce")
    fc = fc[fc["data_inicio"] > "2000-01-01"].sort_values(["pessoa_id", "data_inicio"])
    fc["prev_id"] = fc.groupby("pessoa_id")["contrato_id"].shift(1)
    fc["prev_valor"] = fc.groupby("pessoa_id")["valor_principal"].shift(1)
    for c in ("quitado_em", "ultimo_vencimento_em", "parcelas_pagas_antecipadas", "pct_parcelas_pagas", "dias_ate_quitar", "qtd_parcelas_contratadas", "maior_atraso_pago_dias", "data_inicio"):
        fc["prev_" + c] = fc.groupby("pessoa_id")[c].shift(1)
    fc["prev_quitado_antes_inicio"] = (fc["prev_quitado_em"].notna() & (fc["prev_quitado_em"] <= fc["data_inicio"])).astype(float)
    fc["prev_quitado_antecipado"] = ((fc["prev_quitado_antes_inicio"] == 1) & (fc["prev_quitado_em"] < fc["prev_ultimo_vencimento_em"] - pd.Timedelta(days=3))).astype(float)
    fc["prev_dias_antecipacao"] = (fc["prev_ultimo_vencimento_em"] - fc["prev_quitado_em"]).dt.days.where(fc["prev_quitado_antes_inicio"] == 1)
    fc["prev_duracao_ate_quitar"] = (fc["prev_quitado_em"] - fc["prev_data_inicio"]).dt.days.where(fc["prev_quitado_antes_inicio"] == 1)
    fc["salto_pct"] = (fc["valor_principal"] / fc["prev_valor"] - 1).where(fc["prev_valor"] > 0)
    fc["salto_maior"] = (fc["salto_pct"] > 0.25).astype(float).where(fc["salto_pct"].notna())
    fc["salto_dobro"] = (fc["salto_pct"] >= 1).astype(float).where(fc["salto_pct"].notna())
    fc["prev_pct_pagas_antecipadas"] = (fc["prev_parcelas_pagas_antecipadas"] / fc["prev_qtd_parcelas_contratadas"]).where(fc["prev_qtd_parcelas_contratadas"] > 0)
    fc["prev_pagou_bem"] = ((fc["prev_quitado_antes_inicio"] == 1) & (fc["prev_maior_atraso_pago_dias"].fillna(0) <= 3)).astype(float)
    # dias entre o fim do anterior e o inicio deste (reaplicacao rapida)
    fc["dias_desde_fim_anterior"] = (fc["data_inicio"] - fc["prev_quitado_em"]).dt.days
    fc["reaplicou_ate_3d"] = (fc["dias_desde_fim_anterior"] <= 3).astype(float).where(fc["dias_desde_fim_anterior"].notna())
    cols = ["contrato_id", "prev_quitado_antes_inicio", "prev_quitado_antecipado", "prev_dias_antecipacao", "prev_duracao_ate_quitar", "salto_pct", "salto_maior", "salto_dobro",
            "prev_pct_pagas_antecipadas", "prev_pagou_bem", "dias_desde_fim_anterior", "reaplicou_ate_3d"]
    df = df.merge(fc[cols].rename(columns={c: "mc_" + c for c in cols if c != "contrato_id"}), on="contrato_id", how="left")
    df["mc_e_renovacao"] = (df["ordem_contrato_cliente"] > 1).astype(float)
    # ramo: CNAE da Receita, senao o do relatorio Dora
    cnae = df["rfb_cnae_classe"].where(df["rfb_cnae_classe"].notna(), df.get("f_dora_cnae_div").astype(str).where(df.get("f_dora_cnae_div").notna()))
    df["ramo_cnae_div"] = df["rfb_cnae_div"].where(df["rfb_cnae_div"].notna(), df.get("f_dora_cnae_div"))
    df["ramo_cnae_classe"] = df["rfb_cnae_classe"]
    df["valor_log"] = np.log1p(df["valor_principal"].astype(float))
    return df


def tabela_taxas(df, col, alvo, minimo=8, topo=14):
    d = df[df[alvo].notna() & df[col].notna()]
    g = d.groupby(col)[alvo].agg(["count", "sum", "mean"]).reset_index()
    g = g[g["count"] >= minimo].copy()
    base = d[alvo].mean()
    g["ic_lo"] = [wilson(k, n)[0] for k, n in zip(g["sum"], g["count"])]
    g["ic_hi"] = [wilson(k, n)[1] for k, n in zip(g["sum"], g["count"])]
    # encolhimento (empirical Bayes simples, forca equivalente a 20 contratos)
    g["taxa_encolhida"] = (g["sum"] + 20 * base) / (g["count"] + 20)
    g["p_fisher"] = [stats.fisher_exact([[int(k), int(n - k)], [int(d[alvo].sum() - k), int(len(d) - n - (d[alvo].sum() - k))]])[1] for k, n in zip(g["sum"], g["count"])]
    g = g.sort_values("taxa_encolhida", ascending=False)
    return base, g.head(topo)


def main():
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan3.pkl")
    df = prepara(df, ch)
    df.to_pickle("/tmp/base_scan4.pkl")
    out = []

    import os
    if os.environ.get("SO_ALG"):
        return rodar_d_g(df, ch, out)
    # ---------------------------------------------------------------- (a) contrato repetido e golpe no segundo
    print("\n######## (a) CONTRATO REPETIDO E PADRAO DE GOLPE ########", flush=True)
    for alvo in ("y_calote_30", "ye_0"):
        r = df[df[alvo].notna()]
        print(f"\n-- alvo {alvo}: calote por ordem do contrato", flush=True)
        print(r.groupby(r["ordem_contrato_cliente"].clip(upper=4))[alvo].agg(["count", "mean"]).round(3).to_string(), flush=True)
        ren = r[r["ordem_contrato_cliente"] > 1]
        print(f"renovacoes com rotulo: {len(ren)} | taxa {ren[alvo].mean():.3f} | primeiros: {r[r['ordem_contrato_cliente'] == 1][alvo].mean():.3f}", flush=True)
        for col in ("mc_prev_quitado_antecipado", "mc_prev_pagou_bem", "mc_salto_maior", "mc_salto_dobro", "mc_reaplicou_ate_3d"):
            t = ren.groupby(col)[alvo].agg(["count", "sum", "mean"]).round(3)
            print(f"  {col}: " + " | ".join(f"{int(i) if i == i else 'nan'}: n={int(x['count'])} calote={x['mean']:.2f}" for i, x in t.iterrows()), flush=True)
        # padrao exato: pagou bem/antecipado E saltou o valor
        padrao = ren[(ren["mc_prev_pagou_bem"] == 1) & (ren["mc_salto_maior"] == 1)]
        resto = ren.drop(padrao.index)
        print(f"  PADRAO 'pagou bem o 1o + valor maior no 2o': n={len(padrao)} calote={padrao[alvo].mean():.2f} (IC {tuple(round(x, 2) for x in wilson(padrao[alvo].sum(), len(padrao)))}) | demais renovacoes: n={len(resto)} calote={resto[alvo].mean():.2f}", flush=True)
        for faixa, g in ren.groupby(pd.cut(ren["mc_salto_pct"], [-1, 0, 0.25, 0.5, 1, 100])):
            if len(g) >= 5:
                print(f"    salto {faixa}: n={len(g)} calote={g[alvo].mean():.2f}", flush=True)
    num = [c for c in df.columns if c.startswith("mc_") and pd.api.types.is_numeric_dtype(df[c])]
    for a in ("y_calote_30", "ye_0"):
        r = sc.testes(df[df["ordem_contrato_cliente"] > 1], a, num, [], min_nivel=8)
        r["alvo"] = a + " (so renovacoes)"
        out.append(r)
        print(f"-- testes univariados so nas renovacoes ({a}):", [(x.variavel, round(x.efeito, 2), round(x.p, 3), x.n) for x in r.sort_values("p").head(6).itertuples()], flush=True)

    # ---------------------------------------------------------------- (b) valor emprestado x atributos
    print("\n######## (b) VALOR EMPRESTADO x ATRIBUTOS ########", flush=True)
    print("valor_principal: ", df["valor_principal"].astype(float).describe().round(0).to_dict(), flush=True)
    for col in ("idade_na_contratacao", "tempo_de_casa_dias", "ordem_contrato_cliente", "rfb_idade_empresa_meses", "rfb_capital_log", "mc_prev_quitado_antecipado", "f_int_vendas_mensais", "f_dora_score_pf", "pix_agendado"):
        if col in df and df[col].notna().sum() > 30:
            x = pd.to_numeric(df[col], errors="coerce")
            ok = x.notna()
            rho, p = stats.spearmanr(x[ok], df.loc[ok, "valor_principal"].astype(float))
            print(f"  valor x {col}: Spearman {rho:+.2f} (p={p:.3f}, n={int(ok.sum())})", flush=True)
    for col in ("sexo", "tipo_pessoa", "segmento", "uf", "regiao_fiscal_cpf", "uf_ddd", "provedor_pagamento", "ramo_cnae_div", "x_primeiro_nome"):
        if col in df:
            g = df.groupby(col)["valor_principal"].agg(lambda s: s.astype(float).median()).rename("mediana")
            n = df.groupby(col).size()
            t = pd.concat([g, n.rename("n")], axis=1)
            t = t[t["n"] >= 15]
            grupos = [x["valor_principal"].astype(float).values for _, x in df.groupby(col) if len(x) >= 15]
            if len(grupos) >= 2:
                kw = stats.kruskal(*grupos)
                print(f"  valor por {col}: Kruskal p={kw.pvalue:.4f} | medianas:", t.sort_values("mediana", ascending=False).head(4)["mediana"].round(0).to_dict(), "...", t.sort_values("mediana").head(2)["mediana"].round(0).to_dict(), flush=True)
    # valor como preditor: calote por faixa de valor (quantis)
    for alvo in ("y_calote_30", "ye_0"):
        r = df[df[alvo].notna()]
        q = pd.qcut(r["valor_principal"].astype(float), 5, duplicates="drop")
        print(f"  calote {alvo} por quintil de valor:", r.groupby(q)[alvo].agg(["count", "mean"]).round(2).values.tolist(), flush=True)

    # ---------------------------------------------------------------- (c) ramo / CNAE
    print("\n######## (c) RAMO (CNAE) ########", flush=True)
    print(f"contratos com CNAE: {int(df['ramo_cnae_div'].notna().sum())} de {len(df)} (Receita {int(df['rfb_cnae_div'].notna().sum())}, +Dora)", flush=True)
    for alvo in ("y_calote_30", "ye_0"):
        for col in ("ramo_cnae_div", "ramo_cnae_classe", "segmento"):
            base, g = tabela_taxas(df, col, alvo, minimo=8, topo=12)
            print(f"\n-- {alvo} por {col} (taxa geral {base:.2f}; encolhida = puxada para a media; ordenado pela taxa encolhida):", flush=True)
            print(g[[col, "count", "mean", "ic_lo", "ic_hi", "taxa_encolhida", "p_fisher"]].round(3).to_string(index=False), flush=True)
    # teste global: ramo explica calote? (qui-quadrado nas divisoes com n>=8)
    for alvo in ("y_calote_30", "ye_0"):
        d = df[df[alvo].notna() & df["ramo_cnae_div"].notna()]
        v = d["ramo_cnae_div"].value_counts()
        d = d[d["ramo_cnae_div"].isin(v[v >= 8].index)]
        tab = pd.crosstab(d["ramo_cnae_div"], d[alvo])
        chi = stats.chi2_contingency(tab)
        print(f"qui-quadrado global ramo (divisao CNAE, n>=8) x {alvo}: chi2={chi[0]:.1f} gl={chi[2]} p={chi[1]:.3f} ({len(d)} contratos, {tab.shape[0]} ramos)", flush=True)
    # familias definidas a mao (alimentacao fora do lar, varejo de alimentos, beleza, vestuario, oficina)
    fam = {"restaurante/lanchonete/bar (56)": "56", "varejo alimentar: mercearia/mercadinho/padaria (471,472)": "47[12]", "vestuario/calcados (4781,4782)": "478[12]", "outros varejos (4789 etc)": "4789",
           "beleza/estetica (9602)": "9602", "oficina/auto (45)": "45"}
    cl = df["ramo_cnae_classe"].astype(str)
    for alvo in ("y_calote_30", "ye_0"):
        r = df[df[alvo].notna()]
        linhas = []
        for nome, pat in fam.items():
            m = r.index.isin(r[cl.loc[r.index].str.match(pat)].index)
            n = int(m.sum())
            if n >= 5:
                k = int(r.loc[m, alvo].sum())
                lo, hi = wilson(k, n)
                p = stats.fisher_exact([[k, n - k], [int(r[alvo].sum() - k), int(len(r) - n - (r[alvo].sum() - k))]])[1]
                linhas.append(f"{nome}: n={n} calote={k / n:.2f} IC[{lo:.2f}-{hi:.2f}] p={p:.3f}")
        print(f"\nfamilias (alvo {alvo}, geral {r[alvo].mean():.2f}):\n  " + "\n  ".join(linhas), flush=True)

    return rodar_d_g(df, ch, out)


def rodar_d_g(df, ch, out):
    # ---------------------------------------------------------------- (d) bureau: efeito de selecao
    print("\n######## (d) BUREAU: POR QUE NAO SEPARA? ########", flush=True)
    fch = ch.query_df("""select dora_faixa_pf as faixa, dora_veredito as veredito, vendido, reprovado, dora_score_pf as score
                         from analytics.ficha_credito_conversa where tem_dora = 1""")
    print(f"conversas com Dora: {len(fch)}", flush=True)
    for c in ("vendido", "reprovado", "score"):
        fch[c] = pd.to_numeric(fch[c], errors="coerce").astype(float)
    print("faixa PF x destino (vendido = virou contrato):", flush=True)
    g = fch.groupby("faixa").agg(n=("vendido", "size"), vendido=("vendido", "mean"), reprovado=("reprovado", "mean")).round(3)
    print(g.to_string(), flush=True)
    s = fch.dropna(subset=["score"])
    if len(s) > 50 and s["vendido"].nunique() > 1:
        auc = stats.mannwhitneyu(s.loc[s.vendido == 1, "score"], s.loc[s.vendido == 0, "score"]).statistic / (int((s.vendido == 1).sum()) * int((s.vendido == 0).sum()))
        print(f"AUC do Score PF para 'virou contrato' (analista aprova): {auc:.3f} (n={len(s)})  <- o bureau INFORMA a decisao do analista", flush=True)
    print("veredito da IA x vendido:", fch.groupby("veredito").agg(n=("vendido", "size"), vendido=("vendido", "mean")).round(3).to_string(), flush=True)
    d = df[df["f_dora_score_pf"].notna()]
    print(f"\ncontratos COM score PF no momento da contratacao: {len(d)} ({int((d['y_calote_30'].notna()).sum())} com rotulo)", flush=True)
    print("faixa PF entre os contratos fechados:", d["f_dora_faixa_pf_ord"].value_counts().sort_index().to_dict(), "(1=A ... 8=H)", flush=True)
    print("score PF entre contratos: ", d["f_dora_score_pf"].describe().round(0).to_dict(), flush=True)
    print("=> restricao de faixa: quem tem bureau muito ruim em geral nao vira contrato; entre os que viram, o score varia pouco", flush=True)

    # ---------------------------------------------------------------- (g) comparacao de algoritmos
    print("\n######## (g) COMPARACAO DE ALGORITMOS ########", flush=True)
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler
    from sklearn.svm import SVC
    import lightgbm as lgb
    import credit_experimentos as ex

    def make(nome, cats, nums):
        pre_lin = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True)), ("s", StandardScaler())]), nums),
                                     ("c", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=10), cats)])
        pre_tree = ColumnTransformer([("n", Pipeline([("i", SimpleImputer(strategy="median", add_indicator=True))]), nums),
                                      ("c", Pipeline([("o", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1))]), cats)])
        if nome == "logistica_L2":
            return Pipeline([("p", pre_lin), ("m", LogisticRegression(C=0.05, max_iter=3000))])
        if nome == "logistica_L1":
            return Pipeline([("p", pre_lin), ("m", LogisticRegression(C=0.1, penalty="l1", solver="liblinear", max_iter=3000))])
        if nome == "random_forest":
            return Pipeline([("p", pre_tree), ("m", RandomForestClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=2, random_state=7))])
        if nome == "extra_trees":
            return Pipeline([("p", pre_tree), ("m", ExtraTreesClassifier(n_estimators=300, min_samples_leaf=8, max_features="sqrt", n_jobs=2, random_state=7))])
        if nome == "hist_gb":
            return Pipeline([("p", pre_tree), ("m", HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, min_samples_leaf=20, l2_regularization=2.0, random_state=7))])
        if nome == "lightgbm":
            return Pipeline([("p", pre_tree), ("m", lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=8, max_depth=4, min_child_samples=25, subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5.0, random_state=7, verbose=-1, n_jobs=2))])
        if nome == "naive_bayes":
            return Pipeline([("p", pre_lin), ("m", GaussianNB())])
        if nome == "knn":
            return Pipeline([("p", pre_lin), ("m", KNeighborsClassifier(n_neighbors=25, weights="distance"))])
        if nome == "svm_rbf":
            return Pipeline([("p", pre_lin), ("m", SVC(C=0.5, probability=True, random_state=7))])
        if nome == "mlp":
            return Pipeline([("p", pre_lin), ("m", MLPClassifier(hidden_layer_sizes=(16,), alpha=1.0, max_iter=800, early_stopping=True, random_state=7))])

    ALGS = ["logistica_L2", "logistica_L1", "random_forest", "extra_trees", "hist_gb", "lightgbm", "naive_bayes", "knn", "svm_rbf", "mlp"]
    mcols = [c for c in df.columns if c.startswith("m_") and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().mean() > 0.05]
    mcols = [c for c in mcols if not c.startswith("m_img_pc")] + [c for c in df.columns if c.startswith("m_img_pc")][:4]
    xcols = [c for c in df.columns if c.startswith("x_") and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().mean() > 0.2]
    fcols = [c for c in df.columns if c.startswith("f_") and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().mean() > 0.1]
    rcols = [c for c in df.columns if (c.startswith("rfb_") or c.startswith("r_")) and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().mean() > 0.1]
    mcc = [c for c in df.columns if c.startswith("mc_") and pd.api.types.is_numeric_dtype(df[c])]
    cat_base = ["provedor_pagamento", "tipo_pessoa", "segmento", "sexo", "uf", "regiao_fiscal_cpf", "uf_ddd", "uf_cep"]
    cat_rf = [c for c in ("rfb_cnae_div", "rfb_porte", "f_dora_veredito", "f_int_identidade", "f_int_vinculo_maquininha", "f_int_maquininha", "f_chat_inbox", "ramo_cnae_div") if c in df.columns]
    parc_n = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]
    base_n = ex.NUM_CONTRATO + ex.NUM_CLIENTE + ex.NUM_GEO + ex.NUM_HIST
    conj = {
        "S1 parcimonioso": (["provedor_pagamento"], parc_n),
        "S2 base completo CRM": (cat_base, base_n),
        "S3 +contrato repetido/valor": (cat_base, base_n + mcc),
        "S4 + CNPJ/ramo/socios": (cat_base + ["ramo_cnae_div"], base_n + mcc + rcols),
        "S5 +bureau/chatwoot": (cat_base + cat_rf, base_n + mcc + rcols + fcols),
        "S6 TUDO (+midia +estilo conversa)": (cat_base + cat_rf, base_n + mcc + rcols + fcols + mcols + xcols),
    }
    linhas = []
    for alvo, H in (("ye_0", cf.HORIZONTE_ALVO), ("y_calote_30", 30)):
        for cn, (cs, ns) in conj.items():
            cs = [c for c in cs if c in df.columns]
            ns = list(dict.fromkeys(c for c in ns if c in df.columns))
            ns = [c for c in ns if pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.05]
            for alg in ALGS:
                try:
                    o = avalia_alg(df, alvo, H, cs, ns, lambda c_, n_, a=alg: make(a, c_, n_), ex)
                except Exception as e:  # noqa
                    print(f"  falha {alvo} {cn} {alg}: {str(e)[:80]}", flush=True)
                    continue
                if not len(o):
                    continue
                m = ex.metricas(o)
                linhas.append({"alvo": alvo, "features": cn, "algoritmo": alg, "n_features": len(ns) + len(cs), **m})
                print(f"{alvo:11s}|{cn:34s}|{alg:13s}|nf={len(ns) + len(cs):3d} n={m['n']} AUC={m['auc']} [{m['auc_ic_lo']}-{m['auc_ic_hi']}] PR={m['pr_auc']} Brier={m['brier']}/{m['brier_base']}", flush=True)
    res = pd.DataFrame(linhas)
    res.to_pickle("/tmp/motor_algoritmos.pkl")
    if len(res):
        print("\n==== MELHOR POR ALVO (AUC fora do tempo) ====", flush=True)
        print(res.sort_values("auc", ascending=False).groupby("alvo").head(8)[["alvo", "features", "algoritmo", "n_features", "auc", "auc_ic_lo", "auc_ic_hi", "pr_auc"]].to_string(index=False), flush=True)
        print("\n==== AUC MEDIO POR ALGORITMO (todos os conjuntos) ====", flush=True)
        print(res.groupby(["alvo", "algoritmo"])["auc"].mean().unstack(0).round(3).sort_values("ye_0", ascending=False).to_string(), flush=True)
        print("\n==== AUC MEDIO POR CONJUNTO DE FEATURES (todos os algoritmos) ====", flush=True)
        print(res.groupby(["alvo", "features"])["auc"].agg(["mean", "max"]).round(3).to_string(), flush=True)


def avalia_alg(df, alvo, H, cats, nums, fabrica, ex, ultimos=5, min_teste=25):
    d = df[df[alvo].notna()].copy()
    d["mes"] = d["data_inicio"].dt.to_period("M")
    gap = pd.Timedelta(days=H + cf.CURA_DIAS)
    saidas = []
    for mes in sorted(d["mes"].unique())[-ultimos:]:
        ini = mes.start_time
        te, tr = d[d["mes"] == mes], d[d["data_inicio"] <= ini - gap]
        if len(te) < min_teste or len(tr) < 80 or tr[alvo].nunique() < 2:
            continue
        nf = [c for c in nums if pd.to_numeric(tr[c], errors="coerce").notna().sum() >= 8 and pd.to_numeric(tr[c], errors="coerce").nunique() > 1]
        cf_ = [c for c in cats if tr[c].notna().sum() >= 8 and tr[c].nunique() > 1]
        if not nf and not cf_:
            continue
        Xt, Xs = tr[nf + cf_].copy(), te[nf + cf_].copy()
        for c in nf:
            Xt[c], Xs[c] = pd.to_numeric(Xt[c], errors="coerce").astype(float), pd.to_numeric(Xs[c], errors="coerce").astype(float)
        for c in cf_:
            Xt[c], Xs[c] = Xt[c].astype(object).where(Xt[c].notna(), np.nan), Xs[c].astype(object).where(Xs[c].notna(), np.nan)
            Xt[c], Xs[c] = Xt[c].astype(object).map(lambda v: v if v == v and v is not None else "__nan__"), Xs[c].astype(object).map(lambda v: v if v == v and v is not None else "__nan__")
        pipe = fabrica(cf_, nf)
        pipe.fit(Xt, tr[alvo].astype(int))
        p = pipe.predict_proba(Xs)[:, 1]
        saidas.append(pd.DataFrame({"contrato_id": te["contrato_id"].values, "mes": str(mes), "y": te[alvo].astype(int).values, "p": p, "n_treino": len(tr)}))
    return pd.concat(saidas) if saidas else pd.DataFrame()


if __name__ == "__main__":
    main()
