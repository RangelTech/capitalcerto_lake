"""Abas "Motor de crédito" (reconstruída com nomes claros) e "Mapa de testes" do dashboard 5. Importada por mb2_conteudo.py."""
import nomes_claros as N

ULT_RUN = "(SELECT max(run_id) FROM analytics_ml.motor_execucoes)"


def sc(nome, sql):
    return {"name": nome, "sql": sql, "display": "scalar", "viz": {"scalar.compact_primary_number": False}}


def barra(nome, sql, x, ys, xt="", yt="", display="bar", empilhada=False):
    viz = {"graph.dimensions": x if isinstance(x, list) else [x], "graph.metrics": ys, "graph.x_axis.title_text": xt, "graph.y_axis.title_text": yt}
    if empilhada:
        viz["stackable.stack_type"] = "stacked"
    return {"name": nome, "sql": sql, "display": display, "viz": viz}


def tab(nome, sql):
    return {"name": nome, "sql": sql, "display": "table", "viz": {}}


RISCO = N.sql_mapa("faixa_risco", N.FAIXAS)
CONF = N.sql_mapa("confianca_modelo", N.CONFIANCA)

T_MOTOR = """## Como ler esta aba

O motor avalia **todo dia** cada contrato **ativo** e diz a **chance de calote**: a chance de 20% ou mais das parcelas, da avaliação até o dia 45, ficarem vencidas e em aberto.

**A confiança depende da idade do contrato.** Cada idade (dia 0, 1, 2, 3, 5, 7, 10 e 14) tem o seu modelo, medido em contratos que ele não viu no treino. Nos primeiros dias há pouco comportamento de pagamento para olhar, e a confiança tende a crescer com os dias. O acerto atual de cada idade está nos gráficos abaixo e na aba **Acerto do motor** do Data IA Hub.

**O motor é refeito todo dia** com os contratos cujo resultado já se conhece: ele testa de novo os modelos e as variáveis, escolhe o melhor e só troca o anterior se não piorar. Por isso o que ele considera importante pode mudar com o tempo, e esta aba sempre mostra o estado do último treino.

A previsão só vale para contratos **ativos**; nos contratos antigos use o resultado real. Para ligar ou desligar variáveis, ver como o motor funciona por dentro e como chamar a API de risco, use a aba **Motor de crédito** do Data IA Hub.
"""

