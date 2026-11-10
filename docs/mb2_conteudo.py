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
- **Chance de inadimplência:** probabilidade de 30% ou mais das parcelas até o dia 45 serem pagas com mais de 3 dias de atraso, ou ficarem em aberto. Quase todo cliente atrasa alguma coisa, então esse número é alto para muita gente.
- **Chance de cluster ruim:** probabilidade de o cliente pagar só no começo e parar, ou quase não pagar (padrão de abandono ou golpe).
- **Chance em cada grupo de comportamento:** as barras somam 100%. A maior barra é o destino mais provável do contrato.
- **Confiança do modelo:** vale mais que o número. Na assinatura (dia 0) o modelo quase não separa bom de mau pagador. A partir do dia 5 já separa, e nos dias 7 e 14 separa bem.
- **Risco ao longo do contrato:** mostra se o risco subiu ou desceu com o passar dos dias.
"""

T_PIOR = """## Como ler esta aba

Lista os contratos **ativos** (até 50 dias) cujo risco **subiu** de uma avaliação para a seguinte (por exemplo, do dia 10 para o dia 14), em mais de 3 pontos percentuais. Se o nível de risco mudou, por exemplo de "Médio" para "Alto", aparece na coluna *Nível de risco antes → agora*.

**Ordem de atenção** (posição 1 = olhar primeiro): `prioridade = chance de calote × valor do contrato × (1 + piora)`. Contrato grande, com chance alta e que está piorando rápido, sobe para o topo.

Quando a confiança do modelo é "Baixa" ou "Nenhuma" (primeiros dias), trate a piora como alerta, não como certeza.
"""

T_API = """## Como chamar a API de risco

**Endereço:** `http://169.58.240.203:8000` (rede da Capital Certo). **Chave:** vai no cabeçalho `X-API-Key`. Ela fica guardada no cofre de senhas (arquivo `capitalcerto-risk-api.json`) e **não** é escrita aqui.

**Consulta por telefone, CPF ou CNPJ** (devolve tudo que a base sabe sobre a pessoa e o risco):

```
curl -s -X POST http://169.58.240.203:8000/v1/consulta \\
  -H "X-API-Key: SUA_CHAVE" -H "Content-Type: application/json" \\
  -d '{"telefone": "62999998888"}'
```

Também aceita `{"cpf": "..."}` ou `{"cnpj": "..."}`. Para simular um contrato novo, mande junto `"contrato": {"valor": 1000, "parcelas": 60, "provedor": "PLUGGY", "pix_agendado": true}`.

**Outros endereços (mesma chave):**
- `GET /v1/consulta?telefone=62999998888`: o mesmo, por GET.
- `GET /v1/score/contrato/{código do contrato}`: risco atual de um contrato.
- `GET /v1/clusters`: os grupos de comportamento de pagamento, tamanho e taxa de calote.
- `GET /v1/renovacao/{código do contrato}`: como foi o contrato anterior, para renovação.
- `GET /v1/modelo`: quão confiável é cada etapa do modelo.

**O que volta para cada contrato do cliente (`risco.contratos_existentes`):**
- `prob_calote`, `prob_inadimplencia`, `prob_cluster_ruim`: as três chances, de 0 a 1.
- `faixa_risco`: baixo, medio, alto ou muito_alto.
- `confianca_modelo`: nenhuma, baixa, media ou alta.
- `cluster_trajetoria`: o grupo de comportamento de pagamento, quando já se sabe.
- `motivos`: até 3 fatores que mais empurram o risco para cima.
- `calote_observado`: o que realmente aconteceu, quando o contrato já amadureceu.

**Para contrato novo (`risco.novo_contrato`):** a simulação na assinatura traz também `leitura_observada_dia0`, que é a taxa real de calote de clientes parecidos, com margem de erro de 95%. Sem informar provedor e Pix, a API assume Pix agendado, porque quase todo contrato novo usa Pix hoje.

**Como ler a confiança:** com confiança "nenhuma" ou "baixa", o nível de risco não ordena os clientes com segurança. Decida pela leitura observada, não pela faixa.
"""

T_PESOS = """## Como o risco é calculado, passo a passo

**1. Um modelo para cada idade do contrato.** O mesmo contrato é avaliado de novo nos dias 0, 1, 2, 3, 5, 7, 10 e 14. Cada avaliação só usa o que já se sabia naquele dia, nada do futuro.

**2. O que cada avaliação olha.**
- **Dia 0 (assinatura):** o **canal de cobrança** (Pix agendado ou não, provedor) explica quase tudo que dá para prever. Os testes mostraram que acrescentar idade, tom da conversa, valor ou endereço não melhora.
- **Dias 1 a 3:** o comportamento de pagamento ainda é pouco; aqui o cadastro ajuda um pouco (endereço dividido com outro cliente, contrato anterior, inconsistências).
- **A partir do dia 5:** o **comportamento de pagamento** (parcelas pagas no prazo, atrasadas e em aberto, maior atraso) domina, e a avaliação melhora com os dias.

