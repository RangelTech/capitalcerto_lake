# Dicionario de nomes claros para quem le dashboards e relatorios (pessoal de negocio, sem jargao tecnico). Usado pelo Metabase (expressoes SQL) e gravado em
# analytics.dicionario_nomes_motor. Uso: python nomes_claros.py  (recria a tabela do dicionario)
FATORES = {
    "pix_agendado": "Cobrança por Pix agendado", "provedor_pagamento": "Provedor de cobrança", "idade_na_contratacao": "Idade do cliente ao contratar",
    "cep_vazio": "CEP não preenchido", "prev_atraso_max": "Maior atraso em contrato anterior (dias)", "prev_pct_atrasadas": "% de parcelas atrasadas em contrato anterior",
    "e_renovacao": "É renovação", "cmp_end": "Endereço dividido com outro cliente", "cmp_qualquer": "Algum dado dividido com outro cliente (telefone, e-mail, endereço)",
    "anterior_paga_e_para": "Contrato anterior caiu em 'paga no começo e para'", "inconsistencias": "Inconsistências no cadastro", "dom_inicio": "Dia do mês da contratação",
    "dow_inicio": "Dia da semana da contratação", "maior_salto_valor_historico": "Maior aumento de valor entre contratos", "salto_valor": "Aumento de valor sobre o contrato anterior",
    "razao_valor_vs_anterior": "Valor do contrato ÷ valor do anterior", "razao_parcela_principal": "Valor da parcela ÷ valor do contrato", "valor_parcela": "Valor da parcela",
    "valor_principal": "Valor do contrato", "qtd_parcelas_contratadas": "Número de parcelas", "taxa_juros_pct": "Taxa de juros", "tempo_de_casa_dias": "Tempo como cliente (dias)",
    "atraso_max": "Maior atraso (dias)", "atraso_medio": "Atraso médio (dias)", "n_vencidas": "Parcelas já vencidas", "pct_abertas": "% de parcelas vencidas e em aberto",
    "pct_atrasadas": "% de parcelas atrasadas", "pct_no_prazo": "% de parcelas pagas no prazo", "primeira_paga_no_prazo": "Primeira parcela paga no prazo",
    "regiao_fiscal_cpf": "Estado de origem do CPF", "segmento": "Ramo do negócio", "tipo_empresa": "Tipo de empresa", "tipo_pessoa": "Pessoa física ou jurídica", "uf": "Estado",
    "uf_cep": "Estado do CEP", "uf_ddd": "Estado do DDD do telefone",
}
PREFIXOS_CATEGORIA = {"provedor_pagamento_": "Provedor de cobrança: ", "segmento_": "Ramo: ", "uf_ddd_": "DDD do estado: ", "regiao_fiscal_cpf_": "Origem do CPF: "}
FAIXAS = {"baixo": "Baixo", "medio": "Médio", "alto": "Alto", "muito_alto": "Muito alto"}
CONFIANCA = {"nenhuma": "Nenhuma", "baixa": "Baixa", "media": "Média", "alta": "Alta"}
MODELOS = {"logistica": "Regressão logística", "logistica_L1": "Regressão logística (seleciona fatores)", "lightgbm": "Árvores de decisão (LightGBM)", "rf": "Floresta aleatória",
           "hgb": "Árvores de decisão (HistGB)"}
CONJUNTOS = {"so_comportamento": "Só comportamento de pagamento", "base+comportamento": "Cadastro + comportamento de pagamento", "so_base": "Só cadastro",
             "parcimonioso": "Cadastro enxuto", "parcimonioso+compart": "Cadastro enxuto + endereço dividido"}
ALVOS = {"calote": "Calote", "inadimplencia": "Inadimplência (atraso)", "cluster_ruim": "Cluster ruim (paga no começo e para)"}
CLUSTERS = {"paga no comeco e para": "Paga no começo e para (abandono)", "paga com atraso longo (mais de 30 dias)": "Paga com atraso longo (mais de 30 dias)",
            "paga com atraso moderado (~10 dias)": "Paga com atraso moderado (cerca de 10 dias)", "paga em dia": "Paga em dia"}
TIPOS = {"primeiro": "Primeiro contrato", "renovacao": "Renovação"}


ALGORITMOS = {"l1": "Regressão logística (seleciona fatores)", "l2": "Regressão logística", "arvore": "Árvore de decisão", "rf": "Floresta aleatória", "extratrees": "Árvores extras",
              "hgb": "Gradient boosting (HistGB)", "lgbm": "Gradient boosting (LightGBM)", "nb": "Naive Bayes", "knn": "Vizinhos mais próximos", "svm": "Máquina de vetores (SVM)",
              "mlp": "Rede neural pequena", "comite": "Comitê (média de todos)"}
