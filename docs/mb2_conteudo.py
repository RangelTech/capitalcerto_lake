"""Conteudo das 3 abas novas do dashboard 5 (textos e cartoes), com nomes claros para o pessoal de negocio. Usado por metabase_motor2.py."""
import nomes_claros as N

# filtros do dashboard = "filtro de campo" do Metabase ligado as colunas telefone e nome de analytics.contrato_consulta: ao digitar, aparecem os valores que existem.
FILTRO = "[[AND {{telefone}}]] [[AND {{nome}}]]"
ULTIMO = f"(SELECT contrato_id FROM analytics.contrato_consulta WHERE 1=1 {FILTRO} ORDER BY data_inicio DESC LIMIT 1)"


def sc(nome, sql):
    return {"name": nome, "sql": sql, "display": "scalar", "viz": {"scalar.compact_primary_number": False}}


def barra(nome, sql, x, ys, xt="", yt="", display="bar"):
    return {"name": nome, "sql": sql, "display": display, "viz": {"graph.dimensions": [x], "graph.metrics": ys, "graph.x_axis.title_text": xt, "graph.y_axis.title_text": yt}}


def tab(nome, sql):
    return {"name": nome, "sql": sql, "display": "table", "viz": {}}


def usa_filtro(item):
    item["filtros"] = True
    return item


T_FICHA = """## Como usar esta aba

Escolha o **telefone** ou o **nome** nos filtros no topo: ao digitar, aparecem as opções que já existem na base. Se o cliente tiver mais de um contrato, os gráficos mostram o **mais recente**; a primeira tabela lista todos.

- **Previsão só vale para contrato novo (até 50 dias).** O modelo olha o período do dia da avaliação até o dia 45 do contrato. Contrato mais antigo aparece como "contrato antigo": use o resultado real, não uma chance.
- **Chance de calote:** probabilidade de 20% ou mais das parcelas (da avaliação até o dia 45) ficarem vencidas e em aberto.
- **Chance de inadimplência:** probabilidade de 30% ou mais das parcelas até o dia 45 serem pagas com mais de 3 dias de atraso, ou ficarem em aberto. Como atraso pequeno é comum, esse número costuma ser alto para muita gente.
- **Chance de cluster ruim:** probabilidade de o cliente pagar só no começo e parar, ou quase não pagar (padrão de abandono ou golpe).
- **Chance em cada grupo de comportamento:** as barras somam 100%. A maior barra é o destino mais provável do contrato.
- **Confiança do modelo:** vale mais que o número. Ela vem do acerto medido para a idade do contrato e tende a crescer com os dias. Com confiança baixa ou nenhuma, use como alerta e não como certeza.
- **Risco ao longo do contrato:** mostra se o risco subiu ou desceu com o passar dos dias.
"""

T_PIOR = """## Como ler esta aba

Lista os contratos **ativos** (até 50 dias) cujo risco **subiu** de uma avaliação para a seguinte (por exemplo, do dia 10 para o dia 14), em mais de 3 pontos percentuais. Se o nível de risco mudou, por exemplo de "Médio" para "Alto", aparece na coluna *Nível de risco antes → agora*.

**Ordem de atenção** (posição 1 = olhar primeiro): `prioridade = chance de calote × valor do contrato × (1 + piora)`. Contrato grande, com chance alta e que está piorando rápido, sobe para o topo.

Quando a confiança do modelo é "Baixa" ou "Nenhuma" (primeiros dias), trate a piora como alerta, não como certeza.
"""

RISCO = N.sql_mapa("faixa_risco", N.FAIXAS)
CONF = N.sql_mapa("confianca_modelo", N.CONFIANCA)
STATUS = "transform(status, ['PAID', 'LATE', 'PENDING', 'PARTIAL', 'CANCELLED'], ['Paga', 'Atrasada', 'A vencer', 'Paga em parte', 'Cancelada'], status)"
FATOR_MOTIVO = N.sql_fator("JSONExtractString(f, 'fator')")

