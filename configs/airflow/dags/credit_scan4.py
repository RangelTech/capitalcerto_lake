# Varrimento 4: funil (tempos e voltas), atributos da conversa, aquisicao (anuncio Meta), alvos alternativos (nunca pagou, severidade, fuga de garantia),
# interacoes entre as variaveis que passaram e modelo da DECISAO DO ANALISTA (leads sem contrato). Resultado em /tmp e em analytics_ml.motor_scan_extra.
import re
import warnings

import numpy as np
import pandas as pd
from scipy import stats

import credit_features as cf
import credit_scan as sc

warnings.filterwarnings("ignore")
pd.set_option("display.width", 220)


def conversa_do_contrato(ch, base):
    pon = ch.query_df("select cliente_id, contato_id from analytics.ponte_lead_cliente where vinculo_valido = 1")
    conv = ch.query_df("select id as conversa_id, contact_id as contato_id, inbox_id, created_at as conversa_criada_em, custom_attributes from trusted_chatwoot.conversations final")
    c = base[["contrato_id", "cliente_id", "data_inicio"]].merge(pon, on="cliente_id").merge(conv, on="contato_id")
    c["cutoff"] = pd.to_datetime(c["data_inicio"]) + pd.Timedelta(days=1)
    return c.sort_values("conversa_criada_em").groupby("contrato_id").tail(1)  # 1 conversa por contrato: a mais recente


def funil(ch, c):
    ids = ",".join(str(int(x)) for x in c["conversa_id"].unique())
    t = ch.query_df(f"""select conversation_id as conversa_id, from_stage_id, to_stage_id, performed_at as quando, time_in_previous_stage as tempo, reason
                        from trusted_chatwoot.kanban_stage_transitions final where conversation_id in ({ids})""")
    st = ch.query_df("select id as stage_id, name from trusted_chatwoot.kanban_stages final")
    t = t.merge(st.rename(columns={"stage_id": "to_stage_id", "name": "to_nome"}), on="to_stage_id", how="left")
    m = c[["contrato_id", "conversa_id", "cutoff"]].merge(t, on="conversa_id")
    m = m[pd.to_datetime(m["quando"]) <= m["cutoff"]]
    g = m.groupby("contrato_id")
    f = pd.DataFrame({"fn_n_transicoes": g.size(), "fn_n_estagios_distintos": g["to_nome"].nunique(), "fn_tempo_total_h": g["tempo"].sum() / 3600.0,
                      "fn_tempo_max_estagio_h": g["tempo"].max() / 3600.0, "fn_n_ab_test": g.apply(lambda x: int((x["reason"] == "ab_test").sum())),
                      "fn_n_manual": g.apply(lambda x: int(x["reason"].fillna("").str.contains("Manual|Sidebar", regex=True).sum())),
                      "fn_n_automacao": g.apply(lambda x: int((x["reason"] == "automation_rule").sum()))}).reset_index()
    for nome in ("Relatório - recebido", "Cadastro - IA", "Cadastro - APROVADO", "Leads - Quentes", "PagBank/Ton", "CRÉDITO PARA IA ANALISAR"):
        f["fn_passou_" + re.sub(r"\W+", "_", nome.lower())] = f["contrato_id"].map(g.apply(lambda x, n=nome: float((x["to_nome"] == n).any())))
    return f


