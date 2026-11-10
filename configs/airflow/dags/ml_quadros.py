# Experimento: dados cadastrais x "quadro de pagamento" do cliente. Separado das DAGs de producao.
# Quadros (mutuamente exclusivos, por CPF/CNPJ unico, olhando todos os contratos da empresa Capital Certo):
#   nunca_pagou        : tem parcela vencida em aberto e nenhuma parcela paga
#   pagou_e_parou      : tem parcela vencida em aberto, mas ja pagou parcelas
#   atrasa_mas_paga    : sem parcela vencida hoje, mas ja pagou alguma parcela com mais de 7 dias de atraso
#   paga_em_dia        : sem parcela vencida hoje, pagou parcelas e o maior atraso pago foi de ate 7 dias
# Clientes sem historico (nenhuma parcela paga nem vencida) ficam de fora.
#
# Fator normalizador: o V de Cramer cresce sozinho com o numero de niveis e com grupos pequenos. Por isso cada atributo e
# comparado com a distribuicao do V obtida embaralhando os quadros (permutacao): v_excedente = V - V medio do acaso e
# z = (V - media do acaso) / desvio do acaso. O ranking usa o z. Cada nivel traz ainda lift suavizado (puxado para a media).
# Escopos: "todos" e "so_cadastro_completo" (tira o cohort antigo, que nao tem perfil e quase sempre esta em atraso).
import math
import os
import re
import unicodedata

import numpy as np
import pandas as pd

import ml_inadimplencia as ml

QUADROS = ["nunca_pagou", "pagou_e_parou", "atrasa_mas_paga", "paga_em_dia"]
ROTULOS = {"nunca_pagou": "Nunca pagou", "pagou_e_parou": "Pagou e parou", "atrasa_mas_paga": "Atrasa mas paga", "paga_em_dia": "Paga em dia"}
DDD_UF = {}
for uf, ddds in {"SP": "11 12 13 14 15 16 17 18 19", "RJ": "21 22 24", "ES": "27 28", "MG": "31 32 33 34 35 37 38", "PR": "41 42 43 44 45 46", "SC": "47 48 49",
                 "RS": "51 53 54 55", "DF": "61", "GO": "62 64", "TO": "63", "MT": "65 66", "MS": "67", "AC": "68", "RO": "69", "BA": "71 73 74 75 77", "SE": "79",
                 "PE": "81 87", "AL": "82", "PB": "83", "RN": "84", "CE": "85 88", "PI": "86 89", "MA": "98 99", "PA": "91 93 94", "AM": "92 97", "AP": "96", "RR": "95"}.items():
    for d in ddds.split():
        DDD_UF[d] = uf

SQL = """
SELECT cu.pessoa_id AS pessoa_id, cu.sexo AS sexo, cu.faixa_etaria AS faixa_etaria, if(cu.uf IN ('', 'NI'), 'sem_info', cu.uf) AS uf_endereco, cu.cidade AS cidade,
       if(cu.segmento = '', 'sem_info', cu.segmento) AS segmento, cu.tipo_pessoa AS tipo_pessoa, if(cu.tipo_empresa = '', 'sem_info', cu.tipo_empresa) AS porte_asaas,
       if(ifNull(cu.renda_declarada, 0) > 0, 'informada', 'nao_informada') AS renda,
       any(cr.phone) AS telefone, any(cr.email) AS email, any(cr.name) AS nome, any(cr.cpf_cnpj) AS documento, any(cr.responsible_cpf) AS cpf_responsavel,
       any(f.pagas) AS pagas, any(f.venc) AS venc, any(f.atr_pago) AS atr_pago, toFloat64(any(cu.total_emprestado)) AS total_emprestado,
       dateDiff('day', any(cu.primeiro_contrato_em), toDate('{ref}')) AS dias_desde_primeiro, any(a.tem_asaas) AS tem_asaas
FROM analytics.cliente_360 cu
JOIN trusted_credhub.customer cr ON cr.id = cu.cliente_id
JOIN (SELECT pessoa_id, sum(parcelas_pagas) AS pagas, sum(parcelas_vencidas_em_aberto) AS venc, max(maior_atraso_pago_dias) AS atr_pago
      FROM analytics.fato_contrato WHERE empresa = 'Capital Certo' GROUP BY pessoa_id) f ON f.pessoa_id = cu.pessoa_id
LEFT JOIN (SELECT cliente_id, max(provedor_pagamento = 'ASAAS') AS tem_asaas FROM analytics.dim_contrato WHERE NOT excluido GROUP BY cliente_id) a ON a.cliente_id = cu.cliente_id
WHERE cu.empresa = 'Capital Certo' AND cu.qtd_contratos > 0
GROUP BY cu.pessoa_id, cu.sexo, cu.faixa_etaria, cu.uf, cu.cidade, cu.segmento, cu.tipo_pessoa, cu.tipo_empresa, cu.renda_declarada
"""