ABAS = {
    "Consulta por contrato": [
        [({"text": T_FICHA}, 24, 5)],
        [(usa_filtro(tab("Contratos do cliente",
                         f"SELECT nome AS `Cliente`, telefone AS `Telefone`, contrato_id AS `Código do contrato`, data_inicio AS `Início`, {N.sql_mapa('tipo_contrato', N.TIPOS)} AS `Tipo`, "
                         "valor_principal AS `Valor (R$)`, qtd_parcelas AS `Parcelas`, provedor_pagamento AS `Provedor de cobrança`, if(pix_agendado = 1, 'Sim', 'Não') AS `Pix agendado`, "
                         f"idade_dias AS `Dias de contrato`, {N.sql_etapa('etapa_motor')} AS `Avaliação usada`, round(prob_calote * 100, 1) AS `Chance de calote (%)`, "
                         "round(prob_inadimplencia * 100, 1) AS `Chance de inadimplência (%)`, round(prob_cluster_ruim * 100, 1) AS `Chance de cluster ruim (%)`, "
                         f"{RISCO} AS `Nível de risco`, {CONF} AS `Confiança do modelo`, {N.sql_mapa('cluster_observado', N.CLUSTERS)} AS `Grupo em que realmente caiu`, status AS `Situação`, "
                         "parcelas_pagas AS `Parcelas pagas`, parcelas_atrasadas AS `Parcelas atrasadas`, maior_atraso_em_aberto_dias AS `Maior atraso em aberto (dias)`, saldo_em_aberto AS `Saldo em aberto (R$)` "
                         f"FROM analytics.contrato_consulta WHERE 1=1 {FILTRO} ORDER BY data_inicio DESC LIMIT 30")), 24, 4)],
        [(usa_filtro(barra("Chance de o contrato cair em cada grupo de comportamento (contrato mais recente)",
                           f"SELECT {N.sql_mapa('s.cluster', N.CLUSTERS)} AS `Grupo de comportamento`, round(s.probabilidade * 100, 1) AS `Chance (%)` FROM analytics.contrato_cluster_chances s "
                           f"WHERE s.contrato_id = {ULTIMO} ORDER BY s.probabilidade DESC", "Grupo de comportamento", ["Chance (%)"], "", "Chance (%)")), 12, 8),
         (usa_filtro(barra("Chance de calote, inadimplência e cluster ruim",
                           "SELECT `Tipo de problema`, `Chance (%)` FROM (SELECT 'Calote' AS `Tipo de problema`, round(prob_calote * 100, 1) AS `Chance (%)`, 1 AS o "
                           f"FROM analytics.contrato_consulta WHERE contrato_id = {ULTIMO} "
                           f"UNION ALL SELECT 'Inadimplência (atraso)', round(prob_inadimplencia * 100, 1), 2 FROM analytics.contrato_consulta WHERE contrato_id = {ULTIMO} "
                           f"UNION ALL SELECT 'Cluster ruim (paga no começo e para)', round(prob_cluster_ruim * 100, 1), 3 FROM analytics.contrato_consulta WHERE contrato_id = {ULTIMO}) ORDER BY o",
                           "Tipo de problema", ["Chance (%)"], "", "Chance (%)")), 12, 8)],
        [(usa_filtro(barra("O risco de calote subiu ou desceu ao longo do contrato?",
                           f"SELECT {N.sql_etapa('etapa')} AS `Momento do contrato`, round(prob_calote * 100, 1) AS `Chance de calote (%)` FROM analytics.score_contrato_etapas "
                           f"WHERE contrato_id = {ULTIMO} AND data_referencia = (SELECT max(data_referencia) FROM analytics.score_contrato_etapas) ORDER BY etapa",
                           "Momento do contrato", ["Chance de calote (%)"], "", "Chance de calote (%)", display="line")), 12, 8),
         (usa_filtro(tab("O que mais empurra o risco deste contrato para cima",
                         f"SELECT {FATOR_MOTIVO} AS `Fator`, round(JSONExtractFloat(f, 'efeito'), 3) AS `Quanto pesa no risco` FROM analytics.contrato_consulta "
                         f"ARRAY JOIN JSONExtractArrayRaw(motivos) AS f WHERE contrato_id = {ULTIMO} ORDER BY `Quanto pesa no risco` DESC")), 12, 8)],
        [(usa_filtro(tab("Parcelas do contrato",
                         f"SELECT numero_parcela AS `Parcela`, data_vencimento AS `Vencimento`, data_pagamento AS `Pago em`, {STATUS} AS `Situação`, dias_atraso_ate_pagar AS `Dias de atraso ao pagar`, "
                         "dias_atraso_em_aberto AS `Dias de atraso em aberto`, valor_total AS `Valor da parcela (R$)`, valor_pago AS `Valor pago (R$)` "
                         f"FROM analytics.fato_parcela WHERE contrato_id = {ULTIMO} ORDER BY numero_parcela")), 14, 9),
         (usa_filtro(tab("Resumo do cliente",
                         "SELECT r.resumo_estruturado AS `Resumo do cliente` FROM analytics.resumo_pessoa r FINAL WHERE r.cliente_id IN "
                         f"(SELECT cliente_id FROM analytics.contrato_consulta WHERE contrato_id = {ULTIMO}) ORDER BY r.gerado_em DESC LIMIT 1")), 10, 9)],
    ],
    "Contratos que pioraram": [
        [({"text": T_PIOR}, 24, 4)],
        [(sc("Contratos que pioraram", "SELECT count() AS `Contratos que pioraram` FROM analytics.contrato_piorou"), 8, 3),
         (sc("Valor dos contratos que pioraram (R$)", "SELECT sum(valor_principal) AS `Valor (R$)` FROM analytics.contrato_piorou"), 8, 3),
         (sc("Contratos que mudaram para um nível de risco pior", "SELECT countIf(faixa_anterior != faixa_atual) AS `Contratos` FROM analytics.contrato_piorou"), 8, 3)],
        [(tab("Contratos que pioraram: do que mais precisa de atenção (1) para o que menos",
              "SELECT posicao AS `Posição`, nome AS `Cliente`, telefone AS `Telefone`, contrato_id AS `Código do contrato`, idade_dias AS `Dias de contrato`, valor_principal AS `Valor (R$)`, "
              "saldo_em_aberto AS `Saldo em aberto (R$)`, maior_atraso_dias AS `Maior atraso (dias)`, "
              f"concat({N.sql_etapa('etapa_anterior')}, ' → ', {N.sql_etapa('etapa_atual')}) AS `Avaliações comparadas`, round(prob_anterior * 100, 1) AS `Chance de calote antes (%)`, "
              "round(prob_atual * 100, 1) AS `Chance de calote agora (%)`, round(piora * 100, 1) AS `Piora (pontos percentuais)`, "
              f"concat({N.sql_mapa('faixa_anterior', N.FAIXAS)}, ' → ', {N.sql_mapa('faixa_atual', N.FAIXAS)}) AS `Nível de risco antes → agora`, round(prob_cluster_ruim * 100, 1) AS `Chance de cluster ruim (%)`, "
              f"{N.sql_mapa('confianca', N.CONFIANCA)} AS `Confiança do modelo`, prioridade AS `Prioridade (maior = olhar primeiro)` FROM analytics.contrato_piorou ORDER BY posicao LIMIT 100"), 24, 12)],
        [(barra("Contratos que pioraram, por nível de risco atual",
                f"SELECT {N.sql_mapa('faixa_atual', N.FAIXAS)} AS `Nível de risco atual`, count() AS `Contratos` FROM analytics.contrato_piorou GROUP BY faixa_atual ORDER BY count() DESC",
                "Nível de risco atual", ["Contratos"], "", "Contratos"), 12, 7),
         (barra("Quanto o risco piorou em média, por momento do contrato (pontos percentuais)",
                f"SELECT {N.sql_etapa('etapa_atual')} AS `Momento do contrato`, round(avg(piora) * 100, 1) AS `Piora média (pontos percentuais)` FROM analytics.contrato_piorou GROUP BY etapa_atual ORDER BY etapa_atual",
                "Momento do contrato", ["Piora média (pontos percentuais)"], "", "Piora média (pontos percentuais)"), 12, 7)],
    ],
}


from mb3_conteudo import ABAS3  # noqa: E402

ABAS.update(ABAS3)   # aba "Motor de crédito"

from mb_ajuda import AJUDA  # noqa: E402

for _linhas in ABAS.values():   # texto de ajuda ao lado de cada cartao (chave = titulo do cartao)
    for _linha in _linhas:
        for _it, _w, _h in _linha:
            if _it.get("name") in AJUDA:
                _it["ajuda"] = AJUDA[_it["name"]]
