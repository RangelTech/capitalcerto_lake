# Motor de credito, parte 2: features que vem do Chatwoot (relatorios Dora e DeskData escritos pela IA nas notas do card, mais o
# comportamento do lead na conversa), ligadas ao CONTRATO so com o que existia ate 1 dia depois do inicio dele (sem vazamento).
import os

import numpy as np
import pandas as pd

import bureau_parse as bp

FAIXA_ORD = {c: i + 1 for i, c in enumerate("ABCDEFGH")}
CUTOFF_DIAS = 1


def ler_e_parsear_notas(ch, desde_id=0):
    n = ch.query_df(f"""
        select id as nota_id, conversation_id as conversa_id, created_at as criado_em, content
        from trusted_chatwoot.kanban_notes final
        where id > {int(desde_id)} and (content ilike '%DORA%' or content ilike '%INTEGRIDADE%' or content ilike '%DESKDATA%' or content ilike '%VEREDITO%')
    """)
    linhas = []
    for r in n.itertuples():
        tipo = bp.classifica(r.content)
        if tipo == "dora":
            d = bp.parse_dora(r.content)
        elif tipo == "integridade":
            d = bp.parse_deskdata(r.content)
        else:
            continue
        d.update(nota_id=r.nota_id, conversa_id=r.conversa_id, criado_em=r.criado_em, tipo=tipo)
        linhas.append(d)
    return pd.DataFrame(linhas)


def _num(s):
    return pd.to_numeric(s, errors="coerce")


def _dora_features(d):
    f = pd.DataFrame({"conversa_id": d["conversa_id"], "criado_em": d["criado_em"]})
    f["f_dora_score_pf"] = _num(d.get("score_pf"))
    f["f_dora_faixa_pf_ord"] = d.get("faixa_pf").map(FAIXA_ORD) if "faixa_pf" in d else np.nan
    f["f_dora_taxa_inad_pf"] = _num(d.get("taxa_inadimplencia_pf"))
    f["f_dora_score_pj"] = _num(d.get("score_pj"))
    f["f_dora_pj_default"] = d.get("pj_default").astype("float") if "pj_default" in d else np.nan
    f["f_dora_pj_ativa"] = d.get("status_cadastral_pj").map(lambda s: np.nan if s is None or s != s else float(str(s).startswith("ATIV"))) if "status_cadastral_pj" in d else np.nan
    for esf in ("pf", "pj"):
        for k in ("pefin", "refin", "protesto", "cobranca", "cheque"):
            f[f"f_dora_{k}_{esf}_qtd"] = _num(d.get(f"{k}_{esf}_qtd"))
            f[f"f_dora_{k}_{esf}_valor"] = _num(d.get(f"{k}_{esf}_valor"))
        comp = sum(f[f"f_dora_{k}_{esf}_valor"].fillna(0) for k in ("pefin", "refin", "protesto", "cobranca", "cheque"))
        tot = _num(d.get(f"divida_total_{esf}"))
        f[f"f_dora_divida_total_{esf}"] = tot.where(tot.notna(), comp.where(f[f"f_dora_pefin_{esf}_valor"].notna() | f[f"f_dora_refin_{esf}_valor"].notna()))
        f[f"f_dora_tem_divida_{esf}"] = (f[f"f_dora_divida_total_{esf}"] > 0).astype(float).where(f[f"f_dora_divida_total_{esf}"].notna())
    f["f_dora_divida_pf_acima_50k"] = (f["f_dora_divida_total_pf"] > 50000).astype(float).where(f["f_dora_divida_total_pf"].notna())
    f["f_dora_processos_pf"] = _num(d.get("processos_pf_qtd"))
    f["f_dora_processos_pj"] = _num(d.get("processos_pj_qtd"))
    f["f_dora_cnpj_vinc_negativado"] = d.get("cnpj_vinculado_negativado").astype("float") if "cnpj_vinculado_negativado" in d else np.nan
    f["f_dora_cnae_div"] = d.get("cnae").astype(str).str[:2].where(d.get("cnae").notna()) if "cnae" in d else None
    f["f_dora_uf_cnpj"] = d.get("uf_cnpj") if "uf_cnpj" in d else None
    f["f_dora_veredito"] = d.get("veredito")
    f["f_dora_abertura_cnpj"] = pd.to_datetime(d.get("data_abertura_cnpj"), errors="coerce") if "data_abertura_cnpj" in d else pd.NaT
    f["f_dora_nascimento"] = pd.to_datetime(d.get("data_nascimento"), errors="coerce", dayfirst=False) if "data_nascimento" in d else pd.NaT
    return f


def _integridade_features(d):
    f = pd.DataFrame({"conversa_id": d["conversa_id"], "criado_em": d["criado_em"]})
    f["f_int_identidade"] = d.get("identidade")
    f["f_int_veredito"] = d.get("veredito_integridade")
    f["f_int_vinculo_maquininha"] = d.get("vinculo_maquininha")
    f["f_int_maquininha"] = d.get("maquininha").astype(str).str.upper().str.extract(r"(TON|STONE|PAGBANK|PAGSEGURO|INFINITE|MERCADO|CIELO|REDE|SUMUP|GETNET|SICREDI)", expand=False) if "maquininha" in d else None
    f["f_int_vendas_mensais"] = _num(d.get("vendas_mensais_valor"))
    f["f_int_vendas_minimo"] = d.get("vendas_mensais_minimo").astype("float") if "vendas_mensais_minimo" in d else np.nan
    f["f_int_processos_total"] = _num(d.get("processos_total"))
    f["f_int_processos_ativos"] = _num(d.get("processos_ativos"))
    f["f_int_processos_passivo"] = _num(d.get("processos_polo_passivo"))
    f["f_int_cpf_regular"] = d.get("situacao_cpf").map(lambda s: np.nan if s is None or s != s else float(str(s).startswith("REGULAR"))) if "situacao_cpf" in d else np.nan
    f["f_int_idade"] = _num(d.get("idade"))
    return f


