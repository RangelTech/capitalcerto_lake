# Localizacao da empresa (CIDADE) ligada a dados economicos do IBGE (PIB municipal, PIB per capita, populacao, capital x interior) e ao PIB per capita da UF.
# Cidade da empresa: endereco do CRM (cidade e UF) -> cidade do CNPJ no relatorio Dora -> municipio da Receita. Casamento por nome normalizado + UF com a lista de municipios do IBGE.
# Testa cada variavel contra o calote e a inadimplencia (univariado com FDR). Fontes publicas: servicodados.ibge.gov.br e apisidra.ibge.gov.br (acesso aberto, sem login).
import re
import unicodedata
import warnings

import numpy as np
import pandas as pd
import requests
from scipy import stats

import credit_features as cf
import credit_scan as sc

warnings.filterwarnings("ignore")
CAPITAIS = {"RIO BRANCO/AC", "MACEIO/AL", "MACAPA/AP", "MANAUS/AM", "SALVADOR/BA", "FORTALEZA/CE", "BRASILIA/DF", "VITORIA/ES", "GOIANIA/GO", "SAO LUIS/MA", "CUIABA/MT", "CAMPO GRANDE/MS",
            "BELO HORIZONTE/MG", "BELEM/PA", "JOAO PESSOA/PB", "CURITIBA/PR", "RECIFE/PE", "TERESINA/PI", "RIO DE JANEIRO/RJ", "NATAL/RN", "PORTO ALEGRE/RS", "PORTO VELHO/RO", "BOA VISTA/RR",
            "FLORIANOPOLIS/SC", "SAO PAULO/SP", "ARACAJU/SE", "PALMAS/TO"}


def norm(s):
    t = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z ]", " ", t)).strip()