def _quadro(r):
    if r["venc"] > 0:
        return "nunca_pagou" if r["pagas"] == 0 else "pagou_e_parou"
    if r["pagas"] == 0:
        return None
    return "atrasa_mas_paga" if r["atr_pago"] > 7 else "paga_em_dia"


def _derivar(df):
    tel = df["telefone"].fillna("").astype(str)
    df["ddd"] = tel.str[:2].where(tel.str.len() >= 10, "sem_info")
    df["uf_pelo_ddd"] = df["ddd"].map(DDD_UF).fillna("sem_info")

    def email_tipo(e):
        e = (e or "").lower()
        if not e:
            return "sem_email"
        if e.endswith("@cadastro-simples.local"):
            return "placeholder_do_cadastro"
        d = e.split("@")[-1]
        return {"gmail.com": "gmail", "hotmail.com": "hotmail", "outlook.com": "outlook"}.get(d, "outro_provedor")
    df["email_tipo"] = df["email"].map(email_tipo)

    def forma(n):
        n = " " + unicodedata.normalize("NFD", (n or "").lower()).encode("ascii", "ignore").decode() + " "
        for chave, rot in [(" mei ", "mei"), (" ltda", "ltda"), (" eireli", "eireli"), (" epp ", "epp"), (" me ", "me"), (" s/a ", "sa"), (" s.a", "sa")]:
            if chave in n:
                return rot
        return "sem_sufixo_no_nome"
    df["nome_forma_juridica"] = df["nome"].map(forma)
    palavras = df["nome"].fillna("").str.split().str.len()
    df["nome_n_palavras"] = pd.cut(palavras, [-1, 2, 3, 4, 100], labels=["1 a 2", "3", "4", "5 ou mais"]).astype(str)
    doc = df["documento"].fillna("").astype(str)
    df["cpf_regiao_fiscal"] = np.where(doc.str.len() == 11, doc.str[8], "nao_se_aplica")
    df["cnpj_matriz_ou_filial"] = np.where(doc.str.len() == 14, np.where(doc.str[8:12] == "0001", "matriz", "filial"), "nao_se_aplica")
    df["cidade"] = df["cidade"].map(ml._norm_cidade)
    cont = df["cidade"].value_counts()
    df["cidade"] = df["cidade"].where(df["cidade"].map(cont) >= 15, "Outras cidades")
    df["sexo"] = df["sexo"].replace({"": "sem_info", "MASCULINO": "masculino", "FEMININO": "feminino"})
    return df


ATRIBUTOS = {"sexo": "Sexo", "faixa_etaria": "Idade", "uf_endereco": "UF (endereço cadastrado)", "uf_pelo_ddd": "UF pelo DDD do telefone", "ddd": "DDD do telefone",
             "cidade": "Cidade", "segmento": "Segmento do comércio", "tipo_pessoa": "CPF ou CNPJ", "porte_asaas": "Porte (Asaas)", "renda": "Renda declarada",
             "email_tipo": "Tipo de e-mail", "nome_forma_juridica": "Forma jurídica no nome", "nome_n_palavras": "Palavras no nome", "cpf_regiao_fiscal": "Região fiscal do CPF (9º dígito)",
             "cnpj_matriz_ou_filial": "CNPJ matriz ou filial"}


def _cramers(tab):
    n = tab.sum()
    exp = np.outer(tab.sum(1), tab.sum(0)) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.nansum((tab - exp) ** 2 / np.where(exp == 0, np.nan, exp))
    k = min(tab.shape) - 1
    return chi2, (math.sqrt(chi2 / (n * k)) if k > 0 and n > 0 else 0.0)


def _tab(cod_nivel, cod_quadro, nl, nq):
    t = np.zeros((nl, nq))
    np.add.at(t, (cod_nivel, cod_quadro), 1)
    return t[t.sum(1) > 0]


def associacao(df, atributo, escopo, n_perm=1000, seed=3):
    niveis, cod_n = np.unique(df[atributo].astype(str).values, return_inverse=True)
    quadros = np.array([QUADROS.index(q) for q in df["quadro"]])
    t = _tab(cod_n, quadros, len(niveis), len(QUADROS))
    chi2, v = _cramers(t)
    rng = np.random.default_rng(seed)
    nulo = np.empty(n_perm)
    for i in range(n_perm):
        nulo[i] = _cramers(_tab(cod_n, rng.permutation(quadros), len(niveis), len(QUADROS)))[1]
    mu, sd = nulo.mean(), nulo.std() or 1e-9
    return dict(atributo=atributo, atributo_pt=ATRIBUTOS[atributo], escopo=escopo, clientes=int(len(df)), niveis=int(len(t)), cramers_v=v, v_acaso_medio=mu,
                v_excedente=v - mu, z=(v - mu) / sd, p_permutacao=float((np.sum(nulo >= v) + 1) / (n_perm + 1)))


