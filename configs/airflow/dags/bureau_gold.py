# Gold do bureau: transforma as notas Dora (analise de credito) e DeskData (integridade) do Chatwoot em tabelas analiticas tipadas e comentadas.
#   analytics.dim_relatorio_bureau       1 por nota (relatorio) com tipo, veredito e cobertura da extracao
#   analytics.fato_bureau_dora           campos do relatorio Dora (scores, dividas PF e PJ, processos, cnpj consultado)
#   analytics.fato_bureau_integridade    campos do relatorio de integridade DeskData (identidade, vinculo da maquininha, vendas, processos)
#   analytics.fato_bureau_divida         1 por divida listada (PEFIN, REFIN, protesto, cheque, cobranca) com credor, valor, data
#   analytics.fato_bureau_processo       1 por processo judicial listado
# Reconstrucao completa a cada execucao (~12 mil notas em ~40 s), troca atomica por EXCHANGE TABLES. Sem modelo de linguagem: so regra (bureau_parse).
import re

import numpy as np
import pandas as pd

import bureau_parse as bp
import credit_features as cf

VERSAO_PARSER = "bureau_parse-2026-11-09"
R_ITEM = re.compile(r"R\$\s*([\d\.]+,\d{2})")
R_DATA = re.compile(r"Ocorr[eê]ncia\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})", re.I)
R_CONTRATO = re.compile(r"Contrato\s*([A-Z0-9\.\-]+)", re.I)

DORA_COLS = {
    "cpf": ("Nullable(String)", "CPF do tomador consultado (so digitos)"), "cnpj_consultado": ("Nullable(String)", "CNPJ usado na analise PJ (so digitos)"),
    "cnpj_informado": ("Nullable(String)", "CNPJ informado pelo lead no formulario"), "data_abertura_cnpj": ("Nullable(String)", "data de abertura do CNPJ como aparece no relatorio"),
    "data_nascimento": ("Nullable(String)", "data de nascimento do tomador como aparece no relatorio"), "score_pf": ("Nullable(Int32)", "Score PF (modelo HSPN do bureau)"),
    "faixa_pf": ("Nullable(String)", "faixa do Score PF: A (melhor) a H (pior)"), "taxa_inadimplencia_pf": ("Nullable(Float64)", "taxa de inadimplencia estimada para a faixa PF, em %"),
    "score_pj": ("Nullable(Int32)", "Score PJ (modelo HPJ8)"), "pj_default": ("Nullable(UInt8)", "1 se o relatorio classifica a PJ como DEFAULT"),
    "status_cadastral_pj": ("Nullable(String)", "situacao cadastral do CNPJ (ATIVA, BAIXADA...)"), "cnae": ("Nullable(String)", "CNAE principal (7 digitos)"),
    "cep_cnpj": ("Nullable(String)", "CEP do CNPJ consultado"), "cidade_cnpj": ("Nullable(String)", "cidade do CNPJ consultado"), "uf_cnpj": ("Nullable(String)", "UF do CNPJ consultado"),
    "divida_total_pf": ("Nullable(Float64)", "total de dividas do CPF segundo o bureau, em R$"), "divida_total_pj": ("Nullable(Float64)", "total de dividas do CNPJ segundo o bureau, em R$"),
    "processos_pf_qtd": ("Nullable(Int32)", "processos judiciais PF"), "processos_pj_qtd": ("Nullable(Int32)", "processos judiciais PJ"),
    "cnpj_vinculado_negativado": ("Nullable(UInt8)", "1 se algum CNPJ vinculado ao CPF aparece negativado"), "veredito": ("Nullable(String)", "veredito da IA: NEGATIVO, ALERTA, POSITIVO, ANALISE MANUAL"),
}
for esf in ("pf", "pj"):
    for k, nome in (("pefin", "PEFIN"), ("refin", "REFIN"), ("protesto", "protestos em cartorio"), ("cheque", "cheques sem fundos"), ("cobranca", "registros de cobranca")):
        DORA_COLS[f"{k}_{esf}_qtd"] = ("Nullable(Int32)", f"quantidade de {nome} no {esf.upper()}")
        DORA_COLS[f"{k}_{esf}_valor"] = ("Nullable(Float64)", f"saldo de {nome} no {esf.upper()}, em R$")
