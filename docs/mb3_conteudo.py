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

O modelo avalia **todo dia** cada contrato **ativo** (até 50 dias) e diz a **chance de calote**: 20% ou mais das parcelas, da avaliação até o dia 45, vencidas e em aberto.

**A confiança depende da idade do contrato.**
- **Dia 0 (assinatura):** acerto de cerca de 0,65. O que mais explica é o **canal de cobrança**: Pluggy cerca de 10% de calote, Efi automático de 6% a 31%, Manual cerca de 42% e **Pix agendado cerca de 74%**. Colocar mais informações (tom da conversa, valor, idade, endereço) não melhora.
- **Dias 1 a 3:** acerto de 0,69 a 0,70. Aqui o cadastro (endereço dividido com outro cliente, contrato anterior) ajuda um pouco.
- **Dia 5 em diante:** o **comportamento de pagamento** domina: acerto 0,74 no dia 5 e de 0,80 a 0,85 nos dias 7 a 14.

**Limite conhecido:** nos contratos **antigos** (mais de 50 dias) o grupo "Muito alto" prevê 82% contra 57% que realmente deu calote. Por isso a previsão só vale para contratos ativos; nos antigos use o resultado real.

O modelo é refeito todo dia com os contratos cujo resultado já se conhece e só troca o anterior se não piorar. Para ligar ou desligar variáveis, use a aba **Motor de crédito** do Data IA Hub.
"""

T_MAPA = """## O que já foi testado

Aqui está o **mapa de tudo que foi cruzado** para achar o que explica o calote: informações de cadastro, contrato, cobrança, conversa (inclusive o tom, lido por IA), bureau, mídia, endereço, valor ÷ faturamento, sazonalidade e muito mais. Cada tabela mostra um tipo de teste.

**Como ler "passa na correção":** quando se testa muita coisa, parte aparece como sinal só por sorte. A correção de falsa descoberta (FDR) separa o que provavelmente é real. *Passa = Sim* quer dizer que o achado resistiu a ela.