def _ultima_antes(c, notas, cols, cutoff_col="cutoff"):
    """Para cada contrato, a nota mais recente da conversa do cliente criada ate o cutoff."""
    m = c.merge(notas, on="conversa_id", how="inner")
    m = m[m["criado_em"] <= m[cutoff_col]]
    m = m.sort_values("criado_em").groupby("contrato_id").tail(1)
    return m[["contrato_id", "criado_em"] + cols]


def ficha_por_contrato(ch, base, notas=None):
    """`base`: DataFrame de credit_features.montar(). Devolve contrato_id + colunas f_* (NaN onde nao havia dado na contratacao)."""
    notas = notas if notas is not None else ler_e_parsear_notas(ch)
    pon = ch.query_df("select cliente_id, contato_id from analytics.ponte_lead_cliente where vinculo_valido = 1")
    conv = ch.query_df("select id as conversa_id, contact_id as contato_id, inbox_id, created_at as conversa_criada_em from trusted_chatwoot.conversations final")
    c = base[["contrato_id", "cliente_id", "data_inicio"]].merge(pon, on="cliente_id", how="inner").merge(conv, on="contato_id", how="inner")
    c["cutoff"] = pd.to_datetime(c["data_inicio"]) + pd.Timedelta(days=CUTOFF_DIAS)
    c["criado_em"] = None
    out = base[["contrato_id"]].copy()
    d = notas[notas["tipo"] == "dora"]
    if len(d):
        fd = _dora_features(d.reset_index(drop=True))
        cols = [x for x in fd.columns if x.startswith("f_")]
        u = _ultima_antes(c.drop(columns=["criado_em"]), fd, cols)
        u["f_dora_dias_nota_ate_contrato"] = (pd.Series(pd.to_datetime(base.set_index("contrato_id").loc[u["contrato_id"], "data_inicio"].values), index=u.index) - pd.to_datetime(u["criado_em"])).dt.days
        u = u.drop(columns=["criado_em"])
        out = out.merge(u, on="contrato_id", how="left")
        out["f_tem_dora"] = out["f_dora_score_pf"].notna() | out["f_dora_veredito"].notna()
        ini = pd.to_datetime(base.set_index("contrato_id").loc[out["contrato_id"], "data_inicio"].values)
        out["f_dora_idade_cnpj_meses"] = ((ini - out["f_dora_abertura_cnpj"]).dt.days / 30.4).where(out["f_dora_abertura_cnpj"].notna())
        out["f_dora_idade_nota"] = ((ini - out["f_dora_nascimento"]).dt.days / 365.25).where(out["f_dora_nascimento"].notna())
        out = out.drop(columns=["f_dora_abertura_cnpj", "f_dora_nascimento"])
    i = notas[notas["tipo"] == "integridade"]
    if len(i):
        fi = _integridade_features(i.reset_index(drop=True))
        cols = [x for x in fi.columns if x.startswith("f_")]
        u = _ultima_antes(c.drop(columns=["criado_em"]), fi, cols).drop(columns=["criado_em"])
        out = out.merge(u, on="contrato_id", how="left")
        out["f_tem_integridade"] = out["f_int_identidade"].notna() | out["f_int_veredito"].notna()
    # comportamento do lead na conversa, so ate o cutoff
    ids = c["conversa_id"].unique().tolist()
    if ids:
        msgs = ch.query_df(f"""
            select conversation_id as conversa_id, created_at as quando, message_type as tipo, length(content) as tam
            from trusted_chatwoot.messages final where conversation_id in ({",".join(str(int(x)) for x in ids)}) and private = 0
        """)
        m = c.drop(columns=["criado_em"]).merge(msgs, on="conversa_id", how="inner")
        m = m[m["quando"] <= m["cutoff"]]
        g = m.groupby("contrato_id")
        agg = pd.DataFrame({
            "f_chat_msgs_cliente": g.apply(lambda x: int((x["tipo"] == 0).sum())),
            "f_chat_msgs_empresa": g.apply(lambda x: int((x["tipo"] == 1).sum())),
            "f_chat_tam_medio_cliente": g.apply(lambda x: x.loc[x["tipo"] == 0, "tam"].mean()),
            "f_chat_primeira_msg": g["quando"].min(),
        }).reset_index()
        agg = agg.merge(base[["contrato_id", "data_inicio"]], on="contrato_id")
        agg["f_chat_dias_lead_ate_contrato"] = (pd.to_datetime(agg["data_inicio"]) - pd.to_datetime(agg["f_chat_primeira_msg"])).dt.days
        out = out.merge(agg.drop(columns=["data_inicio", "f_chat_primeira_msg"]), on="contrato_id", how="left")
        ib = c.sort_values("conversa_criada_em").groupby("contrato_id").tail(1)[["contrato_id", "inbox_id"]].rename(columns={"inbox_id": "f_chat_inbox"})
        ib["f_chat_inbox"] = ib["f_chat_inbox"].astype(str)
        out = out.merge(ib, on="contrato_id", how="left")
        out["f_tem_conversa"] = out["f_chat_msgs_cliente"].notna()
    for col in ("f_tem_dora", "f_tem_integridade", "f_tem_conversa"):
        if col in out:
            out[col] = out[col].fillna(False).astype(float)
    return out