INTEG_COLS = {
    "cpf": ("Nullable(String)", "CPF do solicitante (so digitos)"), "identidade": ("Nullable(String)", "veredito de identidade: CONFIRMADO, DIVERGENCIA, IMPOSSIVEL, NOME..."),
    "veredito_integridade": ("Nullable(String)", "veredito geral do relatorio de integridade"), "situacao_cpf": ("Nullable(String)", "situacao cadastral do CPF (REGULAR...)"),
    "data_nascimento": ("Nullable(String)", "data de nascimento"), "idade": ("Nullable(Int32)", "idade em anos na data do relatorio"),
    "vendas_mensais_texto": ("Nullable(String)", "media de vendas mensais como informada"), "vendas_mensais_valor": ("Nullable(Float64)", "valor numerico da media de vendas mensais, em R$"),
    "vendas_mensais_minimo": ("Nullable(UInt8)", "1 se a informacao e um piso ('mais de R$ X')"), "maquininha": ("Nullable(String)", "maquininhas de cartao informadas"),
    "vinculo_maquininha": ("Nullable(String)", "proprio, terceiro, nao_informado ou outro"), "tempo_abertura_cnpj_texto": ("Nullable(String)", "tempo de abertura do CNPJ como informado"),
    "email_cpf": ("Nullable(String)", "e-mail vinculado ao CPF"), "processos_total": ("Nullable(Int32)", "processos judiciais identificados"),
    "processos_ativos": ("Nullable(Int32)", "processos com status ATIVO"), "processos_polo_passivo": ("Nullable(Int32)", "processos em que o solicitante e executado ou reu"),
}


def _t(col, tipo):
    return f"`{col}` {tipo[0]} COMMENT '{tipo[1].replace(chr(39), chr(32))}'"


def itens_divida(txt):
    ta = bp.sem_acento(txt)
    out, esfera, tipo = [], None, None
    for linha in ta.split("\n"):
        l = linha.strip()
        if re.search(r"Dividas do CPF|Dividas PF|SERASA PF", l, re.I):
            esfera = "PF"
        if re.search(r"Dividas do CNPJ|Dividas PJ|SERASA PJ", l, re.I):
            esfera = "PJ"
        m = re.search(r"\b(PEFIN|REFIN)\b|Cartorio|Protesto|Cheques? sem fundo|CCF|cobranca", l, re.I)
        if m and re.search(r"(\d+)\s*(ocorr|registro|cheque|protest)|saldo", l, re.I):
            t = m.group(0).upper()
            tipo = "PEFIN" if "PEFIN" in t else "REFIN" if "REFIN" in t else "PROTESTO" if ("CARTORIO" in t or "PROTESTO" in t) else "CHEQUE" if ("CHEQUE" in t or t == "CCF") else "COBRANCA"
            continue
        v, d = R_ITEM.search(l), R_DATA.search(l)
        if tipo and v and d and not re.search(r"saldo total|total d", l, re.I):
            partes = [p.strip(" .*-") for p in re.split(r"\s[\.\-—]\s|—", re.sub(R_ITEM, "", re.sub(R_DATA, "", l)))]
            partes = [p for p in partes if p and not R_CONTRATO.search(p) and len(p) > 2]
            c = R_CONTRATO.search(l)
            out.append({"esfera": esfera or "", "tipo": tipo, "valor": bp.brl(v.group(1)), "credor": (max(partes, key=len) if partes else "")[:120],
                        "contrato": c.group(1) if c else "", "data_ocorrencia": d.group(1)})
    return out