CONJUNTOS_BATERIA = {"A enxuto (6 + provedor)": "Cadastro enxuto (6 informações + provedor)", "B enxuto + termos do contrato": "Enxuto + termos do contrato",
                     "C enxuto + endereco dividido + inconsistencias": "Enxuto + endereço dividido + inconsistências", "D enxuto + cluster do contrato anterior": "Enxuto + cluster do contrato anterior",
                     "E enxuto + tom engajamento": "Enxuto + tom da conversa (engajamento)", "F enxuto + 12 tons": "Enxuto + 12 medidas de tom", "G enxuto + pib/porte": "Enxuto + PIB e porte do município",
                     "H enxuto + dias lead + pagou bem": "Enxuto + dias até contratar + pagou bem o anterior", "I enxuto + compart + anterior + tom engaj": "Enxuto + endereço dividido + anterior + tom",
                     "J tudo acima": "Tudo junto"}
ALVOS_BATERIA = {"calote D+1..45": "Calote até o dia 45", "calote ate D+30": "Calote em 30 dias", "inadimplencia D+1..45": "Inadimplência até o dia 45", "cluster paga e para": "Cluster ruim (paga no começo e para)"}
ESTRATOS = {"Pix agendado": "Com Pix agendado", "sem Pix agendado": "Sem Pix agendado", "sem Pix": "Sem Pix agendado", "todos": "Todos os contratos", "todos (controla Pix)": "Todos (descontando o Pix)"}
VARIAVEIS_COMBO = {"valor_x_fat": "valor ÷ faturamento", "tom_engaj": "tom: engajamento", "tom_evasivo": "tom: evasivo", "tom_direto": "tom: direto", "dias_lead": "dias até contratar", "origem_cpf": "origem do CPF",
                   "linha_wpp": "linha de WhatsApp", "inconsist": "inconsistências", "endereco": "endereço", "anterior": "contrato anterior", "historico": "histórico", "segmento": "ramo do negócio",
                   "parcelas": "nº de parcelas", "provedor": "provedor", "idade": "idade", "valor": "valor", "cep": "CEP", "ramo": "ramo (CNAE)"}


def sql_variaveis_combo(col):
    """' x ' vira ' + ' e cada nome tecnico de variavel vira o nome claro (mais longos primeiro, para 'valor_x_fat' nao virar 'valor')."""
    expr = f"replaceAll({col}, ' x ', ' + ')"
    for k in sorted(VARIAVEIS_COMBO, key=len, reverse=True):
        expr = f"replaceRegexpAll({expr}, '(^| )({k})($| )', '\\\\1{VARIAVEIS_COMBO[k]}\\\\3')"
    return expr


def _arr(d):
    k = ", ".join("'" + x.replace("'", "\\'") + "'" for x in d)
    v = ", ".join("'" + x.replace("'", "\\'") + "'" for x in d.values())
    return f"[{k}], [{v}]"


def sql_mapa(col, d):
    """Traduz o valor tecnico de `col` para o nome claro (mantem o original se nao houver traducao)."""
    return f"transform({col}, {_arr(d)}, {col})"


def sql_etapa(col):
    return f"if({col} = 0, 'Dia 0 (assinatura)', concat('Dia ', toString({col})))"


def sql_fator(col):
    """Nome claro de um fator do modelo: tira prefixos tecnicos, traduz, e acrescenta 'ate o dia N' quando e comportamento de pagamento."""
    semn = f"replaceRegexpOne({col}, '^(n__|c__)', '')"
    base = f"replaceRegexpOne({semn}, '^missingindicator_', '')"
    sem_dia = f"replaceRegexpOne({base}, '^e[0-9]+_', '')"
    dia = f"extract({base}, '^e([0-9]+)_')"
    cat = "".join(f"if(startsWith({sem_dia}, '{p}'), concat('{t}', substring({sem_dia}, {len(p) + 1})), " for p, t in PREFIXOS_CATEGORIA.items())
    nome = f"{cat}{sql_mapa(sem_dia, FATORES)}" + ")" * len(PREFIXOS_CATEGORIA)
    return (f"concat(if(startsWith({semn}, 'missingindicator_'), 'Sem informação: ', ''), {nome}, "
            f"if({dia} != '', concat(' (até o dia ', {dia}, ')'), ''))")


def tabela_dicionario():
    linhas = []
    for tipo, d in (("fator", FATORES), ("faixa de risco", FAIXAS), ("confiança", CONFIANCA), ("modelo", MODELOS), ("conjunto de variáveis", CONJUNTOS), ("alvo", ALVOS), ("cluster", CLUSTERS),
                    ("tipo de contrato", TIPOS)):
        linhas += [(tipo, k, v) for k, v in d.items()]
    return linhas


def main():
    import pandas as pd

    import credit_features as cf

    ch = cf.conecta()
    ch.command("DROP TABLE IF EXISTS analytics.dicionario_nomes_motor")
    ch.command("CREATE TABLE analytics.dicionario_nomes_motor (tipo String COMMENT 'o que e traduzido', nome_tecnico String COMMENT 'nome usado no banco e na API', "
               "nome_claro String COMMENT 'nome para quem le o dashboard') ENGINE = MergeTree ORDER BY (tipo, nome_tecnico) "
               "COMMENT 'Dicionario: nome tecnico do motor de credito para nome claro, para leitura por area de negocio.'")
    ch.insert_df("analytics.dicionario_nomes_motor", pd.DataFrame(tabela_dicionario(), columns=["tipo", "nome_tecnico", "nome_claro"]))
    print("dicionario", len(tabela_dicionario()))


if __name__ == "__main__":
    main()
