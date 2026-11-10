# Motor de credito: features do CNPJ (Receita Federal) e do quadro societario, mais o historico de pagamento de CLIENTES LIGADOS ao cliente
# (socio que tambem e cliente, socio compartilhado entre clientes, PF dono de outras empresas), sempre COMO ESTAVA no inicio do contrato.
import numpy as np
import pandas as pd

FAIXA_IDADE = {"18 a 30": 1, "31 a 40": 2, "41 a 50": 3, "51 a 60": 4, "61 a 70": 5, "acima de 70": 6, "mais de 70": 6}
PORTE = {"MICRO EMPRESA": 1, "EMPRESA DE PEQUENO PORTE": 2, "DEMAIS": 3}


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def firmografia(ch, base):
    j = ch.query_df("""select cliente_id, cnae_codigo, cnae_divisao, porte_receita, natureza_juridica, capital_social, optante_mei, optante_simples, matriz_ou_filial,
                              uf_receita, municipio_receita, qtd_socios, tem_cnae_secundario, data_abertura
                       from analytics.dim_cnpj_cliente""").drop_duplicates("cliente_id")
    out = base[["contrato_id", "cliente_id", "data_inicio"]].merge(j, on="cliente_id", how="left")
    ab = pd.to_datetime(out["data_abertura"], errors="coerce")
    r = pd.DataFrame({"contrato_id": out["contrato_id"]})
    r["rfb_tem"] = out["cnae_codigo"].notna().astype(float)
    cod = out["cnae_codigo"].astype(str).where(out["cnae_codigo"].notna())
    r["rfb_cnae_div"] = cod.str[:2]
    r["rfb_cnae_grupo"] = cod.str[:3]
    r["rfb_cnae_classe"] = cod.str[:4]
    r["rfb_porte"] = out["porte_receita"]
    r["rfb_natureza"] = out["natureza_juridica"].astype(str).where(out["natureza_juridica"].notna())
    r["rfb_capital_log"] = np.log1p(_num(out["capital_social"]))
    r["rfb_mei"] = out["optante_mei"].astype(float)
    r["rfb_simples"] = out["optante_simples"].astype(float)
    r["rfb_matriz"] = (out["matriz_ou_filial"].astype(str).str.upper().str.startswith("MATRIZ")).astype(float).where(out["matriz_ou_filial"].notna())
    r["rfb_uf"] = out["uf_receita"]
    r["rfb_qtd_socios"] = _num(out["qtd_socios"])
    r["rfb_cnae_sec"] = out["tem_cnae_secundario"].astype(float)
    r["rfb_idade_empresa_meses"] = ((pd.to_datetime(out["data_inicio"]) - ab).dt.days / 30.4).where(ab.notna())
    r["rfb_empresa_nova_6m"] = (r["rfb_idade_empresa_meses"] < 6).astype(float).where(r["rfb_idade_empresa_meses"].notna())
    r["rfb_empresa_nova_12m"] = (r["rfb_idade_empresa_meses"] < 12).astype(float).where(r["rfb_idade_empresa_meses"].notna())
    return r


def _estado_em(parcelas_cliente, quando):
    """Historico de pagamento de um cliente COMO ESTAVA em `quando`: parcelas vencidas ate la, pagas so se pagas ate la."""
    p = parcelas_cliente[parcelas_cliente["venc"] < quando]
    if not len(p):
        return None
    pago = p["pago_em"].notna() & (p["pago_em"] <= quando)
    atraso = np.where(pago, (p["pago_em"] - p["venc"]).dt.days.clip(lower=0), (quando - p["venc"]).dt.days.clip(lower=0))
    return {"n": len(p), "pct_atrasadas": float((atraso > 3).mean()), "atraso_max": float(atraso.max()), "pct_calote": float(((~pago) & (atraso > 30)).mean())}