def ibge():
    """Le os dados do IBGE ja baixados (a VPS nao alcanca os sites do IBGE; baixados de outra maquina em 2026-11-09: PIB municipal 2023 e populacao estimada)."""
    m = pd.read_csv("/tmp/ibge_municipios.csv")
    uf = pd.read_csv("/tmp/ibge_uf.csv")
    m["nome"] = m["nome"].map(norm)
    m["chave"] = m["nome"] + "/" + m["uf"]
    m["pib_pc"] = m["pib_mil_reais"] * 1000 / m["populacao"]
    m["uf_cod"] = (m["cod"] // 100000).astype(int)
    m = m.merge(uf, on="uf_cod", how="left")
    m["pib_pc_uf"] = m["pib_uf"] * 1000 / m["pop_uf"]
    m["capital"] = m["chave"].isin(CAPITAIS).astype(float)
    return m


def cidade_empresa(ch, df):
    a = ch.query_df("""select c.id as cliente_id, a.city as cidade_crm, a.state as uf_crm
                       from trusted_credhub.customer c left join (select entity_id, argMax(city, updated_at) city, argMax(state, updated_at) state from trusted_credhub.addresses
                                                                   where entity_type = 'CUSTOMER' group by entity_id) a on a.entity_id = c.id""")
    r = ch.query_df("select cliente_id, municipio_receita, uf_receita from analytics.dim_cnpj_cliente")
    d = ch.query_df("""select contato_id, argMax(dora_cidade_cnpj, conversa_criada_em) as cidade_dora, argMax(dora_uf_cnpj, conversa_criada_em) as uf_dora
                       from analytics.ficha_credito_conversa where dora_cidade_cnpj is not null group by contato_id""")
    pon = ch.query_df("select cliente_id, toInt64(contato_id) as contato_id from analytics.ponte_lead_cliente where vinculo_valido = 1")
    d = d.assign(contato_id=pd.to_numeric(d["contato_id"], errors="coerce").astype("Int64")).merge(pon.assign(contato_id=pon["contato_id"].astype("Int64")), on="contato_id", how="inner").drop_duplicates("cliente_id")
    x = df[["contrato_id", "cliente_id"]].merge(a, on="cliente_id", how="left").merge(r, on="cliente_id", how="left").merge(d[["cliente_id", "cidade_dora", "uf_dora"]], on="cliente_id", how="left")
    cid = []
    for t in x.itertuples():
        c = None
        for cidade, uf in ((t.cidade_crm, t.uf_crm), (t.cidade_dora, t.uf_dora), (t.municipio_receita, t.uf_receita)):
            if cidade and isinstance(cidade, str) and uf and isinstance(uf, str) and len(uf) == 2 and norm(cidade):
                c = norm(cidade) + "/" + uf.upper()
                break
        cid.append(c)
    x["chave"] = cid
    return x[["contrato_id", "chave"]]


def main():
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan6.pkl")
    df["pix"] = df["pix_agendado"].astype(float)
    m = ibge()
    print(f"IBGE: {len(m)} municipios; com PIB: {int(m['pib_mil_reais'].notna().sum())}; com populacao: {int(m['populacao'].notna().sum())}", flush=True)
    c = cidade_empresa(ch, df)
    print(f"contratos com cidade identificada: {int(c['chave'].notna().sum())} de {len(c)}", flush=True)
    j = c.merge(m[["chave", "cod", "uf", "regiao", "pib_pc", "populacao", "pib_mil_reais", "capital", "pib_pc_uf"]], on="chave", how="left")
    print(f"casadas com o IBGE: {int(j['cod'].notna().sum())} ({j['cod'].notna().mean() * 100:.0f}%)", flush=True)
    df = df.merge(j.rename(columns={"uf": "g_uf", "regiao": "g_regiao", "pib_pc": "g_pib_pc", "populacao": "g_populacao", "capital": "g_capital", "pib_pc_uf": "g_pib_pc_uf"}).drop(columns=["chave", "cod", "pib_mil_reais"]),
                  on="contrato_id", how="left")
    df["g_pib_pc_log"] = np.log(df["g_pib_pc"])
    df["g_pop_log"] = np.log(df["g_populacao"])
    df["g_porte"] = pd.cut(df["g_populacao"], [0, 50_000, 200_000, 1_000_000, 1e9], labels=["ate 50 mil", "50-200 mil", "200 mil-1 mi", "mais de 1 mi"]).astype(object)
    df["g_pib_pc_faixa"] = pd.qcut(df["g_pib_pc"], 4, labels=["1o quartil (menor)", "2o", "3o", "4o (maior)"]).astype(object)
    df["g_pib_pc_uf_log"] = np.log(df["g_pib_pc_uf"])
    df.to_pickle("/tmp/base_scan7.pkl")
    num = ["g_pib_pc_log", "g_pop_log", "g_capital", "g_pib_pc_uf_log"]
    cat = ["g_uf", "g_regiao", "g_porte", "g_pib_pc_faixa"]
    todas = []
    for alvo in ("ye_0", "y_calote_30", "y_calote_15", "y_30"):
        r = sc.testes(df, alvo, num, cat, min_nivel=12)
        if not len(r):
            continue
        r["bloco"] = "localizacao da empresa (cidade, IBGE)"
        todas.append(r)
        sig = r[r["q_fdr"] < 0.10].sort_values("p")
        print(f"\n== {alvo}: n={int(df[alvo].notna().sum())}; {len(r)} testes; q<0.10: {len(sig)}", flush=True)
        for x in sig.head(8).itertuples():
            print(f"   {x.variavel} [{x.nivel}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} estavel={x.estavel}", flush=True)
        print("   menores p:", [(x.variavel, x.nivel[:16], round(x.p, 4), x.n) for x in r.sort_values("p").head(6).itertuples()], flush=True)
    # dentro de 'sem Pix' (isola a cobranca)
    sp = df[df["pix"] == 0]
    for alvo in ("y_calote_30", "ye_0"):
        r = sc.testes(sp, alvo, num, cat, min_nivel=12)
        if len(r):
            print(f"\n-- so SEM Pix, {alvo}: n={int(sp[alvo].notna().sum())}; menores p:", [(x.variavel, x.nivel[:16], round(x.p, 4), x.n, round(x.efeito, 3)) for x in r.sort_values("p").head(6).itertuples()], flush=True)
    # tabela de taxa por UF (com IC) e por quartil de PIB per capita
    for alvo in ("y_calote_30",):
        d = df[df[alvo].notna()]
        t = d.groupby("g_uf")[alvo].agg(["count", "mean"])
        t = t[t["count"] >= 15].sort_values("mean", ascending=False)
        print(f"\ncalote 30d por UF da empresa (n>=15):", t.round(2).to_dict("index"), flush=True)
        print("calote 30d por quartil de PIB per capita do municipio:", d.groupby("g_pib_pc_faixa")[alvo].agg(["count", "mean"]).round(2).to_dict("index"), flush=True)
        print("calote 30d capital x interior:", d.groupby("g_capital")[alvo].agg(["count", "mean"]).round(2).to_dict("index"), flush=True)
    if todas:
        pd.concat(todas).to_pickle("/tmp/motor_scan_geo.pkl")


if __name__ == "__main__":
    main()
