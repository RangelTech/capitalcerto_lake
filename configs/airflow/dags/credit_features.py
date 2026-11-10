# Motor de credito da Capital Certo, parte 1: monta a tabela de features por CONTRATO (so com o que se sabia na contratacao
# ou nos primeiros dias) e os rotulos de inadimplencia por horizonte. Sem modelo de linguagem; tudo regra e SQL.
#
# Regras que evitam vazamento (o modelo nao pode ver o futuro):
#   * nenhuma coluna de desfecho entra como feature (status, classificacao_problema, etiquetas, situacao_operacional);
#   * historico do cliente (contratos anteriores) so conta parcelas com vencimento ANTES do inicio do contrato;
#   * nota de bureau/integridade do Chatwoot so conta se criada ate 1 dia depois do inicio do contrato;
#   * comportamento dos primeiros dias (estagio B) so olha parcelas com vencimento ate D+k.
import os
import re

import numpy as np
import pandas as pd

HORIZONTES = (15, 30, 45)        # dias depois do inicio do contrato; as parcelas sao diarias (60 por contrato)
LIMIAR_ATRASO_DIAS = 3           # parcela "atrasada" = paga com mais de 3 dias de atraso, ou vencida e ainda aberta
LIMIAR_PCT = 0.30                # contrato "ruim" no horizonte H = 30% ou mais das parcelas vencidas ate H atrasadas
LIMIAR_CALOTE = 0.20            # calote = 20% ou mais das parcelas do periodo ainda em aberto e vencidas
CURA_DIAS = 30                  # prazo extra para o cliente regularizar antes de contar como calote
ETAPAS = (0, 1, 2, 3, 5, 7, 10, 14)  # idade do contrato (dias) em que o motor pontua: 0 = na contratacao
HORIZONTE_ALVO = 45              # o alvo de cada etapa k olha as parcelas com vencimento entre k+1 e 45 dias
MATURIDADE_EXTRA_DIAS = 7        # so rotula contrato cujo horizonte fechou ha pelo menos isto (deixa o pagamento tardio entrar)

# Regiao fiscal do CPF (9o digito): onde o CPF foi emitido pela primeira vez (proxy de origem, nao e naturalidade)
REGIAO_FISCAL = {"1": "DF/GO/MS/MT/TO", "2": "AC/AP/AM/PA/RO/RR", "3": "CE/MA/PI", "4": "AL/PB/PE/RN", "5": "BA/SE",
                 "6": "MG", "7": "ES/RJ", "8": "SP", "9": "PR/SC", "0": "RS"}
DDD_UF = {}
for uf, ddds in {"SP": "11 12 13 14 15 16 17 18 19", "RJ": "21 22 24", "ES": "27 28", "MG": "31 32 33 34 35 37 38",
                 "PR": "41 42 43 44 45 46", "SC": "47 48 49", "RS": "51 53 54 55", "DF": "61", "GO": "62 64", "TO": "63",
                 "MT": "65 66", "MS": "67", "AC": "68", "RO": "69", "BA": "71 73 74 75 77", "SE": "79", "PE": "81 87",
                 "AL": "82", "PB": "83", "RN": "84", "CE": "85 88", "PI": "86 89", "PA": "91 93 94", "AM": "92 97",
                 "RR": "95", "AP": "96", "MA": "98 99"}.items():
    for d in ddds.split():
        DDD_UF[d] = uf
CEP_UF = [(1000, 19999, "SP"), (20000, 28999, "RJ"), (29000, 29999, "ES"), (30000, 39999, "MG"), (40000, 48999, "BA"),
          (49000, 49999, "SE"), (50000, 56999, "PE"), (57000, 57999, "AL"), (58000, 58999, "PB"), (59000, 59999, "RN"),
          (60000, 63999, "CE"), (64000, 64999, "PI"), (65000, 65999, "MA"), (66000, 68899, "PA"), (68900, 68999, "AP"),
          (69000, 69299, "AM"), (69300, 69399, "RR"), (69400, 69899, "AM"), (69900, 69999, "AC"), (70000, 72799, "DF"),
          (72800, 72999, "GO"), (73000, 73699, "DF"), (73700, 76799, "GO"), (76800, 76999, "RO"), (77000, 77995, "TO"),
          (78000, 78899, "MT"), (79000, 79999, "MS"), (80000, 87999, "PR"), (88000, 89999, "SC"), (90000, 99999, "RS")]