def socios_e_rede(ch, base, parcelas):
    s = ch.query_df("""select cliente_id, nome_socio, documento_mascarado_socio, qualificacao_socio, data_entrada_sociedade, faixa_etaria_socio, socios_do_cliente
                       from analytics.dim_socio_cliente""")
    rede = ch.query_df("select cliente_id, qtd_empresas_do_socio, qtd_empresas_ativas_do_socio, outros_clientes_com_o_mesmo_socio from analytics.ponte_rede_socios")
    como = ch.query_df("select cliente_id, cnpj_basico, situacao_cadastral from analytics.ponte_cliente_como_socio")
    cnpjs = ch.query_df("select cliente_id as cliente_pj, cnpj_basico from analytics.dim_cnpj_cliente")
    # arestas entre clientes: (a) PF cliente e socio de empresa que tambem e cliente PJ; (b) mesmo socio (nome + documento mascarado) em mais de um cliente
    ar = []
    pj = como.merge(cnpjs, on="cnpj_basico", how="inner")
    ar += [(a, b) for a, b in zip(pj["cliente_id"], pj["cliente_pj"]) if a != b]
    g = s.groupby(["nome_socio", "documento_mascarado_socio"])["cliente_id"].apply(lambda x: sorted(set(x)))
    for lst in g:
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                ar.append((lst[i], lst[j]))
    viz = {}
    for a, b in ar:
        viz.setdefault(a, set()).add(b)
        viz.setdefault(b, set()).add(a)
    ps = parcelas.copy()
    ps["venc"] = pd.to_datetime(ps["data_vencimento"])
    ps["pago_em"] = pd.to_datetime(ps["data_pagamento"])
    por_cli = {k: g_ for k, g_ in ps.groupby("cliente_id")}
    linhas = []
    for r in base[["contrato_id", "cliente_id", "data_inicio"]].itertuples():
        ini = pd.Timestamp(r.data_inicio)
        v = viz.get(r.cliente_id, set())
        est = [e for e in (_estado_em(por_cli[x], ini) for x in v if x in por_cli) if e]
        linhas.append({"contrato_id": r.contrato_id, "r_vinculados": float(len(v)), "r_vinculados_com_historico": float(len(est)),
                       "r_vinc_pct_atrasadas": max([e["pct_atrasadas"] for e in est], default=np.nan),
                       "r_vinc_atraso_max": max([e["atraso_max"] for e in est], default=np.nan),
                       "r_vinc_calote": float(any(e["pct_calote"] >= 0.2 for e in est)) if est else np.nan})
    r = pd.DataFrame(linhas)
    ini = pd.to_datetime(base.set_index("contrato_id").loc[r["contrato_id"], "data_inicio"].values)
    s1 = s.groupby("cliente_id").agg(r_socio_faixa_idade=("faixa_etaria_socio", lambda x: FAIXA_IDADE.get(str(x.iloc[0]).lower(), np.nan)),
                                      r_socio_qualif_admin=("qualificacao_socio", lambda x: float("dmin" in str(x.iloc[0]).lower() or "dministrador" in str(x.iloc[0]))),
                                      r_socio_entrada=("data_entrada_sociedade", "min")).reset_index()
    out = base[["contrato_id", "cliente_id"]].merge(s1, on="cliente_id", how="left").merge(
        rede.groupby("cliente_id").agg(r_socio_n_empresas=("qtd_empresas_do_socio", "max"), r_socio_n_empresas_ativas=("qtd_empresas_ativas_do_socio", "max"),
                                       r_socio_outros_clientes=("outros_clientes_com_o_mesmo_socio", "max")).reset_index(), on="cliente_id", how="left")
    ent = pd.to_datetime(out["r_socio_entrada"], errors="coerce")
    out["r_socio_meses_na_sociedade"] = ((pd.to_datetime(base.set_index("contrato_id").loc[out["contrato_id"], "data_inicio"].values) - ent).dt.days / 30.4).where(ent.notna()).values
    out = out.drop(columns=["r_socio_entrada", "cliente_id"])
    pfdono = como.groupby("cliente_id").agg(r_pf_dono_de_empresas=("cnpj_basico", "nunique"), r_pf_dono_empresas_ativas=("situacao_cadastral", lambda x: int((x == "ativa").sum()))).reset_index()
    out = out.merge(base[["contrato_id", "cliente_id"]], on="contrato_id").merge(pfdono, on="cliente_id", how="left").drop(columns=["cliente_id"])
    return r.merge(out, on="contrato_id", how="left")


def features_cnpj_rede(ch, base, parcelas):
    return firmografia(ch, base).merge(socios_e_rede(ch, base, parcelas), on="contrato_id", how="left")