ABAS3 = {
    "Motor de crédito": [
        [({"text": T_MOTOR}, 24, 7)],
        [(sc("Contratos ativos avaliados hoje", "SELECT count() AS `Contratos` FROM analytics.score_contrato_atual WHERE fase = 'ativo'"), 6, 3),
         (sc("Contratos ativos em risco Alto ou Muito alto", "SELECT countIf(faixa_risco IN ('alto', 'muito_alto')) AS `Contratos` FROM analytics.score_contrato_atual WHERE fase = 'ativo'"), 6, 3),
         (sc("Valor dos contratos ativos em risco Alto ou Muito alto", "SELECT sum(valor_principal) AS `Valor (R$)` FROM analytics.score_contrato_atual WHERE fase = 'ativo' AND faixa_risco IN ('alto', 'muito_alto')"), 6, 3),
         (sc("Chance média de calote dos contratos ativos", "SELECT round(avg(prob_calote) * 100, 1) AS `Chance média (%)` FROM analytics.score_contrato_atual WHERE fase = 'ativo'"), 6, 3)],
        [(barra("Contratos por nível de risco e confiança do modelo",
                f"SELECT {RISCO} AS `Nível de risco`, {CONF} AS `Confiança do modelo`, count() AS `Contratos` FROM analytics.score_contrato_atual WHERE fase = 'ativo' GROUP BY faixa_risco, confianca_modelo ORDER BY faixa_risco",
                ["Nível de risco", "Confiança do modelo"], ["Contratos"], "Nível de risco", "Contratos", empilhada=True), 12, 7),
         (barra("Contratos por idade e nível de risco",
                f"SELECT {N.sql_etapa('etapa')} AS `Idade do contrato`, {RISCO} AS `Nível de risco`, count() AS `Contratos` FROM analytics.score_contrato_atual WHERE fase = 'ativo' GROUP BY etapa, faixa_risco ORDER BY etapa",
                ["Idade do contrato", "Nível de risco"], ["Contratos"], "Idade do contrato", "Contratos", empilhada=True), 12, 7)],
        [(tab("Contratos ativos de maior risco com confiança Média ou Alta",
              f"SELECT k.nome AS `Cliente`, c.telefone AS `Telefone`, s.idade_dias AS `Dias de contrato`, {N.sql_etapa('s.etapa')} AS `Avaliação usada`, round(s.prob_calote * 100, 1) AS `Chance de calote (%)`, "
              f"{N.sql_mapa('s.faixa_risco', N.FAIXAS)} AS `Nível de risco`, {N.sql_mapa('s.confianca_modelo', N.CONFIANCA)} AS `Confiança do modelo`, s.valor_principal AS `Valor (R$)`, c.saldo_em_aberto AS `Saldo em aberto (R$)` "
              "FROM analytics.score_contrato_atual s LEFT JOIN analytics.dim_cliente k ON k.cliente_id = s.cliente_id LEFT JOIN analytics.contrato_consulta c ON c.contrato_id = s.contrato_id "
              "WHERE s.fase = 'ativo' AND s.confianca_modelo IN ('media', 'alta') ORDER BY s.prob_calote DESC LIMIT 40"), 24, 9)],
        [(barra("Acerto do modelo por idade do contrato",
                f"SELECT {N.sql_etapa('etapa')} AS `Idade do contrato`, {N.sql_mapa('alvo', N.ALVOS)} AS `Tipo de previsão`, round(auc, 3) AS `Acerto` FROM analytics_ml.motor_execucoes "
                f"WHERE escolhido = 1 AND run_id = {ULT_RUN} ORDER BY etapa", ["Idade do contrato", "Tipo de previsão"], ["Acerto"], "Idade do contrato", "Acerto (0,5 = chute, 1 = perfeito)"), 12, 7),
         (barra("Previsto contra o que aconteceu, por grupo de risco (dia 7)",
                "SELECT `Grupo de risco`, round(avg(p_calibrado) * 100, 1) AS `Previsto (%)`, round(avg(y) * 100, 1) AS `Aconteceu (%)` FROM (SELECT p_calibrado, y, toUInt8(least(floor(10 * (row_number() OVER (ORDER BY p_calibrado) - 1) / count() OVER ()), 9)) + 1 AS `Grupo de risco` "
                f"FROM analytics_ml.motor_backtest WHERE run_id = {ULT_RUN} AND etapa = 7 AND alvo = 'calote') GROUP BY `Grupo de risco` ORDER BY `Grupo de risco`", "Grupo de risco", ["Previsto (%)", "Aconteceu (%)"], "Grupo de risco (1 = menor risco, 10 = maior)", "Calote (%)"), 12, 7)],
        [(barra("Fatores que mais pesam na previsão do dia 7",
                f"SELECT {N.sql_fator('fator')} AS `Fator`, round(importancia, 3) AS `Peso` FROM analytics_ml.motor_importancia WHERE alvo = 'calote' AND etapa = 7 "
                "AND run_id = (SELECT max(run_id) FROM analytics_ml.motor_importancia WHERE alvo = 'calote') ORDER BY importancia DESC LIMIT 10", "Fator", ["Peso"], "", "Peso", display="row"), 12, 7),
         (barra("Acerto do modelo a cada dia em que é refeito (dias 7 e 14)",
                f"SELECT data_referencia AS `Dia`, {N.sql_etapa('etapa')} AS `Idade do contrato`, round(argMax(auc, run_id), 3) AS `Acerto` FROM analytics_ml.motor_execucoes WHERE escolhido = 1 AND alvo = 'calote' AND etapa IN (7, 14) "
                "GROUP BY data_referencia, etapa ORDER BY data_referencia", ["Dia", "Idade do contrato"], ["Acerto"], "Dia", "Acerto", display="line"), 12, 7)],
        [(tab("Variáveis que mais se relacionam com o calote",
              "SELECT nome_claro AS `Variável`, grupo AS `Grupo`, round(corr_calote, 2) AS `Correlação com calote`, round(corr_inadimplencia, 2) AS `Correlação com inadimplência`, "
              "round(corr_cluster_paga_e_para, 2) AS `Correlação com paga no começo e para`, status_teste AS `Resultado do teste`, if(usada_no_motor = 1, 'Sim', 'Não') AS `Usada pelo motor` "
              "FROM analytics_ml.motor_variaveis ORDER BY abs(corr_calote) DESC LIMIT 15"), 14, 10),
         (barra("Calote real até o dia 45 por mês de início do contrato",
                "SELECT formatDateTime(toStartOfMonth(data_inicio), '%Y-%m') AS `Mês de início`, round(avg(ye_0) * 100, 1) AS `Calote até o dia 45 (%)`, count() AS `Contratos com resultado` "
                "FROM analytics_ml.motor_features_contrato WHERE ye_0 IS NOT NULL GROUP BY `Mês de início` ORDER BY `Mês de início`",
                "Mês de início", ["Calote até o dia 45 (%)"], "Mês de início do contrato", "Calote até o dia 45 (%)", display="line"), 10, 10)],
    ],
}
