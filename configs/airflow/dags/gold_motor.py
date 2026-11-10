# Gold do motor de credito: views de consumo prontas para Metabase, API e analise. Recriadas a cada execucao do motor (CREATE OR REPLACE, sem janela vazia).
#   analytics.contrato_risco_360       1 linha por contrato: quem e, o que contratou, como pagou, rotulos, score atual (calote, inadimplencia, cluster ruim)
#   analytics.cluster_inadimplencia    1 linha por cluster de trajetoria: tamanho, perfil de pagamento, taxa de calote, valor, Pix
#   analytics.risco_dia0_perfil        D+0: taxa de calote por modo de cobranca, tipo de contrato e flag conhecido ao assinar (com intervalo de Wilson)
#   analytics.risco_renovacao          contratos que sao renovacao: cluster do anterior, salto de valor, score e resultado
#   analytics.risco_acuracia_modelo    por alvo e etapa: AUC fora do tempo, confianca
# Uso: python gold_motor.py
import credit_features as cf

VISOES = {
    "analytics.contrato_risco_360": (
        "Visao 360 por contrato da Capital Certo: identificacao, termos do contrato, geografia, historico, trajetoria de pagamento (cluster), rotulos de calote/inadimplencia e o score mais recente do motor. Fonte unica para dashboards e analises.",
        """
SELECT f.contrato_id AS contrato_id, f.cliente_id AS cliente_id, f.pessoa_id AS pessoa_id, f.data_inicio AS data_inicio, f.ordem_contrato_cliente AS ordem_contrato,
       if(f.ordem_contrato_cliente > 1, 'renovacao', 'primeiro') AS tipo_contrato,
       toFloat64OrNull(toString(f.valor_principal)) AS valor_principal, toFloat64OrNull(toString(f.valor_parcela)) AS valor_parcela, toFloat64OrNull(toString(f.qtd_parcelas_contratadas)) AS qtd_parcelas, toFloat64OrNull(toString(f.taxa_juros_pct)) AS taxa_juros_pct,
       f.provedor_pagamento AS provedor_pagamento, f.pix_agendado AS pix_agendado, f.tipo_pessoa AS tipo_pessoa, f.segmento AS segmento, f.sexo AS sexo, f.uf AS uf,
       f.idade_na_contratacao AS idade_na_contratacao, f.tempo_de_casa_dias AS tempo_de_casa_dias, f.uf_cep AS uf_cep, f.uf_ddd AS uf_ddd,
       f.cmp_qualquer AS cmp_dado_compartilhado, f.cmp_end AS cmp_endereco_compartilhado, f.inconsistencias AS inconsistencias,
       f.anterior_paga_e_para AS anterior_paga_e_para, f.cluster AS cluster_trajetoria, f.cluster_ruim AS cluster_ruim,
       f.tj_pct_no_prazo AS pct_parcelas_no_prazo, f.tj_pct_aberta AS pct_parcelas_abertas, f.tj_atraso_medio AS atraso_medio_dias,
       f.ye_0 AS calote_d45, f.yi_0 AS inadimplencia_d45, f.y_calote_30 AS calote_30d,
       s.etapa AS etapa_motor, s.idade_dias AS idade_dias, s.prob_calote AS prob_calote, s.prob_inadimplencia AS prob_inadimplencia, s.prob_cluster_ruim AS prob_cluster_ruim,
       s.faixa_risco AS faixa_risco, s.confianca_modelo AS confianca_modelo, s.motivos AS motivos, s.modelo AS modelo, s.data_referencia AS score_em
FROM analytics_ml.motor_features_contrato f
LEFT JOIN analytics.score_contrato_atual s ON s.contrato_id = f.contrato_id""",
    ),
    "analytics.cluster_inadimplencia": (
        "Clusters de inadimplencia (k-means na trajetoria de pagamento D+1..D+45 dos contratos maduros): o que cada cluster e, quanto pesa, e quanto e calote. O cluster paga no comeco e para e o de golpe/abandono.",
        """
SELECT cluster_trajetoria AS cluster, count() AS contratos, round(count() / sum(count()) OVER (), 4) AS pct_dos_contratos,
       round(avg(pct_parcelas_no_prazo), 3) AS pct_no_prazo_medio, round(avg(pct_parcelas_abertas), 3) AS pct_abertas_medio, round(avg(atraso_medio_dias), 1) AS atraso_medio_dias,
       round(avg(calote_d45), 3) AS taxa_calote_d45, round(avg(valor_principal), 0) AS valor_medio, sum(valor_principal) AS valor_total,
       round(avg(pix_agendado), 3) AS pct_pix_agendado, round(avg(toFloat64(tipo_contrato = 'renovacao')), 3) AS pct_renovacao,
       round(avg(toFloat64(cmp_endereco_compartilhado > 0)), 3) AS pct_endereco_compartilhado
FROM analytics.contrato_risco_360 WHERE cluster_trajetoria IS NOT NULL GROUP BY cluster_trajetoria ORDER BY taxa_calote_d45 DESC""",
    ),
    "analytics.risco_dia0_perfil": (
        "Dia 0: taxa de calote observada por modo de cobranca, tipo de contrato e flags conhecidos ao assinar. O dia 0 so tem sinal fraco (AUC ~0,63): esta tabela e a leitura honesta, com n e intervalo de Wilson 95%.",
        """
SELECT modo_cobranca, tipo_contrato, flag, n, calotes, round(calotes / n, 3) AS taxa_calote,
       round((calotes / n + 1.96 * 1.96 / (2 * n) - 1.96 * sqrt((calotes / n * (1 - calotes / n) + 1.96 * 1.96 / (4 * n)) / n)) / (1 + 1.96 * 1.96 / n), 3) AS ic95_inf,
       round((calotes / n + 1.96 * 1.96 / (2 * n) + 1.96 * sqrt((calotes / n * (1 - calotes / n) + 1.96 * 1.96 / (4 * n)) / n)) / (1 + 1.96 * 1.96 / n), 3) AS ic95_sup
FROM (
  SELECT if(pix_agendado = 1, 'Pix agendado', 'sem Pix') AS modo_cobranca, tipo_contrato, flag, count() AS n, sum(calote_d45) AS calotes
  FROM (
    SELECT pix_agendado, tipo_contrato, calote_d45, arrayJoin(arrayFilter(x -> x != '', [
        'todos',
        if(cmp_endereco_compartilhado > 0, 'endereco compartilhado', 'endereco proprio'),
        if(anterior_paga_e_para = 1, 'anterior paga e para', if(anterior_paga_e_para = 0, 'anterior nao e paga e para', '')),
        if(valor_principal >= 15000, 'valor >= 15 mil', 'valor < 15 mil'),
        if(qtd_parcelas < 60, 'parcelas < 60', 'parcelas >= 60'),
        if(inconsistencias >= 3, 'inconsistencias >= 3', 'inconsistencias < 3')])) AS flag
    FROM analytics.contrato_risco_360 WHERE calote_d45 IS NOT NULL
  ) GROUP BY modo_cobranca, tipo_contrato, flag HAVING n >= 15
)""",
    ),
    "analytics.risco_renovacao": (
        "Contratos que sao renovacao: como foi o anterior (cluster), salto de valor, score do motor e resultado. Renovacao depois de paga e para tem calote muito acima da media.",
        """
SELECT c.contrato_id AS contrato_id, c.pessoa_id AS pessoa_id, c.data_inicio AS data_inicio, c.ordem_contrato AS ordem_contrato, c.valor_principal AS valor_principal,
       toFloat64OrNull(toString(f.valor_contrato_anterior)) AS valor_contrato_anterior, toFloat64OrNull(toString(f.razao_valor_vs_anterior)) AS razao_valor_vs_anterior, toFloat64OrNull(toString(f.dias_desde_quitacao_anterior)) AS dias_desde_quitacao_anterior,
       toFloat64OrNull(toString(f.maior_salto_valor_historico)) AS maior_salto_valor_historico, c.anterior_paga_e_para AS anterior_paga_e_para,
       f.prev_pct_atrasadas AS anterior_pct_atrasadas, f.prev_atraso_max AS anterior_atraso_max, c.prob_calote AS prob_calote, c.prob_inadimplencia AS prob_inadimplencia,
       c.faixa_risco AS faixa_risco, c.calote_d45 AS calote_d45, c.cluster_trajetoria AS cluster_trajetoria
FROM analytics.contrato_risco_360 c JOIN analytics_ml.motor_features_contrato f ON f.contrato_id = c.contrato_id WHERE c.tipo_contrato = 'renovacao'""",
    ),
    "analytics.contrato_consulta": (
        "Ficha de consulta por contrato para o dashboard: nome, telefone (so digitos), contrato, scores atuais, cluster mais provavel e fatores de risco. Busca por telefone ou nome.",
        """
SELECT g.contrato_id AS contrato_id, g.cliente_id AS cliente_id, coalesce(k.nome, '') AS nome, lower(coalesce(k.nome, '')) AS nome_busca,
       replaceRegexpAll(coalesce(c.phone, ''), '[^0-9]', '') AS telefone, coalesce(c.email, '') AS email, g.data_inicio AS data_inicio, g.tipo_contrato AS tipo_contrato,
       g.valor_principal AS valor_principal, g.qtd_parcelas AS qtd_parcelas, g.provedor_pagamento AS provedor_pagamento, g.pix_agendado AS pix_agendado, g.segmento AS segmento, g.uf AS uf,
       g.idade_dias AS idade_dias, g.etapa_motor AS etapa_motor, g.prob_calote AS prob_calote, g.prob_inadimplencia AS prob_inadimplencia, g.prob_cluster_ruim AS prob_cluster_ruim,
       g.faixa_risco AS faixa_risco, g.confianca_modelo AS confianca_modelo, g.motivos AS motivos, g.cluster_trajetoria AS cluster_observado, g.calote_d45 AS calote_observado_d45,
       g.anterior_paga_e_para AS anterior_paga_e_para, g.cmp_endereco_compartilhado AS endereco_compartilhado, g.inconsistencias AS inconsistencias,
       f.status AS status, f.parcelas_pagas AS parcelas_pagas, f.parcelas_atrasadas AS parcelas_atrasadas, f.maior_atraso_em_aberto_dias AS maior_atraso_em_aberto_dias,
       f.valor_vencido_em_aberto AS valor_vencido_em_aberto, f.saldo_em_aberto AS saldo_em_aberto
FROM analytics.contrato_risco_360 g
LEFT JOIN analytics.dim_cliente k ON k.cliente_id = g.cliente_id
LEFT JOIN trusted_credhub.customer c ON c.id = g.cliente_id
LEFT JOIN analytics.fato_contrato f ON f.contrato_id = g.contrato_id""",
    ),
    "analytics.contrato_cluster_chances": (
        "Chance de cada contrato cair em cada cluster de trajetoria (mais recente), com telefone e nome para filtro. Soma das chances por contrato = 100%.",
        """
SELECT s.contrato_id AS contrato_id, c.nome_busca AS nome_busca, c.telefone AS telefone, s.cluster AS cluster, round(s.probabilidade, 4) AS probabilidade,
       s.etapa AS etapa, s.cluster_observado AS cluster_observado
FROM analytics.score_cluster_contrato s JOIN analytics.contrato_consulta c ON c.contrato_id = s.contrato_id
WHERE s.data_referencia = (SELECT max(data_referencia) FROM analytics.score_cluster_contrato)""",
    ),
    "analytics.contrato_piorou": (
        "Contratos ativos que PIORARAM: o score na etapa atual subiu em relacao a etapa anterior ou a faixa de risco piorou. prioridade = chance de calote x valor x (1 + piora); posicao 1 = o que mais exige atencao.",
        """
SELECT row_number() OVER (ORDER BY prioridade DESC) AS posicao, * FROM (
  SELECT c.contrato_id AS contrato_id, c.nome AS nome, c.nome_busca AS nome_busca, c.telefone AS telefone, c.idade_dias AS idade_dias, c.valor_principal AS valor_principal,
         c.saldo_em_aberto AS saldo_em_aberto, c.valor_vencido_em_aberto AS valor_vencido_em_aberto, c.maior_atraso_em_aberto_dias AS maior_atraso_dias,
         b.etapa AS etapa_anterior, a.etapa AS etapa_atual, round(b.prob_calote, 3) AS prob_anterior, round(a.prob_calote, 3) AS prob_atual, round(a.prob_calote - b.prob_calote, 3) AS piora,
         multiIf(b.prob_calote < 0.15, 'baixo', b.prob_calote < 0.30, 'medio', b.prob_calote < 0.50, 'alto', 'muito_alto') AS faixa_anterior,
         multiIf(a.prob_calote < 0.15, 'baixo', a.prob_calote < 0.30, 'medio', a.prob_calote < 0.50, 'alto', 'muito_alto') AS faixa_atual,
         c.prob_cluster_ruim AS prob_cluster_ruim, c.confianca_modelo AS confianca, c.motivos AS motivos,
         round(a.prob_calote * c.valor_principal * (1 + greatest(a.prob_calote - b.prob_calote, 0)), 0) AS prioridade
  FROM (SELECT contrato_id, etapa, prob_calote, row_number() OVER (PARTITION BY contrato_id ORDER BY etapa DESC) rn FROM analytics.score_contrato_etapas
        WHERE data_referencia = (SELECT max(data_referencia) FROM analytics.score_contrato_etapas)) a
  JOIN (SELECT contrato_id, etapa, prob_calote, row_number() OVER (PARTITION BY contrato_id ORDER BY etapa DESC) rn FROM analytics.score_contrato_etapas
        WHERE data_referencia = (SELECT max(data_referencia) FROM analytics.score_contrato_etapas)) b ON b.contrato_id = a.contrato_id AND b.rn = 2
  JOIN analytics.contrato_consulta c ON c.contrato_id = a.contrato_id
  WHERE a.rn = 1 AND c.idade_dias <= 50 AND a.prob_calote - b.prob_calote > 0.03
)""",
    ),
    "analytics.risco_acuracia_modelo": (
        "Acuracia fora do tempo do modelo escolhido por alvo (calote, inadimplencia, cluster_ruim) e etapa, a cada execucao. Confianca: nenhuma (<0,58), baixa, media (>=0,68), alta (>=0,78).",
        """
SELECT run_id, data_referencia, alvo, etapa, features, modelo, n_treino_final, n, round(auc, 3) AS auc, round(auc_ic_lo, 3) AS auc_ic_inf, round(auc_ic_hi, 3) AS auc_ic_sup,
       round(pr_auc, 3) AS pr_auc, round(ks, 3) AS ks, round(brier, 4) AS brier, round(brier_base, 4) AS brier_base, round(lift_top_decil, 2) AS lift_top_decil,
       multiIf(auc >= 0.78, 'alta', auc >= 0.68, 'media', auc >= 0.58, 'baixa', 'nenhuma') AS confianca, fallback
FROM analytics_ml.motor_execucoes WHERE escolhido = 1 ORDER BY data_referencia DESC, alvo, etapa""",
    ),
}


TABELAS = set(VISOES)   # tudo materializado: leitura rapida (API e Metabase com filtro) e comentario em cada coluna (ver documentar_clickhouse.py)


def main():
    ch = cf.conecta()
    for nome, (comentario, sql) in VISOES.items():
        if nome in TABELAS:
            banco, tabela = nome.split(".")
            eng = ch.query("SELECT engine FROM system.tables WHERE database = {b:String} AND name = {t:String}", parameters={"b": banco, "t": tabela}).result_rows
            if eng and eng[0][0] == "View":
                ch.command(f"DROP VIEW {nome}")
            ch.command(f"CREATE OR REPLACE TABLE {nome} ENGINE = MergeTree ORDER BY tuple() COMMENT '{comentario}' AS {sql}")
        else:
            ch.command(f"CREATE OR REPLACE VIEW {nome} COMMENT '{comentario}' AS {sql}")
        n = ch.query(f"select count() from {nome}").result_rows[0][0]
        print(nome, n, flush=True)
    import documentar_clickhouse

    documentar_clickhouse.main()


if __name__ == "__main__":
    main()