def aquisicao(ch, c):
    ids = ",".join(str(int(x)) for x in c["contato_id"].unique())
    n = ch.query_df(f"select contact_id as contato_id, created_at as quando, content from trusted_chatwoot.notes final where contact_id in ({ids}) and content like 'Referral Meta%'")
    m = c[["contrato_id", "contato_id", "cutoff"]].merge(n, on="contato_id")
    m = m[pd.to_datetime(m["quando"]) <= m["cutoff"]].sort_values("quando")
    m["criativo"] = m["content"].str.extract(r"Criativo:\s*([^\\\n]+)", expand=False).str.strip().str[:40]
    m["anuncio"] = m["content"].str.extract(r"An[uú]ncio:\s*([^\\\n]+)", expand=False).str.strip().str[:40]
    u = m.groupby("contrato_id").tail(1)[["contrato_id", "criativo", "anuncio"]].rename(columns={"criativo": "aq_criativo", "anuncio": "aq_anuncio"})
    base = c[["contrato_id", "inbox_id", "conversa_criada_em", "data_inicio"]].copy()
    base["aq_inbox"] = base["inbox_id"].astype(str)
    base["aq_hora_lead"] = pd.to_datetime(base["conversa_criada_em"]).dt.hour
    base["aq_dow_lead"] = pd.to_datetime(base["conversa_criada_em"]).dt.dayofweek
    base["aq_dias_lead_ate_contrato"] = (pd.to_datetime(base["data_inicio"]) - pd.to_datetime(base["conversa_criada_em"])).dt.days
    base["aq_veio_de_anuncio"] = base["contrato_id"].isin(u["contrato_id"]).astype(float)
    return base.drop(columns=["inbox_id", "conversa_criada_em", "data_inicio"]).merge(u, on="contrato_id", how="left")


def atributos_estado_atual(c):
    """custom_attributes da conversa HOJE (nao tem data): podem refletir o que aconteceu DEPOIS do contrato. Testados a parte e marcados como possivel vazamento."""
    out = []
    for r in c.itertuples():
        try:
            a = r.custom_attributes if isinstance(r.custom_attributes, dict) else __import__("json").loads(r.custom_attributes or "{}")
        except Exception:  # noqa
            a = {}
        out.append({"contrato_id": r.contrato_id, "at_proposta": a.get("proposta_escolha"), "at_ton_step": a.get("pagbank_ton_pending_step"), "at_foto_fachada_pendente": float(bool(a.get("aguardando_foto_fachada"))),
                    "at_deskdata_status": a.get("deskdata_status"), "at_desativar_ia": float(bool(a.get("desativar_ia"))), "at_formulario": float(bool(a.get("formulario_whatsapp"))),
                    "at_cnpj_nao_confirmado": float(bool(a.get("cnpj_ownership_unconfirmed_number"))), "at_outra_maquina": float(bool(a.get("pagbank_ton_other_machine_name")))})
    return pd.DataFrame(out)


def alvos_alternativos(ch, df):
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
        r = {"contrato_id": cid}
        mad = (ini + pd.Timedelta(days=45) <= hoje)
        g45 = g[(g["dia"] <= 45) & (g["venc"] < hoje)]
        # nunca pagou: nenhuma parcela paga entre as vencidas nos primeiros 45 dias (so rotula se ha >=10 vencidas)
        r["yalt_nunca_pagou"] = float(g45["pago_em"].notna().sum() == 0) if (mad and len(g45) >= 10) else np.nan
        # pagou so as primeiras: pagou ate 5 e parou
        pag = g45[g45["pago_em"].notna()]
        r["yalt_parou_cedo"] = float(len(pag) > 0 and len(pag) <= 5 and len(g45) >= 20) if (mad and len(g45) >= 20) else np.nan
        atraso = np.where(g45["pago_em"].notna(), (g45["pago_em"] - g45["venc"]).dt.days.clip(lower=0), (hoje - g45["venc"]).dt.days.clip(lower=0))
        r["ysev_atraso_max_45"] = float(np.max(atraso)) if len(atraso) and mad else np.nan
        r["ysev_pct_aberto_45"] = float(g45["pago_em"].isna().mean()) if len(g45) and mad else np.nan
        linhas.append(r)
    out = pd.DataFrame(linhas)
    fc = ch.query_df("select contrato_id, marcado_fuga_garantia from analytics.fato_contrato where empresa = 'Capital Certo'")
    out = out.merge(fc.rename(columns={"marcado_fuga_garantia": "yalt_fuga_garantia"}), on="contrato_id", how="left")
    out["yalt_fuga_garantia"] = pd.to_numeric(out["yalt_fuga_garantia"], errors="coerce")
    return out