def processos(txt):
    ta = bp.sem_acento(txt)
    out = []
    for bloco in re.split(r"\*\*Processo \d+\*\*", ta)[1:]:
        g = lambda pat: (re.search(pat, bloco, re.I) or [None, ""])[1].strip()  # noqa: E731
        out.append({"numero": g(r"^\s*:?\s*([\d\.\-]{15,30})"), "papel": g(r"Papel:\s*([A-Z ]+)"), "classe": g(r"Classe/Assunto:\s*([^\n]+)")[:150],
                    "tribunal": g(r"Tribunal/UF:\s*([^\n]+)")[:100], "status": g(r"Status:\s*([A-Z]+)"), "observacao": g(r"Observacao:\s*([^\n]+)")[:250]})
    return out


def construir(ch=None):
    ch = ch or cf.conecta()
    n = ch.query_df("""select id as nota_id, conversation_id as conversa_id, created_at as criado_em, content
                       from trusted_chatwoot.kanban_notes final
                       where content ilike '%DORA%' or content ilike '%INTEGRIDADE%' or content ilike '%DESKDATA%' or content ilike '%VEREDITO%'""")
    conv = ch.query_df("select id as conversa_id, contact_id as contato_id from trusted_chatwoot.conversations final")
    n = n.merge(conv, on="conversa_id", how="left")
    dim, dora, integ, div, proc = [], [], [], [], []
    for r in n.itertuples():
        tipo = bp.classifica(r.content)
        if tipo == "outro":
            continue
        d = bp.parse_dora(r.content) if tipo == "dora" else bp.parse_deskdata(r.content)
        d = {k: v for k, v in d.items() if v is not None and not (isinstance(v, float) and np.isnan(v))}
        cols = DORA_COLS if tipo == "dora" else INTEG_COLS
        veredito = d.get("veredito") if tipo == "dora" else (d.get("identidade") or d.get("veredito_integridade"))
        dim.append({"nota_id": int(r.nota_id), "conversa_id": int(r.conversa_id), "contato_id": 0 if pd.isna(r.contato_id) else int(r.contato_id), "tipo": tipo,
                    "criado_em": r.criado_em, "veredito": veredito or "", "campos_extraidos": sum(1 for c in cols if c in d), "campos_possiveis": len(cols),
                    "tamanho_texto": len(r.content), "versao_parser": VERSAO_PARSER})
        linha = {"nota_id": int(r.nota_id), **{c: d.get(c) for c in cols}}
        (dora if tipo == "dora" else integ).append(linha)
        if tipo == "dora":
            for it in itens_divida(r.content):
                div.append({"nota_id": int(r.nota_id), **it})
        else:
            for it in processos(r.content):
                proc.append({"nota_id": int(r.nota_id), **it})
    return pd.DataFrame(dim), pd.DataFrame(dora), pd.DataFrame(integ), pd.DataFrame(div), pd.DataFrame(proc)


def _gravar(ch, nome, df, ddl_cols, ordem, comentario):
    novo = f"analytics.`{nome}__novo`"
    ch.command(f"DROP TABLE IF EXISTS {novo}")
    ch.command(f"CREATE TABLE {novo} ({', '.join(ddl_cols)}) ENGINE = MergeTree ORDER BY ({ordem}) COMMENT '{comentario}'")
    if len(df):
        ch.insert_df(novo.replace("`", ""), df)
    existe = ch.query(f"select count() from system.tables where database='analytics' and name='{nome}'").result_rows[0][0]
    if existe:
        ch.command(f"EXCHANGE TABLES analytics.`{nome}` AND {novo}")
        ch.command(f"DROP TABLE {novo}")
    else:
        ch.command(f"RENAME TABLE {novo} TO analytics.`{nome}`")


def _ajusta(df, cols):
    df = df.copy()
    for c, (tipo, _) in cols.items():
        if c not in df:
            df[c] = None
        if "Int32" in tipo:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int32")
        elif "Float64" in tipo:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        elif "UInt8" in tipo:
            df[c] = pd.to_numeric(df[c].astype("float64", errors="ignore"), errors="coerce").astype("UInt8")
        else:
            df[c] = df[c].where(df[c].notna(), None)
    return df