**3. Como funcionam os pesos.** Cada fator recebe um peso, e os pesos são somados. Peso que empurra para cima aumenta o risco; peso que empurra para baixo diminui. A tabela abaixo mostra os fatores mais fortes de cada momento. Em alguns momentos o modelo é uma conta de pesos (regressão logística); em outros é um conjunto de árvores de decisão, e o peso é o quanto aquele fator ajudou nas decisões das árvores. As escalas são diferentes, então compare os pesos só dentro do mesmo momento.

**4. Transformando em probabilidade.** A saída bruta do modelo é ajustada comparando o que ele previu com o que realmente aconteceu em contratos que ele não viu no treino (treina no passado, testa no mês seguinte). Assim, "30%" significa cerca de 30% de calote de verdade.

**5. Nível de risco.** Baixo: menos de 15%. Médio: de 15% a 30%. Alto: de 30% a 50%. Muito alto: 50% ou mais.

**6. Confiança do modelo.** Vem do acerto medido em contratos que o modelo não viu (0,5 = chute, 1 = perfeito): abaixo de 0,58 é "Nenhuma", até 0,68 "Baixa", até 0,78 "Média" e acima disso "Alta".

**7. Três tipos de previsão.** *Calote*: 20% ou mais das parcelas vencidas e em aberto. *Inadimplência*: 30% ou mais das parcelas com mais de 3 dias de atraso ou em aberto. *Cluster ruim*: o jeito de pagar cai em "paga no começo e para" ou "quase não paga".

**8. Os grupos de comportamento (clusters).** Contratos já maduros (45 dias mais 30 de prazo para regularizar) são agrupados automaticamente pelo jeito como pagaram: paga em dia, atraso moderado, atraso longo, paga no começo e para. A chance de cada grupo vem de um modelo estatístico sobre o comportamento de pagamento até aquele dia.

**9. Atualização diária.** Todo dia o modelo é refeito e testado, e só substitui o anterior se não piorar mais de 0,05 no acerto.

**10. O que o modelo ainda não sabe.** Na assinatura o acerto é de cerca de 0,65 (confiança "Baixa"). Bureau, conversa, nome, ramo, fotos e tom da conversa não separaram bom de mau pagador de forma estável. Para melhorar o dia 0 o caminho é dado novo (extrato e saldo por Open Finance) e a escolha do canal de cobrança, não mais variáveis do mesmo tipo.

**11. Você controla as variáveis.** Na aba **Motor de crédito** do Data IA Hub cada variável tem uma chave de liga/desliga e a correlação com calote, inadimplência e cada grupo de devedor. Variável desligada não entra no próximo treino. Também dá para pedir pelo chat uma variável nova para teste.
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
    "API e pesos": [
        [({"text": T_API}, 12, 17), ({"text": T_PESOS}, 12, 17)],
        [(tab("Fatores mais importantes de cada momento (previsão de calote)",
              f"SELECT {N.sql_etapa('i.etapa')} AS `Momento do contrato`, {N.sql_mapa('e.modelo', N.MODELOS)} AS `Tipo de modelo`, {N.sql_fator('i.fator')} AS `Fator`, round(i.importancia, 3) AS `Peso` "
              "FROM analytics_ml.motor_importancia i LEFT JOIN (SELECT etapa, any(modelo) AS modelo FROM analytics_ml.motor_execucoes WHERE escolhido = 1 AND alvo = 'calote' "
              "AND run_id = (SELECT max(run_id) FROM analytics_ml.motor_execucoes) GROUP BY etapa) e ON e.etapa = i.etapa "
              "WHERE i.alvo = 'calote' AND i.run_id = (SELECT max(run_id) FROM analytics_ml.motor_importancia WHERE alvo = 'calote') AND i.etapa IN (0, 3, 7, 14) ORDER BY i.etapa, `Peso` DESC"), 12, 12),
         (tab("Quão confiável é o modelo em cada momento (testado em contratos que ele não viu)",
              f"SELECT {N.sql_mapa('alvo', N.ALVOS)} AS `Tipo de previsão`, {N.sql_etapa('etapa')} AS `Momento do contrato`, {N.sql_mapa('modelo', N.MODELOS)} AS `Modelo escolhido`, "
              f"{N.sql_mapa('features', N.CONJUNTOS)} AS `Informações usadas`, auc AS `Acerto (0,5 = chute, 1 = perfeito)`, auc_ic_inf AS `Acerto mínimo provável`, auc_ic_sup AS `Acerto máximo provável`, "
              f"{N.sql_mapa('confianca', N.CONFIANCA)} AS `Confiança`, n AS `Contratos no teste` FROM analytics.risco_acuracia_modelo "
              "WHERE run_id = (SELECT max(run_id) FROM analytics.risco_acuracia_modelo) ORDER BY alvo, etapa"), 12, 12)],
    ],
}


from mb3_conteudo import ABAS3  # noqa: E402

ABAS.update(ABAS3)   # abas "Motor de crédito" e "Mapa de testes"

from mb_ajuda import AJUDA  # noqa: E402

for _linhas in ABAS.values():   # texto de ajuda ao lado de cada cartao (chave = titulo do cartao)
    for _linha in _linhas:
        for _it, _w, _h in _linha:
            if _it.get("name") in AJUDA:
                _it["ajuda"] = AJUDA[_it["name"]]