def decisao_analista(ch):
    """Modelo da decisao do analista nas conversas com relatorio: virou contrato (1) x reprovado/desqualificado/desistiu (0). Temporal por mes."""
    f = ch.query_df("""select conversa_id, conversa_criada_em, vendido, reprovado, desqualificado, desistiu, dora_score_pf, dora_faixa_pf, dora_veredito, dora_divida_total_pf, dora_pefin_pf_qtd, dora_protesto_pf_qtd,
                              dora_processos_pf_qtd, dora_score_pj, dora_pj_default, dora_cnae, int_identidade, int_vinculo_maquininha, int_vendas_mensais_valor, int_idade, int_processos_ativos, int_situacao_cpf, inbox_id
                       from analytics.ficha_credito_conversa where tem_dora = 1 or tem_integridade = 1""")
    for c in ("vendido", "reprovado", "desqualificado", "desistiu"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f = f[(f["vendido"] == 1) | (f["reprovado"] == 1) | (f["desqualificado"] == 1)].copy()
    f["y"] = (f["vendido"] == 1).astype(int)
    f["mes"] = pd.to_datetime(f["conversa_criada_em"]).dt.to_period("M")
    print(f"decisao do analista: {len(f)} conversas com relatorio e destino; vendido {f['y'].mean():.2f}", flush=True)
    num = ["dora_score_pf", "dora_divida_total_pf", "dora_pefin_pf_qtd", "dora_protesto_pf_qtd", "dora_processos_pf_qtd", "dora_score_pj", "int_vendas_mensais_valor", "int_idade", "int_processos_ativos"]
    cat = ["dora_faixa_pf", "dora_veredito", "int_identidade", "int_vinculo_maquininha", "int_situacao_cpf", "dora_cnae"]
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    X = f[num].apply(pd.to_numeric, errors="coerce")
    for c in cat:
        X[c] = f[c].astype("category").cat.codes
    ms = sorted(f["mes"].unique())[-4:]
    ys, ps = [], []
    for mes in ms:
        tr, te = f[f["mes"] < mes], f[f["mes"] == mes]
        if len(te) < 30 or len(tr) < 200 or tr["y"].nunique() < 2:
            continue
        m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150, random_state=7).fit(X.loc[tr.index], tr["y"])
        ys += te["y"].tolist()
        ps += m.predict_proba(X.loc[te.index])[:, 1].tolist()
    if ys:
        print(f"AUC da decisao do analista (relatorios -> virou contrato), temporal, n={len(ys)}: {roc_auc_score(ys, ps):.3f}", flush=True)
    for c in ("dora_score_pf", "int_vendas_mensais_valor", "dora_divida_total_pf", "int_idade"):
        z = f[["y", c]].apply(pd.to_numeric, errors="coerce").astype(float).dropna()
        if len(z) > 50 and z["y"].nunique() > 1:
            auc = stats.mannwhitneyu(z.loc[z.y == 1, c], z.loc[z.y == 0, c]).statistic / ((z.y == 1).sum() * (z.y == 0).sum())
            print(f"  {c}: AUC p/ 'virou contrato' {auc:.3f} (n={len(z)})", flush=True)