def cep_para_uf(cep):
    d = re.sub(r"\D", "", cep or "")
    if len(d) != 8:
        return None
    n = int(d[:5])
    for a, b, uf in CEP_UF:
        if a <= n <= b:
            return uf
    return None


def _cpf_regiao(doc):
    d = re.sub(r"\D", "", doc or "")
    return REGIAO_FISCAL.get(d[8]) if len(d) == 11 else None


def conecta():
    import clickhouse_connect

    return clickhouse_connect.get_client(host=os.environ["CLICKHOUSE_HOST"], password=os.environ["CLICKHOUSE_PASSWORD"])


def _contratos(ch):
    df = ch.query_df("""
        select c.contrato_id, c.cliente_id, c.pessoa_id, c.tenant_id, c.data_inicio, c.valor_principal, c.valor_parcela,
               c.qtd_parcelas_contratadas, c.taxa_juros_pct, c.ordem_contrato_cliente, c.valor_contrato_anterior, c.salto_valor,
               c.razao_valor_vs_anterior, c.dias_desde_quitacao_anterior, c.maior_salto_valor_historico,
               d.provedor_pagamento, d.pix_agendado, d.intervalo_parcelas,
               k.tipo_pessoa, k.segmento, k.sexo, k.tipo_empresa, k.renda_declarada,
               if(k.data_nascimento > toDate32('1920-01-01') and k.data_nascimento < today(), k.data_nascimento, NULL) as data_nascimento,
               k.cidade, k.uf,
               if(k.cliente_desde > toDate32('2000-01-01'), k.cliente_desde, NULL) as cliente_desde,
               k.documento_final
        from analytics.fato_contrato c
        join analytics.dim_contrato d on d.contrato_id = c.contrato_id
        join analytics.dim_cliente k on k.cliente_id = c.cliente_id
        where c.empresa = 'Capital Certo' and d.excluido = 0 and c.data_inicio is not null and c.data_inicio > toDate('2000-01-01')
    """)
    df.columns = [c.split(".")[-1] for c in df.columns]  # o ClickHouse prefixa com o alias da tabela quando o nome e ambiguo
    return df


def _parcelas(ch):
    return ch.query_df("""
        select contrato_id, cliente_id, numero_parcela, data_vencimento, data_pagamento, status, dias_atraso_ate_pagar,
               dias_atraso_em_aberto, vencida_em_aberto, valor_total, valor_pago
        from analytics.fato_parcela where empresa = 'Capital Certo' and contrato_excluido = 0 and data_vencimento > toDate('2000-01-01')
    """)


def _atraso_parcela(p, hoje):
    """Dias de atraso de cada parcela: paga = dias ate pagar; aberta e vencida = dias desde o vencimento ate hoje; senao 0."""
    pago = p["data_pagamento"].notna()
    atraso = np.where(pago, p["dias_atraso_ate_pagar"].fillna(0), 0).astype(float)
    venc = pd.to_datetime(p["data_vencimento"])
    aberta_vencida = (~pago) & (venc < hoje)
    atraso = np.where(aberta_vencida, (hoje - venc).dt.days, atraso)
    return np.clip(atraso, 0, None), aberta_vencida.to_numpy()


