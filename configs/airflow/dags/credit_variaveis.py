# Catalogo vivo das variaveis do motor de credito: nome claro, grupo, descricao, se o motor usa, e a CORRELACAO de cada uma com calote, inadimplencia e cada cluster de devedor
# (paga em dia, atraso moderado, atraso longo, paga no comeco e para). Alimenta a aba "Motor de credito" do Data IA Hub. Roda todo dia depois do motor.
#   analytics_ml.motor_variaveis      1 linha por variavel (comportamento de pagamento: 1 linha por tipo, medida no dia 7)
# Metrica: variavel numerica = correlacao de Spearman com o alvo (-1 a 1; positivo = mais da variavel, mais do alvo); categorica = V de Cramer (0 a 1, sem sinal).
# Uso: python credit_variaveis.py
import re

import numpy as np
import pandas as pd
from scipy import stats

import credit_features as cf

# variavel -> (nome claro, grupo, descricao)
CAT = {
    "pix_agendado": ("Cobrança por Pix agendado", "Cobrança", "O contrato é cobrado por Pix agendado (1) ou não (0). Definido na criação do contrato."),
    "provedor_pagamento": ("Canal de cobrança", "Cobrança", "Provedor da cobrança: Pluggy, Efi, Efi automático ou manual."),
    "intervalo_parcelas": ("Intervalo entre parcelas", "Contrato", "Periodicidade das parcelas do contrato."),
    "valor_principal": ("Valor do contrato (R$)", "Contrato", "Valor emprestado."),
    "valor_parcela": ("Valor da parcela (R$)", "Contrato", "Valor de cada parcela."),
    "qtd_parcelas_contratadas": ("Número de parcelas", "Contrato", "Quantas parcelas o contrato tem (59 = produto novo desde junho)."),
    "taxa_juros_pct": ("Taxa de juros (%)", "Contrato", "Taxa de juros do contrato."),
    "razao_parcela_principal": ("Parcela ÷ valor do contrato", "Contrato", "Quanto cada parcela pesa sobre o valor emprestado."),
    "dow_inicio": ("Dia da semana da contratação", "Contrato", "Dia da semana em que o contrato começou."),
    "dom_inicio": ("Dia do mês da contratação", "Contrato", "Dia do mês em que o contrato começou."),
    "ordem_contrato_cliente": ("Número do contrato do cliente", "Histórico e renovação", "1 = primeiro contrato; 2 ou mais = renovação."),
    "e_renovacao": ("É renovação", "Histórico e renovação", "1 se o cliente já teve contrato antes."),
    "valor_contrato_anterior": ("Valor do contrato anterior (R$)", "Histórico e renovação", "Valor do contrato anterior do mesmo cliente."),
    "salto_valor": ("Aumento de valor sobre o anterior (R$)", "Histórico e renovação", "Diferença de valor para o contrato anterior."),
    "razao_valor_vs_anterior": ("Valor ÷ valor do anterior", "Histórico e renovação", "Quantas vezes o valor cresceu em relação ao contrato anterior."),
    "dias_desde_quitacao_anterior": ("Dias desde a quitação do anterior", "Histórico e renovação", "Quanto tempo o cliente ficou sem contrato antes de renovar."),
    "maior_salto_valor_historico": ("Maior aumento de valor no histórico", "Histórico e renovação", "Maior salto de valor entre contratos do cliente."),
    "prev_pct_atrasadas": ("% de parcelas atrasadas no contrato anterior", "Histórico e renovação", "Como o cliente pagou o contrato anterior."),
    "prev_atraso_max": ("Maior atraso no contrato anterior (dias)", "Histórico e renovação", "Pior atraso do contrato anterior."),
    "prev_n_parcelas": ("Parcelas do contrato anterior", "Histórico e renovação", "Quantas parcelas o contrato anterior tinha vencidas."),
    "prev_contratos": ("Contratos anteriores", "Histórico e renovação", "Quantos contratos o cliente já teve."),
    "anterior_paga_e_para": ("Anterior caiu em 'paga no começo e para'", "Histórico e renovação", "O contrato anterior seguiu o padrão de abandono."),
    "anterior_paga_em_dia": ("Anterior pagou em dia", "Histórico e renovação", "O contrato anterior foi pago em dia."),
    "tipo_pessoa": ("Pessoa física ou jurídica", "Cliente", "CPF ou CNPJ."),
    "segmento": ("Ramo do negócio", "Cliente", "Segmento informado no cadastro (restaurante, bar, varejo...)."),
    "sexo": ("Sexo", "Cliente", "Sexo informado no cadastro."),
    "tipo_empresa": ("Tipo de empresa", "Cliente", "MEI, ME, EPP etc."),
    "renda_declarada": ("Renda declarada (R$)", "Cliente", "Renda informada no cadastro."),
    "idade_na_contratacao": ("Idade do cliente (anos)", "Cliente", "Idade na data do contrato."),
    "tempo_de_casa_dias": ("Tempo como cliente (dias)", "Cliente", "Dias entre o cadastro e o contrato."),
    "sem_perfil": ("Cadastro sem perfil", "Cliente", "Sem sexo, ramo, estado e idade."),
    "uf": ("Estado do cadastro", "Localização", "Estado informado no cadastro."),
    "uf_cep": ("Estado do CEP", "Localização", "Estado do CEP do endereço."),
    "uf_ddd": ("Estado do DDD", "Localização", "Estado do DDD do telefone."),
    "uf_cadastro": ("Estado do cadastro (CRM)", "Localização", "Estado do endereço no CRM."),
    "regiao_fiscal_cpf": ("Estado de origem do CPF", "Localização", "Região fiscal do CPF (onde foi emitido)."),
    "tem_cep": ("Tem CEP", "Localização", "O cadastro tem CEP."),
    "cep_vazio": ("CEP não preenchido", "Localização", "O cadastro está sem CEP."),
    "uf_ddd_difere_cep": ("DDD de estado diferente do CEP", "Localização", "O telefone é de um estado e o endereço de outro."),
    "uf_ddd_difere_cadastro": ("DDD de estado diferente do cadastro", "Localização", "O telefone é de um estado e o cadastro de outro."),
    "cmp_end": ("Endereço dividido com outro cliente", "Identidade e consistência", "Outros clientes têm o mesmo endereço."),
    "cmp_tel": ("Telefone dividido com outro cliente", "Identidade e consistência", "Outros clientes têm o mesmo telefone."),
    "cmp_email": ("E-mail dividido com outro cliente", "Identidade e consistência", "Outros clientes têm o mesmo e-mail."),
    "cmp_resp": ("Responsável dividido com outro cliente", "Identidade e consistência", "Outros clientes têm o mesmo CPF de responsável."),
    "cmp_qualquer": ("Algum dado dividido com outro cliente", "Identidade e consistência", "Quantos tipos de dado (telefone, e-mail, endereço, responsável) são divididos."),
    "inconsistencias": ("Inconsistências no cadastro", "Identidade e consistência", "Índice de 0 a 4: sem CEP, sem idade, DDD diferente, dado dividido."),
    # comportamento de pagamento (medido no dia 7)
    "pct_no_prazo": ("% de parcelas pagas no prazo", "Comportamento de pagamento", "Das parcelas vencidas até o dia, quantas foram pagas no prazo."),
    "pct_atrasadas": ("% de parcelas atrasadas", "Comportamento de pagamento", "Das parcelas vencidas até o dia, quantas foram pagas com atraso."),
    "pct_abertas": ("% de parcelas vencidas e em aberto", "Comportamento de pagamento", "Das parcelas vencidas até o dia, quantas ainda não foram pagas."),
    "atraso_medio": ("Atraso médio (dias)", "Comportamento de pagamento", "Atraso médio das parcelas pagas."),
    "atraso_max": ("Maior atraso (dias)", "Comportamento de pagamento", "Pior atraso até o dia."),
    "n_vencidas": ("Parcelas já vencidas", "Comportamento de pagamento", "Quantas parcelas venceram até o dia."),
    "primeira_paga_no_prazo": ("Primeira parcela paga no prazo", "Comportamento de pagamento", "A primeira parcela foi paga no prazo."),
}
USADAS_D0 = {"pix_agendado", "provedor_pagamento", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio", "cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para"}
USADAS_D13 = USADAS_D0 | {"qtd_parcelas_contratadas"}
COMPORTAMENTO = {"pct_no_prazo", "pct_atrasadas", "pct_abertas", "atraso_medio", "atraso_max", "n_vencidas", "primeira_paga_no_prazo"}
CATEGORICAS = {"provedor_pagamento", "intervalo_parcelas", "tipo_pessoa", "segmento", "sexo", "tipo_empresa", "uf", "uf_cep", "uf_ddd", "uf_cadastro", "regiao_fiscal_cpf"}
CLUSTERS = {"em_dia": "paga em dia", "atraso_moderado": "paga com atraso moderado", "atraso_longo": "paga com atraso longo", "paga_e_para": "paga no comeco e para"}

DDL = """CREATE TABLE IF NOT EXISTS analytics_ml.motor_variaveis (
  variavel String COMMENT 'nome tecnico (coluna de motor_features_contrato; comportamento de pagamento sem o prefixo do dia)', nome_claro String COMMENT 'nome para a area de negocio',
  grupo String COMMENT 'grupo: cobranca, contrato, cliente, historico, localizacao, identidade, comportamento de pagamento', descricao String COMMENT 'o que a variavel significa',
  tipo LowCardinality(String) COMMENT 'numerica (correlacao de Spearman, -1 a 1) ou categorica (V de Cramer, 0 a 1)', n UInt32 COMMENT 'contratos com a variavel e o resultado conhecidos',
  corr_calote Nullable(Float32) COMMENT 'correlacao com calote (20%+ das parcelas ate o dia 45 vencidas e em aberto); -1 a 1 se numerica, 0 a 1 se categorica',
  corr_inadimplencia Nullable(Float32) COMMENT 'correlacao com inadimplencia (30%+ das parcelas com mais de 3 dias de atraso ou em aberto)',
  corr_cluster_ruim Nullable(Float32) COMMENT 'correlacao com o cluster ruim (paga no comeco e para, ou quase nao paga)',
  corr_cluster_em_dia Nullable(Float32) COMMENT 'correlacao com cair no cluster paga em dia', corr_cluster_atraso_moderado Nullable(Float32) COMMENT 'correlacao com cair no cluster atraso moderado (cerca de 10 dias)',
  corr_cluster_atraso_longo Nullable(Float32) COMMENT 'correlacao com cair no cluster atraso longo (mais de 30 dias)', corr_cluster_paga_e_para Nullable(Float32) COMMENT 'correlacao com cair no cluster paga no comeco e para (golpe ou abandono)',
  p_calote Nullable(Float32) COMMENT 'p-valor da relacao com calote', q_fdr_calote Nullable(Float32) COMMENT 'p-valor corrigido por falsa descoberta (FDR) entre todas as variaveis; menor que 0,10 = sinal',
  usada_dia0 UInt8 COMMENT '1 = entra no modelo do dia 0 (assinatura)', usada_dias_1_a_14 UInt8 COMMENT '1 = entra nos modelos dos dias 1 a 14', usada_no_motor UInt8 COMMENT '1 = o motor usa em alguma etapa',
  status_teste LowCardinality(String) COMMENT 'Tem sinal (q<0,10), Sinal fraco (p<0,05), Sem sinal ou Poucos dados', conclusao String COMMENT 'leitura em portugues claro do resultado',
  atualizado_em DateTime DEFAULT now() COMMENT 'quando foi calculado') ENGINE = MergeTree ORDER BY (grupo, variavel)
  COMMENT 'Todas as variaveis do motor de credito com nome claro, grupo, correlacao com calote, inadimplencia e cada cluster de devedor, e se o motor usa. Fonte da aba Motor de credito do Data IA Hub.'"""


def fdr(p):
    p = np.asarray(p, float)
    ok = ~np.isnan(p)
    q = np.full_like(p, np.nan)
    pv = p[ok]
    o = np.argsort(pv)
    r = pv[o] * len(pv) / (np.arange(len(pv)) + 1)
    r = np.minimum.accumulate(r[::-1])[::-1]
    out = np.empty_like(pv)
    out[o] = np.clip(r, 0, 1)
    q[ok] = out
    return q


def cramer(x, y):
    t = pd.crosstab(x, y)
    if t.shape[0] < 2 or t.shape[1] < 2 or t.values.sum() < 30:
        return np.nan, np.nan
    chi2, p, _, _ = stats.chi2_contingency(t, correction=False)
    return float(np.sqrt(chi2 / (t.values.sum() * (min(t.shape) - 1)))), float(p)


def assoc(x, y, categorica):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 40 or d["y"].nunique() < 2 or d["x"].nunique() < 2:
        return np.nan, np.nan, len(d)
    if categorica:
        v, p = cramer(d["x"].astype(str), d["y"].astype(int))
        return v, p, len(d)
    r, p = stats.spearmanr(pd.to_numeric(d["x"], errors="coerce"), d["y"].astype(float), nan_policy="omit")
    return float(r), float(p), len(d)


def conclusao(nome, tipo, r, q, p, status, n):
    if status == "Poucos dados":
        return f"Poucos contratos com resultado ({n}) para concluir."
    forca = "forte" if abs(r) >= 0.3 else "moderada" if abs(r) >= 0.15 else "fraca"
    if tipo == "categorica":
        return f"{status}: o resultado muda conforme o grupo (associação {forca}, V de Cramér {r:.2f}; p={p:.3f})."
    sentido = "mais calote" if r > 0 else "menos calote"
    return f"{status}: quanto maior {nome.lower()}, tende a haver {sentido} (correlação {r:+.2f}, {forca}; p={p:.3f}, p corrigido={q:.3f})."


def main():
    ch = cf.conecta()
    ch.command(DDL)
    df = ch.query_df("select * from analytics_ml.motor_features_contrato")
    df.columns = [c.split(".")[-1] for c in df.columns]
    df["data_inicio"] = pd.to_datetime(df["data_inicio"])
    alvos_bin = {}
    for nome, col in (("calote", "ye_0"), ("inadimplencia", "yi_0"), ("cluster_ruim", "cluster_ruim")):
        alvos_bin[nome] = pd.to_numeric(df[col], errors="coerce")
    for chave, nome_cl in CLUSTERS.items():
        alvos_bin["cluster_" + chave] = df["cluster"].map(lambda c, n=nome_cl: np.nan if (c is None or pd.isna(c)) else float(str(c).startswith(n))).astype(float)
    # comportamento de pagamento: usa as colunas do dia 7 e os alvos ye_7 / yi_7 (so futuro depois do dia 7)
    alvo7 = {"calote": pd.to_numeric(df["ye_7"], errors="coerce"), "inadimplencia": pd.to_numeric(df["yi_7"], errors="coerce")}
    linhas = []
    nomes = [v for v in CAT if v in df.columns or (v in COMPORTAMENTO and f"e7_{v}" in df.columns)]
    for v in nomes:
        nome, grupo, desc = CAT[v]
        comp = v in COMPORTAMENTO
        col = f"e7_{v}" if comp else v
        x = pd.to_numeric(df[col], errors="coerce") if v not in CATEGORICAS else df[col].astype(object).where(df[col].notna(), None)
        cat = v in CATEGORICAS
        r = {"variavel": v, "nome_claro": nome + (" (até o dia 7)" if comp else ""), "grupo": grupo, "descricao": desc, "tipo": "categorica" if cat else "numerica"}
        medidas = {}
        for k, y in alvos_bin.items():
            yy = alvo7.get(k, y) if comp and k in alvo7 else y
            medidas[k] = assoc(x, yy, cat)
        r["n"] = int(medidas["calote"][2])
        for k in ("calote", "inadimplencia", "cluster_ruim", "cluster_em_dia", "cluster_atraso_moderado", "cluster_atraso_longo", "cluster_paga_e_para"):
            r["corr_" + k] = None if np.isnan(medidas[k][0]) else round(medidas[k][0], 4)
        r["p_calote"] = None if np.isnan(medidas["calote"][1]) else medidas["calote"][1]
        r["usada_dia0"] = int(v in USADAS_D0)
        r["usada_dias_1_a_14"] = int(v in USADAS_D13 or comp)
        r["usada_no_motor"] = int(r["usada_dia0"] or r["usada_dias_1_a_14"])
        linhas.append(r)
    t = pd.DataFrame(linhas)
    t["q_fdr_calote"] = fdr(t["p_calote"].astype(float).values)
    st, conc = [], []
    for _, r in t.iterrows():
        c = r["corr_calote"]
        if c is None or pd.isna(c) or r["n"] < 40:
            s = "Poucos dados"
        elif r["q_fdr_calote"] < 0.10:
            s = "Tem sinal"
        elif r["p_calote"] < 0.05:
            s = "Sinal fraco"
        else:
            s = "Sem sinal"
        st.append(s)
        conc.append(conclusao(r["nome_claro"], r["tipo"], 0 if pd.isna(c) else c, 1 if pd.isna(r["q_fdr_calote"]) else r["q_fdr_calote"], 1 if pd.isna(r["p_calote"]) else r["p_calote"], s, int(r["n"])))
    t["status_teste"], t["conclusao"] = st, conc
    for c in [c for c in t.columns if c.startswith(("corr_", "p_", "q_"))]:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    ch.command("TRUNCATE TABLE analytics_ml.motor_variaveis")
    ch.insert_df("analytics_ml.motor_variaveis", t)
    print("motor_variaveis", len(t), t["status_teste"].value_counts().to_dict(), flush=True)


if __name__ == "__main__":
    main()