def interacoes(df, alvo):
    """Pares de variaveis binarizadas: o par explica mais que cada uma? teste de razao de verossimilhanca (logistica com e sem termo de interacao)."""
    import statsmodels.api as sm

    d = df[df[alvo].notna()].copy()
    b = pd.DataFrame({"pix": d["pix_agendado"].astype(float), "jovem": (d["idade_na_contratacao"] < 35).astype(float).where(d["idade_na_contratacao"].notna()),
                      "renov": d["e_renovacao"].astype(float), "cep_vazio": d["cep_vazio"].astype(float), "valor_alto": (d["valor_principal"].astype(float) > 1000).astype(float),
                      "pluggy": (d["provedor_pagamento"] == "PLUGGY").astype(float), "casa_nova": (d["tempo_de_casa_dias"] < 30).astype(float).where(d["tempo_de_casa_dias"].notna()),
                      "ramo95": (d["ramo_cnae_div"] == "95").astype(float), "pagou_bem": d.get("mc_prev_pagou_bem")})
    y = d[alvo].astype(int)
    linhas = []
    cols = list(b.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            a, c = cols[i], cols[j]
            z = pd.concat([b[a], b[c], y.rename("y")], axis=1).dropna()
            if len(z) < 100 or z[a].nunique() < 2 or z[c].nunique() < 2 or z["y"].nunique() < 2:
                continue
            try:
                X0 = sm.add_constant(z[[a, c]])
                X1 = X0.copy()
                X1["x"] = z[a] * z[c]
                if X1["x"].sum() < 8:
                    continue
                m0 = sm.Logit(z["y"], X0).fit(disp=0)
                m1 = sm.Logit(z["y"], X1).fit(disp=0)
                lr = 2 * (m1.llf - m0.llf)
                p = stats.chi2.sf(lr, 1)
                tab = z.groupby([a, c])["y"].agg(["count", "mean"]).round(2)
                linhas.append({"par": f"{a} x {c}", "n": len(z), "p_interacao": float(p), "efeito_interacao": float(m1.params["x"]), "celulas": tab.values.tolist()})
            except Exception:  # noqa
                continue
    r = pd.DataFrame(linhas).sort_values("p_interacao")
    if len(r):
        r["q"] = sc.fdr_bh(r["p_interacao"].values)
    return r


def main():
    import os

    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan6.pkl" if os.environ.get("SO_FINAL") else "/tmp/base_scan4.pkl")
    df["e_renovacao"] = (df["ordem_contrato_cliente"] > 1).astype(float)
    df["cep_vazio"] = df["uf_cep"].isna().astype(float)
    if os.environ.get("SO_FINAL"):
        return final(df, ch, [])
    c = conversa_do_contrato(ch, df)
    print(f"contratos ligados a conversa: {len(c)} de {len(df)}", flush=True)
    for nome, fn in (("funil", lambda: funil(ch, c)), ("aquisicao", lambda: aquisicao(ch, c)), ("atributos_estado_atual", lambda: atributos_estado_atual(c)), ("alvos_alternativos", lambda: alvos_alternativos(ch, df))):
        try:
            x = fn()
            df = df.merge(x, on="contrato_id", how="left")
            print(f"[{nome}] +{x.shape[1] - 1} colunas", flush=True)
        except Exception as e:  # noqa
            import traceback

            traceback.print_exc()
            print(f"[{nome}] FALHOU: {str(e)[:150]}", flush=True)
    df.to_pickle("/tmp/base_scan6.pkl")
    todas = []
    blocos = {"funil": [x for x in df.columns if x.startswith("fn_")], "aquisicao": [x for x in df.columns if x.startswith("aq_")], "atributos_estado_atual(VAZAMENTO POSSIVEL)": [x for x in df.columns if x.startswith("at_")]}
    for bloco, cols in blocos.items():
        num = [x for x in cols if pd.api.types.is_numeric_dtype(df[x])]
        cat = [x for x in cols if x not in num and df[x].nunique() <= 40]
        for a in ("ye_0", "y_calote_30", "y_calote_15"):
            r = sc.testes(df, a, num, cat, min_nivel=10)
            if not len(r):
                print("== " + bloco + " x " + a + ": sem testes validos", flush=True)
                continue
            r["bloco"] = bloco
            todas.append(r)
            sig = r[r["q_fdr"] < 0.10].sort_values("p")
            print(f"\n== {bloco} x {a}: {len(r)} testes; q<0.10: {len(sig)}", flush=True)
            for x in sig.head(8).itertuples():
                print(f"   {x.variavel} [{x.nivel}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} estavel={x.estavel}", flush=True)
            print("   menores p:", [(x.variavel, x.nivel[:14], round(x.p, 4), x.n) for x in r.sort_values("p").head(5).itertuples()], flush=True)
    # alvos alternativos: reaplica o varrimento univariado nas variaveis que mais importam
    cand_num = [x for x in ("pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio", "valor_principal", "mc_prev_pagou_bem", "mc_salto_maior",
                            "f_dora_score_pf", "f_int_vendas_mensais", "rfb_idade_empresa_meses", "tempo_de_casa_dias", "razao_parcela_principal", "taxa_juros_pct", "qtd_parcelas_contratadas") if x in df.columns]
    cand_cat = [x for x in ("provedor_pagamento", "ramo_cnae_div", "segmento", "regiao_fiscal_cpf", "uf_ddd", "sexo", "tipo_pessoa") if x in df.columns]
    for a in ("yalt_nunca_pagou", "yalt_parou_cedo", "yalt_fuga_garantia", "ysev_atraso_max_45", "ysev_pct_aberto_45"):
        if a not in df or df[a].notna().sum() < 100:
            print(f"\n== alvo {a}: sem rotulo suficiente", flush=True)
            continue
        if a.startswith("ysev"):  # continuo: correlacao de Spearman
            lin = []
            for v in cand_num:
                z = df[[v, a]].apply(pd.to_numeric, errors="coerce").dropna()
                if len(z) > 60:
                    rho, p = stats.spearmanr(z[v], z[a])
                    lin.append((v, round(rho, 3), round(p, 4), len(z)))
            print(f"\n== alvo continuo {a}: Spearman (variavel, rho, p, n):", sorted(lin, key=lambda t: t[2])[:8], flush=True)
            continue
        r = sc.testes(df, a, cand_num, cand_cat, min_nivel=10)
        if not len(r):
            continue
        r["bloco"] = "alvo:" + a
        todas.append(r)
        print(f"\n== alvo {a}: n rotulado {int(df[a].notna().sum())}, taxa {df[a].mean():.3f}; q<0.10: {int((r['q_fdr'] < 0.10).sum())}", flush=True)
        for x in r.sort_values("p").head(8).itertuples():
            print(f"   {x.variavel} [{x.nivel[:14]}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} estavel={x.estavel}", flush=True)
    return final(df, ch, todas)


def final(df, ch, todas):
    if todas:
        pd.concat(todas).to_pickle("/tmp/motor_scan4.pkl")
    # confusao: dias entre o 1o contato e o contrato e so renovacao disfarcada?
    for a in ("y_calote_30", "ye_0"):
        z = df[df[a].notna() & df["aq_dias_lead_ate_contrato"].notna()]
        for nome, s in (("so 1o contrato", z[z["ordem_contrato_cliente"] == 1]), ("so renovacoes", z[z["ordem_contrato_cliente"] > 1])):
            if len(s) > 40 and s[a].nunique() > 1:
                auc = stats.mannwhitneyu(s.loc[s[a] == 1, "aq_dias_lead_ate_contrato"], s.loc[s[a] == 0, "aq_dias_lead_ate_contrato"]).statistic / ((s[a] == 1).sum() * (s[a] == 0).sum())
                print(f"  dias lead->contrato x {a} ({nome}): AUC {auc:.3f} n={len(s)}", flush=True)
    # interacoes
    for a in ("ye_0", "y_calote_30"):
        r = interacoes(df, a)
        print(f"\n== interacoes 2 a 2 ({a}): {len(r)} pares; q<0.10: {int((r['q'] < 0.10).sum()) if len(r) else 0}", flush=True)
        for x in r.head(8).itertuples():
            print(f"   {x.par} n={x.n} p_interacao={x.p_interacao:.4f} q={x.q:.3f} efeito={x.efeito_interacao:+.2f}", flush=True)
    print("\n######## DECISAO DO ANALISTA ########", flush=True)
    decisao_analista(ch)
    if todas:
        pd.concat(todas).to_pickle("/tmp/motor_scan4.pkl")


if __name__ == "__main__":
    main()