def niveis(df, atributo, escopo, K=20):
    out = []
    geral = {q: (df["quadro"] == q).mean() for q in QUADROS}
    for nivel, g in df.groupby(atributo):
        n = len(g)
        for q in QUADROS:
            k = int((g["quadro"] == q).sum())
            lo, hi = ml.wilson(k, n)
            suav = (k + K * geral[q]) / (n + K)
            out.append(dict(atributo=atributo, atributo_pt=ATRIBUTOS[atributo], nivel=str(nivel), escopo=escopo, quadro=q, quadro_pt=ROTULOS[q], clientes_no_nivel=n,
                            clientes_no_quadro=k, pct_no_nivel=k / n, pct_ic_baixo=lo, pct_ic_alto=hi, pct_geral=geral[q], lift=(k / n) / geral[q] if geral[q] else np.nan,
                            pct_suavizado=suav, lift_suavizado=suav / geral[q] if geral[q] else np.nan, grupo_pequeno=n < 30))
    return out


def rodar(ref=None):
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    df = ch.query_df(SQL.replace("{ref}", ref))
    for c in ["pagas", "venc", "atr_pago"]:
        df[c] = pd.to_numeric(df[c]).fillna(0)
    df["quadro"] = df.apply(_quadro, axis=1)
    df = df[df["quadro"].notna()].copy()
    df = _derivar(df)
    assoc, lv = [], []
    for escopo, d in [("todos", df), ("so_cadastro_completo", df[(df["sexo"] != "sem_info") & (df["segmento"] != "sem_info")])]:
        for a in ATRIBUTOS:
            if d[a].nunique() < 2:
                continue
            assoc.append(associacao(d, a, escopo))
            lv += niveis(d, a, escopo)
    A, L = pd.DataFrame(assoc), pd.DataFrame(lv)
    A["rank_z"] = A.groupby("escopo")["z"].rank(ascending=False, method="first").astype(int)
    A["sinal"] = np.where((A["p_permutacao"] < 0.05) & (A["z"] > 2), "acima do acaso", "dentro do acaso")
    for t in (A, L):
        t["data_referencia"] = ref
    resumo = df.groupby("quadro").size().reset_index(name="clientes")
    resumo["quadro_pt"] = resumo["quadro"].map(ROTULOS)
    resumo["pct"] = resumo["clientes"] / resumo["clientes"].sum()
    resumo["data_referencia"] = ref
    ml._salvar(ch, "quadros_resumo", resumo, "Experimento. Quantos clientes em cada quadro de pagamento (nunca pagou, pagou e parou, atrasa mas paga, paga em dia).",
               dict(quadro="codigo do quadro", clientes="clientes (CPF/CNPJ unico) no quadro", quadro_pt="nome do quadro", pct="participacao no total", data_referencia="data de corte do CRM"))
    ml._salvar(ch, "quadros_atributo", A, "Experimento. Quanto cada dado cadastral se associa ao quadro de pagamento, com fator normalizador: Cramer V descontado do V que sai por acaso (permutacao), z e p. Escopo todos ou so quem tem cadastro completo.",
               dict(atributo="codigo do atributo", atributo_pt="nome do atributo", escopo="todos os clientes ou so quem tem cadastro completo (tira o cohort antigo)", clientes="clientes na analise",
                    niveis="niveis do atributo com pelo menos 1 cliente", cramers_v="forca bruta da associacao (0 a 1)", v_acaso_medio="V medio com quadros embaralhados (o que sai por sorte)",
                    v_excedente="V menos o V do acaso", z="(V - media do acaso) / desvio do acaso: comparavel entre atributos", p_permutacao="chance de um V tao alto sair por sorte", rank_z="posicao no ranking de z dentro do escopo",
                    sinal="acima do acaso (p < 0,05 e z > 2) ou dentro do acaso", data_referencia="data de corte do CRM"))
    ml._salvar(ch, "quadros_nivel", L, "Experimento. Para cada nivel de cada atributo, a participacao de cada quadro de pagamento, com intervalo de Wilson, lift sobre a participacao geral e lift suavizado.",
               dict(atributo="codigo do atributo", atributo_pt="nome do atributo", nivel="valor do atributo", escopo="todos ou so_cadastro_completo", quadro="codigo do quadro", quadro_pt="nome do quadro",
                    clientes_no_nivel="clientes no nivel", clientes_no_quadro="clientes do nivel nesse quadro", pct_no_nivel="clientes_no_quadro / clientes_no_nivel", pct_ic_baixo="Wilson 95% inferior",
                    pct_ic_alto="Wilson 95% superior", pct_geral="participacao do quadro em todos os clientes do escopo", lift="pct_no_nivel / pct_geral",
                    pct_suavizado="participacao puxada para a geral em grupo pequeno: (k + 20 x geral) / (n + 20)", lift_suavizado="pct_suavizado / pct_geral", grupo_pequeno="menos de 30 clientes", data_referencia="data de corte do CRM"))
    print(resumo.to_string())
    print(A.sort_values(["escopo", "rank_z"])[["escopo", "atributo", "niveis", "cramers_v", "v_acaso_medio", "z", "p_permutacao", "sinal"]].round(3).to_string())
    return A, L


if __name__ == "__main__":
    rodar()