def rodar():
    ch = cf.conecta()
    dim, dora, integ, div, proc = construir(ch)
    print(f"notas: {len(dim)} ({dim['tipo'].value_counts().to_dict()}) | dividas: {len(div)} | processos: {len(proc)}", flush=True)
    dim["criado_em"] = pd.to_datetime(dim["criado_em"])
    _gravar(ch, "dim_relatorio_bureau", dim, [
        "`nota_id` UInt64 COMMENT 'id da nota no Chatwoot (kanban_notes.id)'", "`conversa_id` UInt64 COMMENT 'conversa onde a nota foi escrita'",
        "`contato_id` UInt64 COMMENT 'contato do Chatwoot dono da conversa'", "`tipo` LowCardinality(String) COMMENT 'dora (analise de credito) ou integridade (DeskData)'",
        "`criado_em` DateTime64(3, 'UTC') COMMENT 'quando a IA escreveu a nota'", "`veredito` String COMMENT 'dora: NEGATIVO/ALERTA/POSITIVO; integridade: CONFIRMADO/DIVERGENCIA/IMPOSSIVEL...'",
        "`campos_extraidos` UInt16 COMMENT 'quantos campos o parser conseguiu preencher'", "`campos_possiveis` UInt16 COMMENT 'quantos campos o parser tenta extrair deste tipo'",
        "`tamanho_texto` UInt32 COMMENT 'tamanho da nota em caracteres'", "`versao_parser` LowCardinality(String) COMMENT 'versao do parser por regra'"],
        "nota_id", "Dimensao de relatorios de bureau escritos pela IA nas notas do card (Dora e DeskData), com a cobertura da extracao. Chave: nota_id.")
    dora = _ajusta(dora, DORA_COLS)
    _gravar(ch, "fato_bureau_dora", dora, ["`nota_id` UInt64 COMMENT 'nota (dim_relatorio_bureau.nota_id)'"] + [_t(c, t) for c, t in DORA_COLS.items()], "nota_id",
            "Campos extraidos por regra dos relatorios de analise de credito (Dora): scores PF e PJ, dividas por tipo, processos, CNPJ consultado, veredito da IA.")
    integ = _ajusta(integ, INTEG_COLS)
    _gravar(ch, "fato_bureau_integridade", integ, ["`nota_id` UInt64 COMMENT 'nota (dim_relatorio_bureau.nota_id)'"] + [_t(c, t) for c, t in INTEG_COLS.items()], "nota_id",
            "Campos extraidos por regra dos relatorios de integridade (DeskData): identidade, situacao do CPF, vinculo da maquininha, vendas mensais, processos.")
    if len(div):
        div["valor"] = pd.to_numeric(div["valor"], errors="coerce")
    _gravar(ch, "fato_bureau_divida", div, [
        "`nota_id` UInt64 COMMENT 'nota do relatorio Dora'", "`esfera` LowCardinality(String) COMMENT 'PF ou PJ'", "`tipo` LowCardinality(String) COMMENT 'PEFIN, REFIN, PROTESTO, CHEQUE ou COBRANCA'",
        "`valor` Nullable(Float64) COMMENT 'valor da ocorrencia em R$'", "`credor` String COMMENT 'credor ou cartorio como aparece no relatorio'", "`contrato` String COMMENT 'numero do contrato, quando informado'",
        "`data_ocorrencia` String COMMENT 'data da ocorrencia como aparece no relatorio'"], "nota_id, tipo",
        "Uma linha por divida listada nos relatorios Dora (PEFIN, REFIN, protestos, cheques sem fundo, cobrancas).")
    _gravar(ch, "fato_bureau_processo", proc, [
        "`nota_id` UInt64 COMMENT 'nota do relatorio de integridade'", "`numero` String COMMENT 'numero do processo (CNJ)'", "`papel` String COMMENT 'papel do solicitante: EXECUTADO, REU...'",
        "`classe` String COMMENT 'classe e assunto'", "`tribunal` String COMMENT 'tribunal, UF e vara'", "`status` LowCardinality(String) COMMENT 'ATIVO ou ARQUIVADO'",
        "`observacao` String COMMENT 'observacao do relatorio (exequente, eventos)'"], "nota_id",
        "Uma linha por processo judicial listado nos relatorios de integridade (DeskData).")
    print(ch.query_df("select 'dim' t, count() n from analytics.dim_relatorio_bureau union all select 'dora', count() from analytics.fato_bureau_dora union all select 'integ', count() from analytics.fato_bureau_integridade union all select 'divida', count() from analytics.fato_bureau_divida union all select 'processo', count() from analytics.fato_bureau_processo").to_string(), flush=True)