def rotulos_e_comportamento(contratos, parcelas, hoje=None):
    """Para cada contrato: rotulos por horizonte e comportamento dos primeiros dias (estagio B)."""
    hoje = pd.Timestamp(hoje or pd.Timestamp.now().normalize())
    p = parcelas.merge(contratos[["contrato_id", "data_inicio"]], on="contrato_id", how="inner")
    p["data_inicio"] = pd.to_datetime(p["data_inicio"])
    p["venc"] = pd.to_datetime(p["data_vencimento"])
    p["atraso"], p["aberta_vencida"] = _atraso_parcela(p, hoje)
    p["dia"] = (p["venc"] - p["data_inicio"]).dt.days
    p["atrasada"] = (p["atraso"] > LIMIAR_ATRASO_DIAS)
    saida = []
    for cid, g in p.groupby("contrato_id"):
        ini = g["data_inicio"].iloc[0]
        r = {"contrato_id": cid}
        for H in HORIZONTES:
            gh = g[g["dia"] <= H]
            r[f"n_parcelas_ate_{H}"] = len(gh)
            r[f"pct_atrasadas_{H}"] = gh["atrasada"].mean() if len(gh) else np.nan
            r[f"atraso_max_{H}"] = gh["atraso"].max() if len(gh) else np.nan
            maduro = (ini + pd.Timedelta(days=H + MATURIDADE_EXTRA_DIAS)) <= hoje and len(gh) >= max(5, int(H * 0.4))
            r[f"maduro_{H}"] = bool(maduro)
            r[f"y_{H}"] = (1 if r[f"pct_atrasadas_{H}"] >= LIMIAR_PCT else 0) if maduro else np.nan
            # calote efetivo: parcela do periodo que continua em aberto e vencida HOJE (so rotula se deu tempo de pagar depois: +30 dias)
            r[f"pct_calote_{H}"] = gh["aberta_vencida"].mean() if len(gh) else np.nan
            maduro_c = (ini + pd.Timedelta(days=H + CURA_DIAS)) <= hoje and len(gh) >= max(5, int(H * 0.4))
            r[f"y_calote_{H}"] = (1 if r[f"pct_calote_{H}"] >= LIMIAR_CALOTE else 0) if maduro_c else np.nan
        # comportamento dos primeiros dias, conhecido ate D+k (estagio B do motor): so parcelas com vencimento ate D+k e ja vencidas
        for k in (3, 7):
            gk = g[(g["dia"] <= k) & (g["venc"] < hoje)]
            r[f"b{k}_n"] = len(gk)
            r[f"b{k}_pct_pagas_no_prazo"] = (gk["atraso"] <= 0).mean() if len(gk) else np.nan
            r[f"b{k}_pct_atrasadas"] = gk["atrasada"].mean() if len(gk) else np.nan
            r[f"b{k}_atraso_max"] = gk["atraso"].max() if len(gk) else np.nan
            r[f"b{k}_pct_abertas"] = gk["aberta_vencida"].mean() if len(gk) else np.nan
        saida.append(r)
    return pd.DataFrame(saida)


def etapas_motor(contratos, parcelas, hoje=None):
    """Para cada contrato e cada etapa k: comportamento COMO ESTAVA em D+k (so pagamentos feitos ate D+k) e o rotulo medido
    SO nas parcelas com vencimento depois de D+k (ate HORIZONTE_ALVO), olhando o estado de hoje. Sem vazamento."""
    hoje = pd.Timestamp(hoje or pd.Timestamp.now().normalize())
    p = parcelas.merge(contratos[["contrato_id", "data_inicio"]], on="contrato_id", how="inner")
    p["ini"] = pd.to_datetime(p["data_inicio"])
    p["venc"] = pd.to_datetime(p["data_vencimento"])
    p["pago_em"] = pd.to_datetime(p["data_pagamento"])
    p["dia"] = (p["venc"] - p["ini"]).dt.days
    p["aberta_hoje"] = p["pago_em"].isna() & (p["venc"] < hoje)
    saida = []
    for cid, g in p.groupby("contrato_id"):
        ini = g["ini"].iloc[0]
        r = {"contrato_id": cid}
        for k in ETAPAS:
            if k > 0:
                asof = ini + pd.Timedelta(days=k)
                due = g[g["dia"] <= k]
                if len(due):
                    pago = due["pago_em"].notna() & (due["pago_em"] <= asof)
                    atraso = np.where(pago, (due["pago_em"] - due["venc"]).dt.days.clip(lower=0), (asof - due["venc"]).dt.days.clip(lower=0))
                    r[f"e{k}_n_vencidas"] = len(due)
                    r[f"e{k}_pct_no_prazo"] = float((pago & (due["pago_em"] <= due["venc"])).mean())
                    r[f"e{k}_pct_atrasadas"] = float((atraso > LIMIAR_ATRASO_DIAS).mean())
                    r[f"e{k}_pct_abertas"] = float((~pago).mean())
                    r[f"e{k}_atraso_max"] = float(np.max(atraso))
                    r[f"e{k}_atraso_medio"] = float(np.mean(atraso))
                    prim = due[due["numero_parcela"] == due["numero_parcela"].min()]
                    r[f"e{k}_primeira_paga_no_prazo"] = float(((prim["pago_em"].notna()) & (prim["pago_em"] <= prim["venc"])).iloc[0])
            fut = g[(g["dia"] > k) & (g["dia"] <= HORIZONTE_ALVO)]
            maduro = (ini + pd.Timedelta(days=HORIZONTE_ALVO + CURA_DIAS) <= hoje) and len(fut) >= 10
            r[f"ye_{k}"] = (1.0 if fut["aberta_hoje"].mean() >= LIMIAR_CALOTE else 0.0) if maduro else np.nan
            # inadimplencia (atraso): >= 30% das parcelas futuras pagas com mais de 3 dias de atraso ou ainda em aberto, estado de hoje; amadurece 7 dias apos o horizonte
            maduro_i = (ini + pd.Timedelta(days=HORIZONTE_ALVO + MATURIDADE_EXTRA_DIAS) <= hoje) and len(fut) >= 10
            if maduro_i:
                at_f = np.where(fut["pago_em"].notna(), (fut["pago_em"] - fut["venc"]).dt.days.clip(lower=0), (hoje - fut["venc"]).dt.days.clip(lower=0))
                r[f"yi_{k}"] = float((at_f > LIMIAR_ATRASO_DIAS).mean() >= LIMIAR_PCT)
            else:
                r[f"yi_{k}"] = np.nan
        saida.append(r)
    return pd.DataFrame(saida)