**Os dados completos** estão no ClickHouse (`analytics_ml.motor_testes_realizados` e as tabelas `motor_*`), para consulta pela IA.
"""

ABAS3 = {
    "Motor de crédito": [
        [({"text": T_MOTOR}, 24, 5)],
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
        [(barra("Calote em 30 dias com e sem Pix agendado, por provedor (contratos de junho em diante)",
                "SELECT provedor_pagamento AS `Provedor de cobrança`, if(pix_agendado = 1, 'Com Pix agendado', 'Sem Pix agendado') AS `Cobrança`, round(avg(y_calote_30) * 100, 1) AS `Calote em 30 dias (%)` FROM analytics_ml.motor_features_contrato "
                "WHERE y_calote_30 IS NOT NULL AND data_inicio >= '2026-06-01' GROUP BY provedor_pagamento, pix_agendado ORDER BY provedor_pagamento, pix_agendado", ["Provedor de cobrança", "Cobrança"], ["Calote em 30 dias (%)"], "Provedor de cobrança", "Calote em 30 dias (%)"), 12, 7),
         (barra("Participação do Pix agendado nos contratos novos, por mês",
                "SELECT formatDateTime(toStartOfMonth(data_inicio), '%Y-%m') AS `Mês`, round(avg(pix_agendado) * 100, 1) AS `Contratos com Pix agendado (%)` FROM analytics_ml.motor_features_contrato WHERE data_inicio >= '2026-01-01' GROUP BY `Mês` ORDER BY `Mês`",
                "Mês", ["Contratos com Pix agendado (%)"], "Mês", "Contratos com Pix agendado (%)", display="line"), 12, 7)],
        [(tab("Variáveis que mais se relacionam com o calote",
              "SELECT nome_claro AS `Variável`, grupo AS `Grupo`, round(corr_calote, 2) AS `Correlação com calote`, round(corr_inadimplencia, 2) AS `Correlação com inadimplência`, "
              "round(corr_cluster_paga_e_para, 2) AS `Correlação com paga no começo e para`, status_teste AS `Resultado do teste`, if(usada_no_motor = 1, 'Sim', 'Não') AS `Usada pelo motor` "
              "FROM analytics_ml.motor_variaveis ORDER BY abs(corr_calote) DESC LIMIT 15"), 14, 10),
         (barra("Calote real até o dia 45 por canal de cobrança (contratos de junho em diante)",
                "SELECT multiIf(pix_agendado = 1, 'Pix agendado', provedor_pagamento = 'PLUGGY', 'Pluggy', provedor_pagamento = 'EFI_AUTOMATICO', 'Efi automático', provedor_pagamento = 'EFI', 'Efi', 'Manual') AS `Canal de cobrança`, "
                "round(avg(ye_0) * 100, 1) AS `Calote até o dia 45 (%)`, count() AS `Contratos` FROM analytics_ml.motor_features_contrato WHERE ye_0 IS NOT NULL AND data_inicio >= '2026-06-01' GROUP BY `Canal de cobrança` ORDER BY `Calote até o dia 45 (%)` DESC",
                "Canal de cobrança", ["Calote até o dia 45 (%)"], "", "Calote até o dia 45 (%)"), 10, 10)],
    ],
    "Mapa de testes": [
        [({"text": T_MAPA}, 24, 5)],
        [(sc("Combinações de informações testadas", "SELECT count() AS `Combinações` FROM analytics_ml.motor_combinacoes_1a4"), 6, 3),
         (sc("Combinações com sinal depois da correção", "SELECT countIf(q_fdr < 0.1) AS `Combinações` FROM analytics_ml.motor_combinacoes_1a4"), 6, 3),
         (sc("Informações testadas com controle de Pix e mês", "SELECT uniqExact(variavel) AS `Informações` FROM analytics_ml.motor_efeito_ajustado"), 6, 3),
         (sc("Algoritmos comparados", "SELECT uniqExactIf(algoritmo, algoritmo != 'comite') AS `Algoritmos` FROM analytics_ml.motor_matriz_modelos"), 6, 3)],
        [(tab("Efeito de cada informação, descontando Pix, provedor e mês",
              f"SELECT variavel AS `Informação`, {N.sql_mapa('alvo', N.ALVOS_BATERIA)} AS `Tipo de previsão`, if(odds_ratio_por_dp > 1, 'Aumenta o risco', 'Protege contra o risco') AS `Efeito`, "
              "round((odds_ratio_por_dp - 1) * 100, 0) AS `Muda a chance em (%) por desvio padrão`, round(p, 4) AS `p-valor`, if(q_fdr < 0.1, 'Sim', 'Não') AS `Passa na correção`, n AS `Contratos` "
              "FROM analytics_ml.motor_efeito_ajustado ORDER BY p LIMIT 40"), 24, 11)],
        [(barra("Quanto cada algoritmo acerta em média (todos os testes)",
                f"SELECT {N.sql_mapa('algoritmo', N.ALGORITMOS)} AS `Algoritmo`, round(avg(auc), 3) AS `Acerto médio` FROM analytics_ml.motor_matriz_modelos GROUP BY algoritmo ORDER BY avg(auc) DESC",
                "Algoritmo", ["Acerto médio"], "", "Acerto médio (0,5 = chute)", display="row"), 12, 8),
         (barra("Quanto cada conjunto de informações acerta em média",
                f"SELECT {N.sql_mapa('conjunto', N.CONJUNTOS_BATERIA)} AS `Conjunto de informações`, round(avg(auc), 3) AS `Acerto médio` FROM analytics_ml.motor_matriz_modelos GROUP BY conjunto ORDER BY avg(auc) DESC",
                "Conjunto de informações", ["Acerto médio"], "", "Acerto médio (0,5 = chute)", display="row"), 12, 8)],
        [(tab("Melhor combinação de modelo e informações em cada situação",
              f"SELECT {N.sql_mapa('estrato', N.ESTRATOS)} AS `Grupo de contratos`, {N.sql_mapa('alvo', N.ALVOS_BATERIA)} AS `Tipo de previsão`, {N.sql_mapa('argMax(algoritmo, auc)', N.ALGORITMOS)} AS `Melhor algoritmo`, "
              f"{N.sql_mapa('argMax(conjunto, auc)', N.CONJUNTOS_BATERIA)} AS `Informações usadas`, round(max(auc), 3) AS `Acerto`, max(n) AS `Contratos no teste` FROM analytics_ml.motor_matriz_modelos "
              "GROUP BY estrato, alvo ORDER BY estrato, alvo"), 24, 7)],
        [(tab("Perfis de clientes com taxa muito diferente da média (que passam na correção)",
              f"SELECT {N.sql_mapa('estrato', N.ESTRATOS)} AS `Grupo de contratos`, {N.sql_mapa('alvo', N.ALVOS_BATERIA)} AS `Tipo de previsão`, {N.sql_variaveis_combo('variaveis')} AS `Informações combinadas`, perfil AS `Perfil`, n AS `Contratos no perfil`, "
              "round(taxa * 100, 1) AS `Taxa no perfil (%)`, round(base_estrato * 100, 1) AS `Taxa média (%)`, round(dif * 100, 1) AS `Diferença (pontos)` FROM analytics_ml.motor_combinacoes_1a4 "
              "WHERE q_fdr < 0.1 AND n >= 30 AND n_var <= 3 ORDER BY abs(dif) * sqrt(n) DESC LIMIT 40"), 24, 11)],
        [(tab("Sazonalidade: dia da semana, semana do mês e mês de início",
              f"SELECT {N.sql_mapa('estrato', N.ESTRATOS)} AS `Grupo de contratos`, {N.sql_mapa('alvo', N.ALVOS_BATERIA)} AS `Tipo de previsão`, efeito AS `Efeito testado`, round(p_lr, 4) AS `p-valor`, if(p_lr < 0.05, 'Sim', 'Não') AS `Existe efeito`, n AS `Contratos` "
              "FROM analytics_ml.motor_sazonalidade_controlada ORDER BY p_lr"), 12, 9),
         (tab("Todas as informações candidatas e o que se descobriu",
              "SELECT familia AS `Grupo`, variavel AS `Informação`, momento AS `Quando se sabe`, multiIf(status_teste = 'SINAL', 'Tem sinal', status_teste = 'SINAL_FRACO', 'Sinal fraco', status_teste = 'SEM_SINAL', 'Sem sinal', "
              "status_teste = 'INSUFICIENTE', 'Poucos dados', status_teste = 'NAO_TESTADA', 'Ainda não testada', status_teste = 'BLOQUEADA', 'Bloqueada', 'Indisponível') AS `Resultado`, resultado AS `Detalhe` "
              "FROM analytics_ml.motor_catalogo_variaveis ORDER BY familia, status_teste"), 12, 9)],
    ],
}