def ficha_credito_conversa(ch):
    """1 linha por conversa: ultimo relatorio Dora + ultimo de integridade + funil + atributos da conversa + cliente do CRM (se ligado)."""
    dc = ", ".join(f"fd.`{c}` AS `dora_{c}`" for c in DORA_COLS)
    ic = ", ".join(f"fi.`{c}` AS `int_{c}`" for c in INTEG_COLS)
    nome = "ficha_credito_conversa"
    novo = f"analytics.`{nome}__novo`"
    ch.command(f"DROP TABLE IF EXISTS {novo}")
    ch.command(f"""
        CREATE TABLE {novo} ENGINE = MergeTree ORDER BY conversa_id
        COMMENT 'Ficha de credito por conversa do Chatwoot: ultimo relatorio Dora e ultimo de integridade (DeskData) ja parseados, estagio do funil, atributos da conversa e, quando ligado, o cliente e o desfecho no CRM. Uma linha por conversa.'
        AS
        WITH d AS (SELECT conversa_id, argMax(nota_id, criado_em) AS nota_dora, argMax(criado_em, criado_em) AS dora_criado_em, count() AS dora_n_versoes
                   FROM analytics.dim_relatorio_bureau WHERE tipo = 'dora' GROUP BY conversa_id),
             i AS (SELECT conversa_id, argMax(nota_id, criado_em) AS nota_int, argMax(criado_em, criado_em) AS int_criado_em, count() AS int_n_versoes
                   FROM analytics.dim_relatorio_bureau WHERE tipo = 'integridade' GROUP BY conversa_id),
             p AS (SELECT contato_id, argMax(cliente_id, confianca) AS cliente_id FROM analytics.ponte_lead_cliente WHERE vinculo_valido = 1 GROUP BY contato_id)
        SELECT c.id AS conversa_id, c.contact_id AS contato_id, c.inbox_id AS inbox_id, c.created_at AS conversa_criada_em, s.name AS estagio_atual,
               (s.name = 'Vendido') AS vendido, (s.name ILIKE '%REPROVADO%') AS reprovado, (s.name ILIKE 'Cliente - DESISTIU%') AS desistiu, (s.name = 'Desqualificado') AS desqualificado,
               isNotNull(d.nota_dora) AS tem_dora, isNotNull(i.nota_int) AS tem_integridade, d.dora_criado_em, d.dora_n_versoes, i.int_criado_em, i.int_n_versoes,
               {dc}, {ic},
               JSONExtractString(c.custom_attributes, 'deskdata_status') AS attr_deskdata_status, JSONExtractString(c.custom_attributes, 'proposta_escolha') AS attr_proposta_escolha,
               JSONExtractString(c.custom_attributes, 'cnpj_ownership_unconfirmed_number') AS attr_cnpj_nao_confirmado,
               JSONExtractBool(c.custom_attributes, 'desativar_ia') AS attr_desativar_ia, JSONExtractBool(c.custom_attributes, 'formulario_whatsapp') AS attr_formulario_whatsapp,
               p.cliente_id AS cliente_id, k.situacao_atual AS crm_situacao_cliente, k.maior_atraso_em_aberto_dias AS crm_maior_atraso_em_aberto_dias, k.qtd_contratos AS crm_qtd_contratos
        FROM trusted_chatwoot.conversations AS c FINAL
        LEFT JOIN trusted_chatwoot.kanban_stages AS s FINAL ON toInt64(s.id) = toInt64(c.kanban_stage_id)
        LEFT JOIN d ON toInt64(d.conversa_id) = toInt64(c.id) LEFT JOIN i ON toInt64(i.conversa_id) = toInt64(c.id)
        LEFT JOIN analytics.fato_bureau_dora AS fd ON fd.nota_id = d.nota_dora LEFT JOIN analytics.fato_bureau_integridade AS fi ON fi.nota_id = i.nota_int
        LEFT JOIN p ON toInt64(p.contato_id) = toInt64(c.contact_id) LEFT JOIN analytics.dim_cliente AS k ON k.cliente_id = p.cliente_id
        SETTINGS join_use_nulls = 1
    """)
    existe = ch.query(f"select count() from system.tables where database='analytics' and name='{nome}'").result_rows[0][0]
    if existe:
        ch.command(f"EXCHANGE TABLES analytics.`{nome}` AND {novo}")
        ch.command(f"DROP TABLE {novo}")
    else:
        ch.command(f"RENAME TABLE {novo} TO analytics.`{nome}`")
    # comentarios de coluna: os do dicionario do bureau (prefixados) para o catalogo ficar 100% descrito
    for pref, cols in (("dora_", DORA_COLS), ("int_", INTEG_COLS)):
        for c, (_, com) in cols.items():
            ch.command(f"ALTER TABLE analytics.`{nome}` COMMENT COLUMN `{pref}{c}` '{com.replace(chr(39), chr(32))} (do ultimo relatorio da conversa)'")
    extras = {"conversa_id": "conversa do Chatwoot", "contato_id": "contato do Chatwoot", "inbox_id": "linha de WhatsApp (inbox)", "conversa_criada_em": "quando a conversa comecou",
              "estagio_atual": "estagio atual no funil de credito (kanban)", "vendido": "1 se a conversa terminou em Vendido (contrato fechado)", "reprovado": "1 se o estagio e de reprovacao",
              "desistiu": "1 se o cliente desistiu", "desqualificado": "1 se foi desqualificado", "tem_dora": "1 se existe relatorio Dora", "tem_integridade": "1 se existe relatorio de integridade",
              "dora_criado_em": "data do ultimo relatorio Dora", "dora_n_versoes": "quantas versoes do relatorio Dora a conversa tem", "int_criado_em": "data do ultimo relatorio de integridade",
              "int_n_versoes": "quantas versoes do relatorio de integridade", "attr_deskdata_status": "falha da consulta DeskData (insufficient_credits, missing_cpf) quando houve",
              "attr_proposta_escolha": "resposta do lead a proposta", "attr_cnpj_nao_confirmado": "CNPJ que o lead nao confirmou ser dele", "attr_desativar_ia": "1 se a IA foi desativada na conversa",
              "attr_formulario_whatsapp": "1 se o lead preencheu o formulario do WhatsApp", "cliente_id": "cliente do CRM ligado ao contato (ponte_lead_cliente, vinculo valido)",
              "crm_situacao_cliente": "situacao do cliente no CRM hoje", "crm_maior_atraso_em_aberto_dias": "maior atraso em aberto do cliente no CRM", "crm_qtd_contratos": "contratos do cliente no CRM"}
    for c, com in extras.items():
        ch.command(f"ALTER TABLE analytics.`{nome}` COMMENT COLUMN `{c}` '{com}'")
    print(ch.query_df(f"select count() conversas, sum(tem_dora) dora, sum(tem_integridade) integ, sum(vendido) vendidos, countIf(cliente_id IS NOT NULL) com_cliente from analytics.{nome}").to_string(), flush=True)


if __name__ == "__main__":
    rodar()
    ficha_credito_conversa(cf.conecta())