def cluster_trajetoria(parcelas, contratos, hoje=None):
    """Cluster de INADIMPLENCIA de cada contrato MADURO (D+1..D+45 + 30 dias de cura): k-means sobre a trajetoria de pagamento, com nomes deterministicos pelo centroide.
    Devolve contrato_id, cluster (nome), cluster_ruim ('paga no comeco e para' ou 'quase nao paga') e as metricas da trajetoria."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    hoje = pd.Timestamp(hoje or pd.Timestamp.now().normalize())
    p = parcelas.merge(contratos[["contrato_id", "data_inicio"]], on="contrato_id", how="inner")
    p["venc"] = pd.to_datetime(p["data_vencimento"])
    p["pago_em"] = pd.to_datetime(p["data_pagamento"])
    p["ini"] = pd.to_datetime(p["data_inicio"])
    p["dia"] = (p["venc"] - p["ini"]).dt.days
    linhas = []
    for cid, g in p.groupby("contrato_id"):
        ini = g["ini"].iloc[0]
        if ini + pd.Timedelta(days=HORIZONTE_ALVO + CURA_DIAS) > hoje:
            continue
        g = g[(g["dia"] >= 1) & (g["dia"] <= HORIZONTE_ALVO)].sort_values("venc")
        if len(g) < 15:
            continue
        pago = g["pago_em"].notna().to_numpy()
        atraso = np.where(pago, (g["pago_em"] - g["venc"]).dt.days.clip(lower=0), (hoje - g["venc"]).dt.days.clip(lower=0))
        n, met = len(g), len(g) // 2
        idx = np.where(pago)[0]
        linhas.append({"contrato_id": cid, "tj_n": n, "tj_pct_no_prazo": float((pago & (atraso <= 0)).mean()), "tj_pct_paga_atrasada": float((pago & (atraso > 3)).mean()), "tj_pct_aberta": float((~pago).mean()),
                       "tj_atraso_medio": float(np.mean(np.where(pago, atraso, 0))), "tj_atraso_max_pagas": float(np.max(np.where(pago, atraso, 0))), "tj_pagou_1a_metade": float(pago[:met].mean()),
                       "tj_pagou_2a_metade": float(pago[met:].mean()), "tj_ultima_paga_frac": float((idx.max() + 1) / n) if len(idx) else 0.0})
    tj = pd.DataFrame(linhas)
    if len(tj) < 40:
        return tj.assign(cluster=None, cluster_ruim=np.nan)
    cols = [c for c in tj.columns if c.startswith("tj_") and c != "tj_n"]
    X = StandardScaler().fit_transform(tj[cols])
    melhor = None
    for k in (3, 4, 5, 6):
        km = KMeans(n_clusters=k, n_init=20, random_state=7).fit(X)
        s = silhouette_score(X, km.labels_)
        if melhor is None or s > melhor[0] + 0.01:
            melhor = (s, k, km.labels_)
    tj["cluster_id"] = melhor[2]
    cent = tj.groupby("cluster_id")[cols].mean()

    def rot(c):
        if c["tj_pct_aberta"] >= 0.60:
            return "paga no comeco e para" if c["tj_pagou_1a_metade"] >= 0.4 else "quase nao paga"
        if c["tj_pct_aberta"] >= 0.30:
            return "paga so parte"
        if c["tj_atraso_medio"] >= 30:
            return "paga com atraso longo (mais de 30 dias)"
        if c["tj_pct_no_prazo"] >= 0.6 and c["tj_atraso_medio"] < 4:
            return "paga em dia"
        return "paga com atraso moderado (~10 dias)"

    nomes, vistos = {i: rot(cent.loc[i]) for i in cent.index}, {}
    for i, nm in list(nomes.items()):
        vistos[nm] = vistos.get(nm, 0) + 1
        if vistos[nm] > 1:
            nomes[i] = f"{nm} ({vistos[nm]})"
    tj["cluster"] = tj["cluster_id"].map(nomes)
    tj["cluster_ruim"] = tj["cluster"].map(lambda c: float(str(c).startswith(("paga no comeco", "quase nao paga"))))
    tj.attrs["silhouette"] = melhor[0]
    return tj


def historico_anterior(contratos, parcelas, hoje=None):
    """Comportamento do cliente nos contratos ANTERIORES, so com parcelas vencidas antes do inicio do contrato atual."""
    hoje = pd.Timestamp(hoje or pd.Timestamp.now().normalize())
    p = parcelas.copy()
    p["venc"] = pd.to_datetime(p["data_vencimento"])
    p["atraso"], _ = _atraso_parcela(p, hoje)
    p["atrasada"] = p["atraso"] > LIMIAR_ATRASO_DIAS
    por_cliente = {k: g for k, g in p.groupby("cliente_id")}
    linhas = []
    for r in contratos.itertuples():
        g = por_cliente.get(r.cliente_id)
        out = {"contrato_id": r.contrato_id, "prev_n_parcelas": 0, "prev_pct_atrasadas": np.nan, "prev_atraso_max": np.nan, "prev_contratos": 0}
        if g is not None:
            ini = pd.Timestamp(r.data_inicio)
            ant = g[(g["contrato_id"] != r.contrato_id) & (g["venc"] < ini)]
            if len(ant):
                out.update(prev_n_parcelas=len(ant), prev_pct_atrasadas=ant["atrasada"].mean(), prev_atraso_max=ant["atraso"].max(),
                           prev_contratos=ant["contrato_id"].nunique())
        linhas.append(out)
    return pd.DataFrame(linhas)


def geografia(ch, contratos):
    """UF por regiao fiscal do CPF, DDD, CEP e (no CRM) cidade/UF informadas."""
    cli = ch.query_df("""
        select c.id as cliente_id, c.cpf_cnpj, c.responsible_cpf, c.phone, a.zipcode, a.state, a.city
        from trusted_credhub.customer c
        left join (select entity_id, argMax(zipcode, updated_at) zipcode, argMax(state, updated_at) state, argMax(city, updated_at) city
                   from trusted_credhub.addresses where entity_type = 'CUSTOMER' group by entity_id) a on a.entity_id = c.id
    """)
    cli["cpf_ref"] = np.where(cli["cpf_cnpj"].str.len() == 11, cli["cpf_cnpj"], cli["responsible_cpf"].fillna(""))
    cli["regiao_fiscal_cpf"] = cli["cpf_ref"].map(_cpf_regiao)
    tel = cli["phone"].fillna("").str.replace(r"\D", "", regex=True)
    cli["ddd"] = tel.where(tel.str.len() >= 10, "").str[-11:-9].where(tel.str.len() >= 10, "")
    cli["ddd"] = cli["ddd"].where(cli["ddd"].str.len() == 2)
    cli["uf_ddd"] = cli["ddd"].map(DDD_UF)
    cli["uf_cep"] = cli["zipcode"].map(cep_para_uf)
    cli["uf_cadastro"] = cli["state"].str.upper().str.strip()
    cli["tem_cep"] = cli["zipcode"].fillna("").str.len().eq(8)
    return cli[["cliente_id", "regiao_fiscal_cpf", "uf_ddd", "uf_cep", "uf_cadastro", "tem_cep", "ddd"]]


def receita(ch):
    """Enriquecimento do CNPJ (Receita Federal, ja carregada). Colunas conferidas na tabela analytics.dim_cnpj_cliente."""
    try:
        cols = ch.query_df("select name from system.columns where database='analytics' and table='dim_cnpj_cliente'")["name"].tolist()
    except Exception:  # noqa
        return pd.DataFrame(columns=["cliente_id"])
    pick = [c for c in cols if c in ("cliente_id", "cnae_divisao", "cnae_fiscal", "idade_empresa_meses", "idade_empresa_anos", "porte", "opcao_mei",
                                     "opcao_simples", "capital_social", "qtd_socios", "matriz_filial", "uf", "situacao_cadastral")]
    if "cliente_id" not in pick:
        return pd.DataFrame(columns=["cliente_id"])
    df = ch.query_df(f"select {', '.join(pick)} from analytics.dim_cnpj_cliente")
    return df.rename(columns={c: f"rfb_{c}" for c in df.columns if c != "cliente_id"}).drop_duplicates("cliente_id")


def compartilhamento(ch):
    """Dado de identidade compartilhado entre clientes diferentes (telefone, e-mail, endereco, CPF do responsavel). Descoberta de 10/11: mesmo endereco em 2+ clientes
    ~ calote 43% contra 30% (p=0,0014). Devolve cliente_id, cmp_tel, cmp_email, cmp_end, cmp_resp, cmp_qualquer (quantos OUTROS clientes dividem o dado)."""
    c = ch.query_df("""
        select c.id as cliente_id, c.phone as phone, c.email as email, c.responsible_cpf as resp, a.zipcode as zipcode, a.number as numero, a.street as rua
        from trusted_credhub.customer c
        left join (select entity_id, argMax(zipcode, updated_at) zipcode, argMax(number, updated_at) number, argMax(street, updated_at) street
                   from trusted_credhub.addresses where entity_type = 'CUSTOMER' group by entity_id) a on a.entity_id = c.id
    """)
    c.columns = [x.split(".")[-1] for x in c.columns]
    dig = lambda s: s.fillna("").astype(str).str.replace(r"\D", "", regex=True)  # noqa: E731
    c["tel"] = dig(c["phone"]).str[-8:]
    c["email_n"] = c["email"].fillna("").astype(str).str.lower().str.strip()
    cep = dig(c["zipcode"])
    c["end"] = (cep + "|" + c["numero"].fillna("").astype(str).str.strip().str.lower() + "|" + c["rua"].fillna("").astype(str).str.lower().str.strip()).where(cep.str.len() == 8, "")
    c["resp_n"] = dig(c["resp"])

    def conta(col, min_len=1):
        s = c[col]
        vc = s[s.str.len() >= min_len].value_counts()
        return s.map(lambda v: int(vc.get(v, 1)) - 1 if len(v) >= min_len else 0)

    c["cmp_tel"], c["cmp_email"], c["cmp_end"], c["cmp_resp"] = conta("tel", 8), conta("email_n"), conta("end"), conta("resp_n", 11)
    c["cmp_qualquer"] = (c[["cmp_tel", "cmp_email", "cmp_end", "cmp_resp"]] > 0).sum(axis=1).astype(float)
    return c[["cliente_id", "cmp_tel", "cmp_email", "cmp_end", "cmp_resp", "cmp_qualquer"]].drop_duplicates("cliente_id")


def cluster_anterior(base):
    """Cluster de trajetoria do contrato ANTERIOR do mesmo CPF/CNPJ (so vale se aquele contrato ja estava maduro quando este comecou: inicio >= 75 dias antes).
    Descoberta de 10/11: anterior 'paga e para' -> novo contrato com calote 82% (n=11) contra 22%."""
    o = base.sort_values(["pessoa_id", "data_inicio"])[["contrato_id", "pessoa_id", "data_inicio", "cluster"]].copy()
    o["cl_ant"] = o.groupby("pessoa_id")["cluster"].shift(1)
    o["ini_ant"] = o.groupby("pessoa_id")["data_inicio"].shift(1)
    ok = o["cl_ant"].notna() & ((o["data_inicio"] - o["ini_ant"]).dt.days >= HORIZONTE_ALVO + CURA_DIAS)
    o["anterior_paga_e_para"] = np.where(ok, o["cl_ant"].astype(str).str.startswith(("paga no comeco", "quase nao paga")).astype(float), np.nan)
    o["anterior_paga_em_dia"] = np.where(ok, o["cl_ant"].astype(str).str.startswith("paga em dia").astype(float), np.nan)
    return o[["contrato_id", "anterior_paga_e_para", "anterior_paga_em_dia"]]


def montar(ch=None, hoje=None):
    """Tabela final: 1 linha por contrato, com features, rotulos e comportamento dos primeiros dias."""
    ch = ch or conecta()
    hoje = pd.Timestamp(hoje or pd.Timestamp.now().normalize())
    ct = _contratos(ch)
    pa = _parcelas(ch)
    ct["data_inicio"] = pd.to_datetime(ct["data_inicio"])
    base = ct.merge(rotulos_e_comportamento(ct, pa, hoje), on="contrato_id", how="left")
    base = base.merge(etapas_motor(ct, pa, hoje), on="contrato_id", how="left")
    _tj = cluster_trajetoria(pa, ct, hoje)
    base = base.merge(_tj[["contrato_id", "cluster", "cluster_ruim"] + [c for c in _tj.columns if c.startswith("tj_")]], on="contrato_id", how="left")
    base = base.merge(historico_anterior(ct, pa, hoje), on="contrato_id", how="left")
    base = base.merge(geografia(ch, ct), on="cliente_id", how="left")
    base = base.merge(compartilhamento(ch), on="cliente_id", how="left")
    base = base.merge(cluster_anterior(base), on="contrato_id", how="left")
    rf = receita(ch)
    if len(rf):
        base = base.merge(rf, on="cliente_id", how="left")
    nasc = pd.to_datetime(base["data_nascimento"], errors="coerce")
    base["idade_na_contratacao"] = ((base["data_inicio"] - nasc).dt.days / 365.25).where(nasc.notna())
    base["tempo_de_casa_dias"] = (base["data_inicio"] - pd.to_datetime(base["cliente_desde"], errors="coerce")).dt.days
    base["razao_parcela_principal"] = base["valor_parcela"].astype(float) / base["valor_principal"].astype(float).replace(0, np.nan)
    base["sem_perfil"] = base[["sexo", "segmento", "uf", "idade_na_contratacao"]].isna().all(axis=1)
    base["uf_ddd_difere_cep"] = (base["uf_ddd"].notna() & base["uf_cep"].notna() & (base["uf_ddd"] != base["uf_cep"]))
    base["uf_ddd_difere_cadastro"] = (base["uf_ddd"].notna() & base["uf_cadastro"].notna() & (base["uf_ddd"] != base["uf_cadastro"]))
    base["dow_inicio"] = base["data_inicio"].dt.dayofweek
    base["pix_agendado"] = base["pix_agendado"].astype(float)          # definido na criacao do contrato; 3x a 6x mais calote (ver spec)
    base["e_renovacao"] = (base["ordem_contrato_cliente"] > 1).astype(float)
    base["cep_vazio"] = base["uf_cep"].isna().astype(float)
    base["dom_inicio"] = base["data_inicio"].dt.day
    base["semana_mes_inicio"] = (base["dom_inicio"] - 1) // 7 + 1
    base["inconsistencias"] = (base["cep_vazio"].fillna(0) + base["idade_na_contratacao"].isna().astype(float) + base["uf_ddd_difere_cep"].astype(float).fillna(0)
                               + (base["cmp_qualquer"].fillna(0) > 0).astype(float))
    base["_gerado_em"] = pd.Timestamp.now()
    return base
