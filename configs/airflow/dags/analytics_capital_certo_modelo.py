# Modelo dimensional da Capital Certo (ClickHouse, banco `analytics`).
# Cruza trusted_credhub (CRM, contratos e cobranca) com trusted_chatwoot (atendimento e funil de credito).
# Cada entrada: (nome, dependencias, sql). O sql pode ter varios comandos separados por ";;".
# Placeholders: {db} banco analytics, {ref} data de referencia (data de corte do CRM, ver DAG).
# Atencao: nao usar chaves { } soltas no SQL (o texto passa por str.format).
#
# Convencoes: chaves naturais do CRM (texto cuid) e do Chatwoot (inteiro); sem chave substituta, porque a
# origem ja tem id estavel. Dimensao = descreve; fato = mede. Inadimplencia nao e dimensao: e MEDIDA, guardada
# em fato_parcela (por parcela) e fato_inadimplencia_contrato_dia (fotografia diaria por contrato, acumula
# historico). A situacao ATUAL do cliente aparece como atributo de dim_cliente (derivado, sobrescrito a cada rodada).

DATABASE = "analytics"


def NORM(x):
    """nome sem acento, minusculo, so letras e espacos (para comparar nomes entre Chatwoot e CRM)"""
    return f"trimBoth(replaceRegexpAll(normalizeUTF8NFD(lowerUTF8({x})), '[^a-z ]', ''))"


STAGE_CLASS = """
multiIf(upperUTF8(s.name) = 'PROBLEMÁTICOS', 'problematico',
        upperUTF8(s.name) LIKE 'CRÉDITO%REPROVADO%', 'credito_reprovado',
        upperUTF8(s.name) LIKE 'CRÉDITO%APROVADO%', 'credito_aprovado',
        upperUTF8(s.name) = 'VENDIDO', 'vendido',
        upperUTF8(s.name) = 'DESQUALIFICADO', 'desqualificado',
        upperUTF8(s.name) LIKE 'CADASTRO%REPROVADO%', 'cadastro_reprovado',
        upperUTF8(s.name) LIKE '%APROVADO%', 'cadastro_aprovado',
        upperUTF8(s.name) LIKE 'CLIENTE%DESISTIU%', 'desistiu',
        upperUTF8(s.name) LIKE 'LEADS%', 'lead',
        upperUTF8(s.name) LIKE 'REMARKETING%', 'remarketing',
        'outro')
"""

ESTAGIO_CTE = """estagios AS (
    SELECT s.id AS estagio_id, s.name AS estagio, f.name AS funil, s.position AS posicao, """ + STAGE_CLASS + """ AS classe
    FROM trusted_chatwoot.kanban_stages AS s FINAL JOIN trusted_chatwoot.kanban_funnels AS f FINAL ON f.id = s.kanban_funnel_id
)"""

T = []  # (nome, deps, sql)

# ---------------------------------------------------------------- DIMENSOES SIMPLES
T.append(("dim_tenant", [], """
CREATE OR REPLACE TABLE {db}.dim_tenant ENGINE = MergeTree ORDER BY tenant_id AS
SELECT id AS tenant_id, slug AS tenant, name AS nome,
       multiIf(slug IN ('capital-certo', 'castelo-forte'), 'Capital Certo', slug = 'gotech-ds', 'Gotech', 'Outros') AS empresa,
       is_active AS ativo, created_at AS criado_em
FROM trusted_credhub.tenant
"""))

T.append(("dim_data", [], """
CREATE OR REPLACE TABLE {db}.dim_data ENGINE = MergeTree ORDER BY data AS
SELECT d AS data, toYear(d) AS ano, toQuarter(d) AS trimestre, toMonth(d) AS mes, toDayOfMonth(d) AS dia,
       toISOWeek(d) AS semana_iso, toDayOfWeek(d) AS dia_da_semana, toStartOfMonth(d) AS inicio_do_mes,
       formatDateTime(d, '%Y-%m') AS ano_mes, toDayOfWeek(d) >= 6 AS fim_de_semana,
       toDayOfWeek(d) < 6 AS dia_util_sem_feriados
FROM (SELECT toDate('2020-01-01') + number AS d FROM numbers(4018))
"""))

T.append(("dim_canal_chatwoot", [], """
CREATE OR REPLACE TABLE {db}.dim_canal_chatwoot ENGINE = MergeTree ORDER BY canal_id AS
SELECT i.id AS canal_id, i.name AS nome, i.channel_type AS tipo_canal,
       multiIf(positionCaseInsensitiveUTF8(i.name, 'neovision') > 0, 'NeoVision',
               positionCaseInsensitiveUTF8(i.name, 'capitalcerto') > 0 OR positionCaseInsensitiveUTF8(i.name, 'capital certo') > 0, 'Capital Certo',
               positionCaseInsensitiveUTF8(i.name, 'tráfego') > 0 OR positionCaseInsensitiveUTF8(i.name, 'trafego') > 0, 'Tráfego pago',
               'Outros') AS marca_linha,
       i.created_at AS criado_em
FROM trusted_chatwoot.inboxes AS i FINAL
"""))

T.append(("dim_estagio_funil", [], """
CREATE OR REPLACE TABLE {db}.dim_estagio_funil ENGINE = MergeTree ORDER BY estagio_id AS
WITH """ + ESTAGIO_CTE + """
SELECT estagio_id, estagio, funil, posicao, classe,
       multiIf(classe = 'lead', 1, classe = 'remarketing', 1, classe IN ('cadastro_aprovado', 'cadastro_reprovado'), 2,
               classe IN ('credito_aprovado', 'credito_reprovado'), 3, classe = 'vendido', 4, 9) AS etapa_macro,
       classe IN ('cadastro_reprovado', 'credito_reprovado', 'desqualificado', 'desistiu', 'problematico') AS estagio_negativo
FROM estagios
"""))

T.append(("dim_classificacao_operacional", [], """
CREATE OR REPLACE TABLE {db}.dim_classificacao_operacional ENGINE = MergeTree ORDER BY classificacao_id AS
SELECT id AS classificacao_id, tenant_id, label AS rotulo, description AS descricao, active AS ativa, sort_order AS ordem, created_at AS criada_em
FROM trusted_credhub.tenant_classification
"""))

# ---------------------------------------------------------------- CRUZAMENTO LEAD x CLIENTE
T.append(("ponte_lead_cliente", ["extrair_documentos_chatwoot"], """
CREATE OR REPLACE TABLE {db}.ponte_lead_cliente ENGINE = MergeTree ORDER BY (cliente_id, contato_id) AS
WITH cli AS (
    SELECT id AS cliente_id, tenant_id, cpf_cnpj, responsible_cpf, concat(left(phone, 2), right(phone, 8)) AS chave_tel,
           """ + NORM("ifNull(name, '')") + """ AS nome_norm
    FROM trusted_credhub.customer WHERE deleted_at IS NULL
),
ctt AS (
    SELECT contato_id, chave_tel, nome_norm FROM (
        SELECT id AS contato_id, replaceRegexpAll(ifNull(phone_number, ''), '[^0-9]', '') AS d, """ + NORM("ifNull(name, '')") + """ AS nome_norm
        FROM trusted_chatwoot.contacts FINAL
    ) ARRAY JOIN [concat(substring(d, 3, 2), right(d, 8))] AS chave_tel
    WHERE startsWith(d, '55') AND length(d) >= 12
),
tel AS (
    SELECT c.cliente_id AS cliente_id, k.contato_id AS contato_id, 1 AS via_telefone, 0 AS via_doc_titular, 0 AS via_doc_responsavel, 0 AS citacoes
    FROM cli c JOIN ctt k ON k.chave_tel = c.chave_tel
),
doc_t AS (
    SELECT c.cliente_id AS cliente_id, d.contato_id AS contato_id, 0 AS via_telefone, 1 AS via_doc_titular, 0 AS via_doc_responsavel, toUInt64(count()) AS citacoes
    FROM cli c JOIN analytics_stg.chatwoot_documentos_citados d ON d.documento = c.cpf_cnpj WHERE c.cpf_cnpj != '' GROUP BY c.cliente_id, d.contato_id
),
doc_r AS (
    SELECT c.cliente_id AS cliente_id, d.contato_id AS contato_id, 0 AS via_telefone, 0 AS via_doc_titular, 1 AS via_doc_responsavel, toUInt64(count()) AS citacoes
    FROM cli c JOIN analytics_stg.chatwoot_documentos_citados d ON d.documento = c.responsible_cpf WHERE c.responsible_cpf != '' GROUP BY c.cliente_id, d.contato_id
),
pares AS (
    SELECT cliente_id, contato_id, max(via_telefone) AS via_telefone, max(via_doc_titular) AS via_doc_titular,
           max(via_doc_responsavel) AS via_doc_responsavel, sum(citacoes) AS qtd_citacoes_documento
    FROM (SELECT cliente_id, contato_id, via_telefone, via_doc_titular, via_doc_responsavel, toUInt64(citacoes) AS citacoes FROM tel
          UNION ALL SELECT cliente_id, contato_id, via_telefone, via_doc_titular, via_doc_responsavel, citacoes FROM doc_t
          UNION ALL SELECT cliente_id, contato_id, via_telefone, via_doc_titular, via_doc_responsavel, citacoes FROM doc_r)
    GROUP BY cliente_id, contato_id
),
atividade AS (SELECT assumeNotNull(contact_id) AS contato_id, max(last_activity_at) AS ultima_atividade FROM trusted_chatwoot.conversations FINAL WHERE contact_id IS NOT NULL GROUP BY contact_id),
nomes_ctt AS (SELECT id AS contato_id, """ + NORM("ifNull(name, '')") + """ AS nome_norm FROM trusted_chatwoot.contacts FINAL),
enriq AS (
    SELECT p.cliente_id AS cliente_id, t.slug AS tenant, p.contato_id AS contato_id, p.via_telefone AS via_telefone,
           p.via_doc_titular AS via_doc_titular, p.via_doc_responsavel AS via_doc_responsavel, p.qtd_citacoes_documento AS qtd_citacoes_documento,
           if(c.nome_norm = '' OR n.nome_norm = '', NULL, round(least(1.0, jaroWinklerSimilarity(c.nome_norm, n.nome_norm)), 3)) AS similaridade_nome, a.ultima_atividade AS ultima_atividade
    FROM pares p
    JOIN cli c ON c.cliente_id = p.cliente_id
    JOIN trusted_credhub.tenant t ON t.id = c.tenant_id
    LEFT JOIN nomes_ctt n ON n.contato_id = p.contato_id
    LEFT JOIN atividade a ON a.contato_id = p.contato_id
),
pont AS (
    SELECT *,
        (via_doc_titular = 1 OR via_doc_responsavel = 1) AS via_documento,
        multiIf(via_telefone = 1 AND (via_doc_titular = 1 OR via_doc_responsavel = 1), 'alta',
                via_doc_titular = 1 OR via_doc_responsavel = 1, 'alta_documento',
                ifNull(similaridade_nome, 0) >= 0.8, 'media_telefone_nome', 'baixa_telefone') AS confianca,
        3 * toUInt8(via_doc_titular = 1 OR via_doc_responsavel = 1) + 2 * via_telefone + toUInt8(ifNull(similaridade_nome, 0) >= 0.8) AS pontuacao
    FROM enriq
)
SELECT *, row_number() OVER (PARTITION BY cliente_id ORDER BY pontuacao DESC, ultima_atividade DESC) = 1 AS vinculo_principal_do_cliente,
       confianca != 'baixa_telefone' AS vinculo_valido
FROM pont
"""))

# ---------------------------------------------------------------- FATOS DE FUNIL (CHATWOOT)
T.append(("fato_transicao_funil", ["dim_estagio_funil"], """
CREATE OR REPLACE TABLE {db}.fato_transicao_funil ENGINE = MergeTree ORDER BY (contato_id, ocorreu_em) AS
SELECT tr.id AS transicao_id, tr.conversation_id AS conversa_id, assumeNotNull(c.contact_id) AS contato_id, c.inbox_id AS canal_id,
       tr.from_stage_id AS estagio_origem_id, tr.to_stage_id AS estagio_destino_id,
       ifNull(eo.classe, '') AS classe_origem, ifNull(ed.classe, '') AS classe_destino,
       assumeNotNull(tr.performed_at) AS ocorreu_em, toDate(tr.performed_at) AS data,
       tr.time_in_previous_stage AS segundos_no_estagio_anterior,
       round(tr.time_in_previous_stage / 86400, 2) AS dias_no_estagio_anterior,
       ifNull(tr.reason, '') != '' AS tem_motivo
FROM trusted_chatwoot.kanban_stage_transitions AS tr FINAL
JOIN trusted_chatwoot.conversations AS c FINAL ON c.id = tr.conversation_id
LEFT JOIN {db}.dim_estagio_funil eo ON eo.estagio_id = tr.from_stage_id
LEFT JOIN {db}.dim_estagio_funil ed ON ed.estagio_id = tr.to_stage_id
WHERE c.contact_id IS NOT NULL
"""))

T.append(("chatwoot_contato_funil", ["dim_estagio_funil", "fato_transicao_funil"], """
CREATE OR REPLACE TABLE {db}.chatwoot_contato_funil ENGINE = MergeTree ORDER BY contato_id AS
WITH conv AS (
    SELECT assumeNotNull(c.contact_id) AS contato_id, c.id AS conversa_id, c.created_at AS created_at, c.last_activity_at AS last_activity_at,
           c.kanban_stage_id AS estagio_id, c.inbox_id AS canal_id
    FROM trusted_chatwoot.conversations AS c FINAL WHERE c.contact_id IS NOT NULL
),
msgs AS (
    SELECT m.conversation_id AS conversa_id, countIf(m.message_type = 0) AS msgs_do_cliente,
           countIf(m.message_type = 1 AND NOT ifNull(m.private, false)) AS msgs_enviadas
    FROM trusted_chatwoot.messages AS m FINAL GROUP BY m.conversation_id
),
trans AS (
    SELECT contato_id, count() AS qtd_transicoes,
           minIf(ocorreu_em, classe_destino = 'credito_aprovado') AS primeira_aprovacao_em,
           minIf(ocorreu_em, classe_destino = 'vendido') AS primeira_venda_em,
           countIf(classe_destino = 'problematico') > 0 AS ja_foi_problematico,
           countIf(classe_destino = 'credito_aprovado') > 0 AS ja_foi_credito_aprovado,
           countIf(classe_destino = 'credito_reprovado') > 0 AS ja_foi_credito_reprovado,
           countIf(classe_destino = 'cadastro_reprovado') > 0 AS ja_foi_cadastro_reprovado,
           countIf(classe_destino = 'vendido') > 0 AS ja_foi_vendido,
           countIf(classe_destino = 'desqualificado') > 0 AS ja_foi_desqualificado,
           countIf(classe_destino = 'desistiu') > 0 AS ja_desistiu
    FROM {db}.fato_transicao_funil GROUP BY contato_id
),
agg AS (
    SELECT cv.contato_id AS contato_id, count() AS qtd_conversas, min(cv.created_at) AS primeira_conversa_em,
           max(cv.last_activity_at) AS ultima_atividade_em,
           argMin(cv.canal_id, cv.created_at) AS canal_primeiro_contato_id,
           argMax(cv.estagio_id, cv.last_activity_at) AS estagio_atual_id,
           sum(ifNull(m.msgs_do_cliente, 0)) AS msgs_do_cliente, sum(ifNull(m.msgs_enviadas, 0)) AS msgs_enviadas
    FROM conv cv LEFT JOIN msgs m ON m.conversa_id = cv.conversa_id GROUP BY cv.contato_id
)
SELECT a.contato_id AS contato_id, a.qtd_conversas AS qtd_conversas, a.primeira_conversa_em AS primeira_conversa_em,
       a.ultima_atividade_em AS ultima_atividade_em, a.canal_primeiro_contato_id AS canal_primeiro_contato_id,
       e.estagio AS estagio_atual, e.funil AS funil_atual, e.classe AS classe_estagio_atual,
       a.msgs_do_cliente AS msgs_do_cliente, a.msgs_enviadas AS msgs_enviadas, ifNull(t.qtd_transicoes, 0) AS qtd_transicoes,
       t.primeira_aprovacao_em AS primeira_aprovacao_em, t.primeira_venda_em AS primeira_venda_em,
       dateDiff('day', a.primeira_conversa_em, t.primeira_aprovacao_em) AS dias_ate_primeira_aprovacao,
       ifNull(t.ja_foi_problematico, false) AS ja_foi_problematico, ifNull(t.ja_foi_credito_aprovado, false) AS ja_foi_credito_aprovado,
       ifNull(t.ja_foi_credito_reprovado, false) AS ja_foi_credito_reprovado, ifNull(t.ja_foi_cadastro_reprovado, false) AS ja_foi_cadastro_reprovado,
       ifNull(t.ja_foi_vendido, false) AS ja_foi_vendido, ifNull(t.ja_foi_desqualificado, false) AS ja_foi_desqualificado, ifNull(t.ja_desistiu, false) AS ja_desistiu
FROM agg a
LEFT JOIN {db}.dim_estagio_funil e ON e.estagio_id = a.estagio_atual_id
LEFT JOIN trans t ON t.contato_id = a.contato_id
"""))

# ---------------------------------------------------------------- FATOS FINANCEIROS (CRM)
T.append(("fato_parcela", ["dim_tenant"], """
CREATE OR REPLACE TABLE {db}.fato_parcela ENGINE = MergeTree ORDER BY (contrato_id, numero_parcela) AS
WITH toDate('{ref}') AS ref
SELECT i.id AS parcela_id, i.contract_id AS contrato_id, c.customer_id AS cliente_id, i.tenant_id AS tenant_id,
       i.installment_number AS numero_parcela, toDate(i.due_date) AS data_vencimento, toDate(i.paid_at) AS data_pagamento,
       toString(i.status) AS status,
       i.original_amount AS valor_principal, i.interest_amount AS valor_juros, i.penalty_amount AS valor_multa,
       i.total_amount AS valor_total, ifNull(i.paid_amount, 0) AS valor_pago,
       toString(i.status) IN ('LATE', 'PARTIAL', 'PENDING') AS em_aberto,
       if(toString(i.status) IN ('LATE', 'PARTIAL', 'PENDING'), i.total_amount - ifNull(i.paid_amount, 0), 0) AS valor_em_aberto,
       toString(i.status) IN ('LATE', 'PARTIAL', 'PENDING') AND toDate(i.due_date) < ref AS vencida_em_aberto,
       if(toString(i.status) IN ('LATE', 'PARTIAL', 'PENDING') AND toDate(i.due_date) < ref, dateDiff('day', toDate(i.due_date), ref), 0) AS dias_atraso_em_aberto,
       if(toString(i.status) = 'PAID' AND i.paid_at IS NOT NULL, greatest(0, dateDiff('day', toDate(i.due_date), toDate(i.paid_at))), NULL) AS dias_atraso_ate_pagar,
       toString(i.status) = 'PAID' AND toDate(i.paid_at) < toDate(i.due_date) AS pago_antecipado,
       toString(i.status) = 'PAID' AND toDate(i.paid_at) <= toDate(i.due_date) AS pago_no_prazo,
       multiIf(toString(i.status) = 'CANCELLED', 'cancelada',
               toString(i.status) = 'PAID', 'paga',
               NOT (toString(i.status) IN ('LATE', 'PARTIAL', 'PENDING') AND toDate(i.due_date) < ref), 'a_vencer',
               dateDiff('day', toDate(i.due_date), ref) <= 7, '01_a_07_dias',
               dateDiff('day', toDate(i.due_date), ref) <= 30, '08_a_30_dias',
               dateDiff('day', toDate(i.due_date), ref) <= 60, '31_a_60_dias',
               dateDiff('day', toDate(i.due_date), ref) <= 90, '61_a_90_dias', 'acima_de_90_dias') AS faixa_atraso,
       c.deleted_at IS NOT NULL AS contrato_excluido, tn.empresa AS empresa,
       ref AS data_referencia
FROM trusted_credhub.installment AS i
JOIN trusted_credhub.contract AS c ON c.id = i.contract_id
LEFT JOIN {db}.dim_tenant tn ON tn.tenant_id = i.tenant_id
"""))

T.append(("fato_pagamento", ["dim_tenant"], """
CREATE OR REPLACE TABLE {db}.fato_pagamento ENGINE = MergeTree ORDER BY (contrato_id, recebido_em) AS
WITH rec AS (
    SELECT payment_id, count() AS qtd_parcelas_conciliadas, min(toString(method)) AS metodo_conciliacao, min(created_at) AS conciliado_em
    FROM trusted_credhub.reconciliation GROUP BY payment_id
)
SELECT p.id AS pagamento_id, p.contract_id AS contrato_id, c.customer_id AS cliente_id, p.tenant_id AS tenant_id,
       p.installment_id AS parcela_id_direta, assumeNotNull(p.received_at) AS recebido_em, toDate(p.received_at) AS data_recebimento,
       toHour(p.received_at) AS hora_utc, p.amount AS valor, toString(p.payment_method) AS metodo, toString(p.status) AS status,
       toString(p.status) = 'RECONCILED' AS conciliado, toString(p.status) = 'UNMATCHED' AS sem_conciliacao,
       ifNull(r.qtd_parcelas_conciliadas, 0) AS qtd_parcelas_conciliadas, r.metodo_conciliacao AS metodo_conciliacao,
       r.conciliado_em AS conciliado_em, toString(c.payment_provider) AS provedor_contrato,
       dateDiff('day', toDate(c.start_date), toDate(p.received_at)) AS dias_desde_inicio_contrato, tn.empresa AS empresa
FROM trusted_credhub.payment AS p
LEFT JOIN trusted_credhub.contract AS c ON c.id = p.contract_id
LEFT JOIN {db}.dim_tenant tn ON tn.tenant_id = p.tenant_id
LEFT JOIN rec r ON r.payment_id = p.id
"""))

T.append(("fato_cobranca_whatsapp", ["dim_tenant"], """
CREATE OR REPLACE TABLE {db}.fato_cobranca_whatsapp ENGINE = MergeTree ORDER BY (contrato_id, enviada_em) AS
SELECT w.id AS mensagem_id, assumeNotNull(w.contract_id) AS contrato_id, w.customer_id AS cliente_id, w.tenant_id AS tenant_id, w.installment_id AS parcela_id,
       w.template_name AS template, multiIf(w.template_name LIKE '%leve%', 'leve', w.template_name LIKE '%medio%', 'medio', w.template_name LIKE '%critico%', 'critico', 'outro') AS nivel_cobranca,
       toString(w.status) AS status, assumeNotNull(w.created_at) AS enviada_em, toDate(w.created_at) AS data_envio,
       toString(w.status) IN ('DELIVERED', 'READ') AS entregue, toString(w.status) = 'READ' AS lida, toString(w.status) = 'FAILED' AS falhou,
       w.error_code AS codigo_erro,
       if(w.read_at IS NULL, NULL, dateDiff('minute', w.created_at, w.read_at)) AS minutos_ate_leitura,
       i.due_date AS vencimento_parcela, dateDiff('day', toDate(i.due_date), toDate(w.created_at)) AS dias_atraso_no_envio,
       i.total_amount AS valor_parcela, i.paid_at AS parcela_paga_em,
       i.paid_at IS NOT NULL AND i.paid_at >= w.created_at AND i.paid_at <= w.created_at + INTERVAL 3 DAY AS pagou_em_3d,
       i.paid_at IS NOT NULL AND i.paid_at >= w.created_at AND i.paid_at <= w.created_at + INTERVAL 7 DAY AS pagou_em_7d,
       multiIf(i.due_date IS NULL, 'sem_parcela', dateDiff('day', toDate(i.due_date), toDate(w.created_at)) <= 0, 'no_dia_ou_antes', dateDiff('day', toDate(i.due_date), toDate(w.created_at)) <= 7, '01_a_07_dias', dateDiff('day', toDate(i.due_date), toDate(w.created_at)) <= 30, '08_a_30_dias', 'acima_de_30_dias') AS faixa_atraso_no_envio,
       tn.empresa AS empresa
FROM trusted_credhub.whatsapp_message AS w
LEFT JOIN trusted_credhub.installment AS i ON i.id = w.installment_id
LEFT JOIN {db}.dim_tenant tn ON tn.tenant_id = w.tenant_id
"""))

# ---------------------------------------------------------------- CONTRATO
T.append(("dim_contrato", ["dim_classificacao_operacional", "dim_tenant"], """
CREATE OR REPLACE TABLE {db}.dim_contrato ENGINE = MergeTree ORDER BY contrato_id AS
WITH etq AS (
    SELECT cc.contract_id AS contrato_id, groupArray(tc.label) AS etiquetas
    FROM trusted_credhub.contract_custom_classification cc JOIN trusted_credhub.tenant_classification tc ON tc.id = cc.classification_id
    GROUP BY cc.contract_id
)
SELECT c.id AS contrato_id, c.customer_id AS cliente_id, c.tenant_id AS tenant_id, c.start_date AS data_inicio,
       toString(c.status) AS status, toString(c.classification) AS classificacao_problema,
       toString(c.payment_provider) AS provedor_pagamento, c.pix_agendado AS pix_agendado,
       toString(c.due_type) AS tipo_vencimento, toString(c.installment_interval) AS intervalo_parcelas,
       '' AS status_subconta, toString(c.pluggy_block_mode) AS modo_bloco_pluggy,
       c.principal_amount AS valor_principal, c.total_installments AS qtd_parcelas_contratadas,
       multiIf(c.principal_amount <= 200, 'ate_200', c.principal_amount <= 500, '201_a_500', c.principal_amount <= 1000, '501_a_1000',
               c.principal_amount <= 1500, '1001_a_1500', 'acima_de_1500') AS faixa_valor,
       multiIf(c.total_installments <= 30, 'ate_30', c.total_installments <= 60, '31_a_60', c.total_installments <= 90, '61_a_90', 'acima_de_90') AS faixa_prazo,
       c.interest_rate_percent AS taxa_juros_pct, c.late_fee_enabled AS multa_atraso_ativa, c.late_interest_enabled AS juros_mora_ativo,
       c.amendment_count AS qtd_aditivos, c.consent_renewal_count AS qtd_renovacoes_consentimento,
       c.awaiting_queue AS na_fila_de_espera, ifNull(e.etiquetas, []) AS etiquetas_operacionais,
       arrayExists(x -> upperUTF8(x) LIKE 'FUGA DE GARANTIA%', ifNull(e.etiquetas, [])) AS etiqueta_fuga_garantia,
       arrayExists(x -> upperUTF8(x) LIKE '%RECUPERA%' AND upperUTF8(x) NOT LIKE '%QUITADO%', ifNull(e.etiquetas, [])) AS etiqueta_recuperacao,
       arrayExists(x -> upperUTF8(x) LIKE '%MAQUININHA%' OR upperUTF8(x) LIKE '%ARRANJOS%', ifNull(e.etiquetas, [])) AS etiqueta_maquininha,
       arrayExists(x -> upperUTF8(x) IN ('AÇÃO EXECUTADA', 'EXECUTADO'), ifNull(e.etiquetas, [])) AS etiqueta_acao_executada,
       multiIf(arrayExists(x -> upperUTF8(x) LIKE 'FUGA DE GARANTIA%', ifNull(e.etiquetas, [])), 'Fuga de garantia',
               arrayExists(x -> upperUTF8(x) IN ('AÇÃO EXECUTADA', 'EXECUTADO'), ifNull(e.etiquetas, [])), 'Ação executada',
               arrayExists(x -> upperUTF8(x) LIKE '%RECUPERA%' AND upperUTF8(x) NOT LIKE '%QUITADO%', ifNull(e.etiquetas, [])), 'Em recuperação',
               arrayExists(x -> upperUTF8(x) LIKE '%MAQUININHA%' OR upperUTF8(x) LIKE '%ARRANJOS%', ifNull(e.etiquetas, [])), 'Cobrança na maquininha',
               'Sem etiqueta de problema') AS situacao_operacional,
       c.deleted_at IS NOT NULL AS excluido, c.deleted_at AS excluido_em, c.created_at AS criado_em, tn.empresa AS empresa
FROM trusted_credhub.contract AS c LEFT JOIN etq e ON e.contrato_id = c.id
LEFT JOIN {db}.dim_tenant tn ON tn.tenant_id = c.tenant_id
"""))

T.append(("fato_contrato", ["fato_parcela", "fato_pagamento", "fato_cobranca_whatsapp", "dim_contrato", "dim_tenant"], """
CREATE OR REPLACE TABLE {db}.fato_contrato ENGINE = MergeTree ORDER BY contrato_id AS
WITH parc AS (
    SELECT contrato_id,
           countIf(status != 'CANCELLED') AS parcelas_total, countIf(status = 'PAID') AS parcelas_pagas,
           countIf(status = 'LATE') AS parcelas_atrasadas, countIf(status = 'PARTIAL') AS parcelas_parciais, countIf(status = 'PENDING') AS parcelas_a_vencer,
           sumIf(valor_total, status != 'CANCELLED') AS valor_devido_total, sum(valor_pago) AS valor_pago_total, sum(valor_em_aberto) AS saldo_em_aberto,
           sumIf(valor_em_aberto, vencida_em_aberto) AS valor_vencido_em_aberto,
           max(dias_atraso_em_aberto) AS maior_atraso_em_aberto_dias, max(ifNull(dias_atraso_ate_pagar, 0)) AS maior_atraso_pago_dias,
           countIf(pago_antecipado) AS parcelas_pagas_antecipadas, countIf(vencida_em_aberto) AS parcelas_vencidas_em_aberto,
           max(data_vencimento) AS ultimo_vencimento_em, max(data_pagamento) AS ultimo_pagamento_em
    FROM {db}.fato_parcela GROUP BY contrato_id
),
pag AS (SELECT contrato_id, count() AS pagamentos_qtd, sum(valor) AS pagamentos_valor, countIf(sem_conciliacao) AS pagamentos_sem_conciliacao FROM {db}.fato_pagamento GROUP BY contrato_id),
inter AS (SELECT contract_id AS contrato_id, count() AS interacoes_qtd FROM trusted_credhub.contract_interaction GROUP BY contract_id),
tick AS (SELECT contract_id AS contrato_id, count() AS chamados_suporte_qtd FROM trusted_credhub.support_ticket WHERE contract_id IS NOT NULL GROUP BY contract_id),
wpp AS (SELECT contrato_id, count() AS cobrancas_whatsapp_qtd, countIf(lida) AS cobrancas_lidas FROM {db}.fato_cobranca_whatsapp GROUP BY contrato_id),
base AS (
    SELECT d.contrato_id AS contrato_id, d.cliente_id AS cliente_id, d.tenant_id AS tenant_id, d.empresa AS empresa,
           lower(hex(cityHash64(cu.cpf_cnpj))) AS pessoa_id, toDate(d.data_inicio) AS data_inicio,
           d.status AS status, d.valor_principal AS valor_principal, c.installment_value AS valor_parcela,
           d.qtd_parcelas_contratadas AS qtd_parcelas_contratadas, d.taxa_juros_pct AS taxa_juros_pct, d.criado_em AS criado_em,
           if(cu.birth_date IS NULL, NULL, dateDiff('year', cu.birth_date, c.start_date)) AS idade_cliente,
           p.parcelas_total AS parcelas_total, p.parcelas_pagas AS parcelas_pagas, p.parcelas_atrasadas AS parcelas_atrasadas,
           p.parcelas_parciais AS parcelas_parciais, p.parcelas_a_vencer AS parcelas_a_vencer, p.valor_devido_total AS valor_devido_total,
           p.valor_pago_total AS valor_pago_total, p.saldo_em_aberto AS saldo_em_aberto, p.valor_vencido_em_aberto AS valor_vencido_em_aberto,
           p.parcelas_vencidas_em_aberto AS parcelas_vencidas_em_aberto, p.maior_atraso_em_aberto_dias AS maior_atraso_em_aberto_dias,
           p.maior_atraso_pago_dias AS maior_atraso_pago_dias, p.parcelas_pagas_antecipadas AS parcelas_pagas_antecipadas,
           p.ultimo_vencimento_em AS ultimo_vencimento_em, p.ultimo_pagamento_em AS ultimo_pagamento_em,
           if(p.parcelas_total > 0 AND p.parcelas_pagas = p.parcelas_total, p.ultimo_pagamento_em, NULL) AS quitado_em,
           ifNull(g.pagamentos_qtd, 0) AS pagamentos_qtd, ifNull(g.pagamentos_valor, 0) AS pagamentos_valor,
           ifNull(g.pagamentos_sem_conciliacao, 0) AS pagamentos_sem_conciliacao, ifNull(i.interacoes_qtd, 0) AS interacoes_qtd,
           ifNull(k.chamados_suporte_qtd, 0) AS chamados_suporte_qtd, ifNull(w.cobrancas_whatsapp_qtd, 0) AS cobrancas_whatsapp_qtd,
           ifNull(w.cobrancas_lidas, 0) AS cobrancas_lidas, d.qtd_aditivos AS qtd_aditivos, d.classificacao_problema AS classificacao_problema,
           d.situacao_operacional AS situacao_operacional, d.etiqueta_fuga_garantia AS etiqueta_fuga_garantia
    FROM {db}.dim_contrato d
    JOIN trusted_credhub.contract c ON c.id = d.contrato_id
    JOIN trusted_credhub.customer cu ON cu.id = d.cliente_id
    LEFT JOIN parc p ON p.contrato_id = d.contrato_id LEFT JOIN pag g ON g.contrato_id = d.contrato_id
    LEFT JOIN inter i ON i.contrato_id = d.contrato_id LEFT JOIN tick k ON k.contrato_id = d.contrato_id LEFT JOIN wpp w ON w.contrato_id = d.contrato_id
    WHERE NOT d.excluido
),
seq AS (
    SELECT *, row_number() OVER w AS ordem_contrato_cliente, lagInFrame(valor_principal, 1) OVER w AS valor_contrato_anterior,
           lagInFrame(quitado_em, 1) OVER w AS quitado_em_contrato_anterior
    FROM base
    WINDOW w AS (PARTITION BY pessoa_id ORDER BY data_inicio, criado_em ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
),
seq2 AS (
    SELECT *, if(ordem_contrato_cliente > 1, valor_principal - valor_contrato_anterior, NULL) AS salto_valor,
           if(ordem_contrato_cliente > 1 AND valor_contrato_anterior > 0, valor_principal / valor_contrato_anterior, NULL) AS razao_valor_vs_anterior,
           if(quitado_em_contrato_anterior IS NULL, NULL, dateDiff('day', toDate(quitado_em_contrato_anterior), data_inicio)) AS dias_desde_quitacao_anterior
    FROM seq
)
SELECT *,
    max(ifNull(salto_valor, 0)) OVER (PARTITION BY pessoa_id ORDER BY data_inicio, criado_em ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS maior_salto_valor_historico,
    if(parcelas_total > 0, parcelas_pagas / parcelas_total, NULL) AS pct_parcelas_pagas,
    if(quitado_em IS NULL, NULL, dateDiff('day', data_inicio, toDate(quitado_em))) AS dias_ate_quitar,
    quitado_em IS NOT NULL AND toDate(quitado_em) < toDate(ultimo_vencimento_em) AS quitou_antes_do_prazo,
    ifNull(dias_desde_quitacao_anterior <= 1, false) AS reaplicou_ate_1d,
    ifNull(dias_desde_quitacao_anterior <= 3, false) AS reaplicou_ate_3d,
    ifNull(dias_desde_quitacao_anterior <= 7, false) AS reaplicou_ate_7d,
    status = 'LATE' OR parcelas_vencidas_em_aberto > 0 AS em_atraso,
    classificacao_problema = 'FUGA_DE_GARANTIA' OR etiqueta_fuga_garantia AS marcado_fuga_garantia,
    toDate('{ref}') AS data_referencia
FROM seq2
"""))

T.append(("fato_relatorio_credito", [], r"""
CREATE OR REPLACE TABLE {db}.fato_relatorio_credito ENGINE = MergeTree ORDER BY (contato_id, criado_em) AS
SELECT n.id AS nota_id, n.conversation_id AS conversa_id, assumeNotNull(c.contact_id) AS contato_id, assumeNotNull(n.created_at) AS criado_em,
       toUInt16OrNull(extract(n.content, '(?i)Score PF:[*\\s]*(\\d+)')) AS score_pf,
       upper(extract(n.content, '(?i)Score PF[^\\n]*?faixa\\s+([A-Ha-h])')) AS faixa_pf,
       toFloat64OrNull(replaceOne(extract(n.content, '(?i)Score PF[^\\n]*?inadimpl[^\\n0-9]*(\\d+[,.]\\d+)\\s*%'), ',', '.')) AS taxa_inadimplencia_pf_pct,
       toUInt16OrNull(extract(n.content, '(?i)Score PJ:[*\\s]*(\\d+)')) AS score_pj,
       toFloat64OrNull(replaceOne(extract(n.content, '(?i)Score PJ[^\\n]*?(?:inadimpl|probabilidade)[^\\n0-9]*(\\d+[,.]?\\d*)'), ',', '.')) AS taxa_inadimplencia_pj_pct,
       match(n.content, '(?i)SITUA[CÇ][AÃ]O DO CNPJ: INAPTO') AS cnpj_inapto,
       match(n.content, '(?i)DEFAULT') AS pj_em_default,
       length(n.content) AS tamanho_relatorio
FROM trusted_chatwoot.kanban_notes AS n FINAL
JOIN trusted_chatwoot.conversations AS c FINAL ON c.id = n.conversation_id
WHERE c.contact_id IS NOT NULL AND match(n.content, 'SISTEMA DORA')
"""))

# ---------------------------------------------------------------- CLIENTE e LEAD
T.append(("dim_cliente", ["fato_contrato", "ponte_lead_cliente", "chatwoot_contato_funil", "dim_tenant"], """
CREATE OR REPLACE TABLE {db}.dim_cliente ENGINE = MergeTree ORDER BY cliente_id AS
WITH ct AS (
    SELECT cliente_id, count() AS qtd_contratos, sum(valor_principal) AS total_emprestado, max(valor_principal) AS maior_contrato,
           min(data_inicio) AS primeiro_contrato_em, max(data_inicio) AS ultimo_contrato_em,
           countIf(status = 'PAID_OFF') AS contratos_quitados, countIf(status = 'ACTIVE') AS contratos_ativos,
           countIf(em_atraso) AS contratos_em_atraso, sum(valor_vencido_em_aberto) AS valor_vencido_em_aberto,
           sum(saldo_em_aberto) AS saldo_em_aberto, max(maior_atraso_em_aberto_dias) AS maior_atraso_em_aberto_dias,
           max(maior_atraso_pago_dias) AS maior_atraso_pago_dias, sum(valor_pago_total) AS total_pago
    FROM {db}.fato_contrato GROUP BY cliente_id
),
exc AS (SELECT customer_id AS cliente_id, count() AS contratos_excluidos FROM trusted_credhub.contract WHERE deleted_at IS NOT NULL GROUP BY customer_id),
lk AS (
    SELECT cliente_id, countIf(vinculo_valido) AS qtd_contatos_chatwoot_validos, count() AS qtd_contatos_chatwoot_candidatos,
           argMaxIf(contato_id, pontuacao, vinculo_principal_do_cliente) AS contato_chatwoot_principal_id,
           argMaxIf(confianca, pontuacao, vinculo_principal_do_cliente) AS confianca_vinculo,
           maxIf(toUInt8(vinculo_valido), vinculo_principal_do_cliente) AS tem_vinculo_valido
    FROM {db}.ponte_lead_cliente GROUP BY cliente_id
)
SELECT cu.id AS cliente_id, cu.tenant_id AS tenant_id, t.tenant AS tenant, t.empresa AS empresa, lower(hex(cityHash64(cu.cpf_cnpj))) AS pessoa_id, ifNull(cu.name, '') AS nome,
       if(length(cu.cpf_cnpj) = 14, 'CNPJ', 'CPF') AS tipo_pessoa, right(cu.cpf_cnpj, 4) AS documento_final,
       ifNull(toString(cu.segment), '') AS segmento, ifNull(toString(cu.sex), '') AS sexo, ifNull(toString(cu.company_type), '') AS tipo_empresa, cu.income_value AS renda_declarada,
       cu.birth_date AS data_nascimento, if(cu.birth_date IS NULL, NULL, dateDiff('year', cu.birth_date, toDate('{ref}'))) AS idade,
       multiIf(cu.birth_date IS NULL, 'sem_data', dateDiff('year', cu.birth_date, toDate('{ref}')) < 25, 'ate_24',
               dateDiff('year', cu.birth_date, toDate('{ref}')) < 35, '25_a_34', dateDiff('year', cu.birth_date, toDate('{ref}')) < 45, '35_a_44',
               dateDiff('year', cu.birth_date, toDate('{ref}')) < 55, '45_a_54', '55_ou_mais') AS faixa_etaria,
       ad.city AS cidade, ad.state AS uf, cu.created_at AS cliente_desde, cu.deleted_at IS NOT NULL AS excluido,
       ifNull(ct.qtd_contratos, 0) AS qtd_contratos, ifNull(ex.contratos_excluidos, 0) AS contratos_excluidos,
       ct.total_emprestado AS total_emprestado, ct.maior_contrato AS maior_contrato, ct.primeiro_contrato_em AS primeiro_contrato_em,
       ct.ultimo_contrato_em AS ultimo_contrato_em, ct.contratos_quitados AS contratos_quitados, ct.contratos_ativos AS contratos_ativos,
       ct.contratos_em_atraso AS contratos_em_atraso, ct.valor_vencido_em_aberto AS valor_vencido_em_aberto, ct.saldo_em_aberto AS saldo_em_aberto,
       ct.maior_atraso_em_aberto_dias AS maior_atraso_em_aberto_dias, ct.maior_atraso_pago_dias AS maior_atraso_pago_dias, ct.total_pago AS total_pago,
       multiIf(ifNull(ct.qtd_contratos, 0) = 0, 'sem_contrato', ifNull(ct.contratos_em_atraso, 0) > 0, 'inadimplente',
               ifNull(ct.contratos_ativos, 0) > 0, 'adimplente_com_contrato_ativo', 'quitado_sem_pendencia') AS situacao_atual,
       ifNull(lk.tem_vinculo_valido, 0) = 1 AS tem_vinculo_chatwoot, lk.contato_chatwoot_principal_id AS contato_chatwoot_principal_id,
       lk.confianca_vinculo AS confianca_vinculo, ifNull(lk.qtd_contatos_chatwoot_validos, 0) AS qtd_contatos_chatwoot_validos
FROM trusted_credhub.customer AS cu
JOIN {db}.dim_tenant t ON t.tenant_id = cu.tenant_id
LEFT JOIN trusted_credhub.addresses ad ON ad.entity_id = cu.id
LEFT JOIN ct ON ct.cliente_id = cu.id LEFT JOIN exc ex ON ex.cliente_id = cu.id LEFT JOIN lk ON lk.cliente_id = cu.id
"""))

T.append(("dim_lead", ["chatwoot_contato_funil", "dim_canal_chatwoot"], """
CREATE OR REPLACE TABLE {db}.dim_lead ENGINE = MergeTree ORDER BY contato_id AS
SELECT k.id AS contato_id, k.created_at AS criado_em, toDate(k.created_at) AS data_criacao,
       JSONExtractString(ifNull(k.custom_attributes, '{{}}'), 'origem') AS origem_declarada,
       ifNull(f.qtd_conversas, 0) AS qtd_conversas, f.primeira_conversa_em AS primeira_conversa_em, f.ultima_atividade_em AS ultima_atividade_em,
       f.canal_primeiro_contato_id AS canal_primeiro_contato_id, ifNull(cn.nome, '') AS canal_primeiro_contato, ifNull(cn.marca_linha, '') AS marca_linha,
       ifNull(f.estagio_atual, '') AS estagio_atual, ifNull(f.funil_atual, '') AS funil_atual, ifNull(f.classe_estagio_atual, '') AS classe_estagio_atual,
       ifNull(f.msgs_do_cliente, 0) AS msgs_do_cliente, ifNull(f.msgs_enviadas, 0) AS msgs_enviadas, ifNull(f.qtd_transicoes, 0) AS qtd_transicoes,
       ifNull(f.ja_foi_problematico, false) AS ja_foi_problematico, ifNull(f.ja_foi_credito_aprovado, false) AS ja_foi_credito_aprovado,
       ifNull(f.ja_foi_credito_reprovado, false) AS ja_foi_credito_reprovado, ifNull(f.ja_foi_cadastro_reprovado, false) AS ja_foi_cadastro_reprovado,
       ifNull(f.ja_foi_vendido, false) AS ja_foi_vendido, ifNull(f.ja_foi_desqualificado, false) AS ja_foi_desqualificado, ifNull(f.ja_desistiu, false) AS ja_desistiu
FROM trusted_chatwoot.contacts AS k FINAL
LEFT JOIN {db}.chatwoot_contato_funil f ON f.contato_id = k.id
LEFT JOIN {db}.dim_canal_chatwoot cn ON cn.canal_id = f.canal_primeiro_contato_id
"""))

T.append(("fato_lead_funil", ["dim_lead", "ponte_lead_cliente", "fato_contrato", "dim_tenant"], """
CREATE OR REPLACE TABLE {db}.fato_lead_funil ENGINE = MergeTree ORDER BY contato_id AS
WITH vinc AS (
    SELECT p.contato_id AS contato_id, count() AS qtd_clientes_vinculados, groupUniqArray(p.tenant) AS tenants_vinculados,
           argMax(p.confianca, p.pontuacao) AS melhor_confianca, max(p.pontuacao) AS melhor_pontuacao
    FROM {db}.ponte_lead_cliente p WHERE p.vinculo_valido GROUP BY p.contato_id
),
contratos AS (
    SELECT p.contato_id AS contato_id, count() AS qtd_contratos, sum(c.valor_principal) AS valor_contratado, min(c.data_inicio) AS primeiro_contrato_em,
           countIf(c.em_atraso) AS contratos_em_atraso
    FROM {db}.ponte_lead_cliente p JOIN {db}.fato_contrato c ON c.cliente_id = p.cliente_id JOIN {db}.dim_tenant t ON t.tenant_id = c.tenant_id
    WHERE p.vinculo_valido AND t.empresa = 'Capital Certo' GROUP BY p.contato_id
),
docs AS (SELECT contato_id, count() AS docs_citados FROM analytics_stg.chatwoot_documentos_citados GROUP BY contato_id)
SELECT l.contato_id AS contato_id, l.data_criacao AS data_criacao, toStartOfMonth(toDate(assumeNotNull(ifNull(l.primeira_conversa_em, l.criado_em)))) AS safra_lead,
       l.canal_primeiro_contato_id AS canal_id, l.marca_linha AS marca_linha, l.classe_estagio_atual AS classe_estagio_atual,
       l.qtd_conversas AS qtd_conversas, l.msgs_do_cliente AS msgs_do_cliente, l.msgs_enviadas AS msgs_enviadas, l.qtd_transicoes AS qtd_transicoes,
       ifNull(d.docs_citados, 0) AS docs_citados, ifNull(d.docs_citados, 0) > 0 AS citou_documento,
       ifNull(v.qtd_clientes_vinculados, 0) AS qtd_clientes_vinculados, ifNull(v.qtd_clientes_vinculados, 0) > 0 AS tem_vinculo_crm,
       ifNull(v.melhor_confianca, '') AS melhor_confianca, ifNull(v.tenants_vinculados, []) AS tenants_vinculados,
       ifNull(c.qtd_contratos, 0) AS qtd_contratos, ifNull(c.qtd_contratos, 0) > 0 AS virou_contrato,
       c.primeiro_contrato_em AS primeiro_contrato_em, ifNull(c.valor_contratado, 0) AS valor_contratado, ifNull(c.contratos_em_atraso, 0) AS contratos_em_atraso,
       if(c.primeiro_contrato_em IS NULL OR l.primeira_conversa_em IS NULL, NULL, dateDiff('day', toDate(l.primeira_conversa_em), c.primeiro_contrato_em)) AS dias_ate_primeiro_contrato,
       multiIf(ifNull(c.qtd_contratos, 0) = 0 AND ifNull(v.qtd_clientes_vinculados, 0) = 0, 'sem_vinculo_crm',
               ifNull(c.qtd_contratos, 0) = 0, 'cliente_cadastrado_sem_contrato',
               c.primeiro_contrato_em < toDate(l.primeira_conversa_em), 'cliente_existente_antes_do_contato',
               'virou_contrato_apos_contato') AS situacao_conversao,
       l.ja_foi_vendido AS funil_marcou_vendido
FROM {db}.dim_lead l
LEFT JOIN vinc v ON v.contato_id = l.contato_id LEFT JOIN contratos c ON c.contato_id = l.contato_id LEFT JOIN docs d ON d.contato_id = l.contato_id
WHERE l.qtd_conversas > 0
"""))

# ---------------------------------------------------------------- SNAPSHOT DIARIO DE INADIMPLENCIA
T.append(("fato_inadimplencia_contrato_dia", ["fato_parcela", "dim_contrato"], """
CREATE TABLE IF NOT EXISTS {db}.fato_inadimplencia_contrato_dia (
    data_referencia Date, contrato_id String, cliente_id String, tenant_id String, status_contrato LowCardinality(String),
    provedor_pagamento LowCardinality(String), valor_principal Decimal(18, 4), parcelas_vencidas_em_aberto UInt32,
    valor_vencido_em_aberto Decimal(18, 4), saldo_em_aberto Decimal(18, 4), maior_atraso_dias UInt32,
    faixa_atraso LowCardinality(String), em_inadimplencia Bool
) ENGINE = MergeTree PARTITION BY toYYYYMM(data_referencia) ORDER BY (data_referencia, contrato_id)
COMMENT 'Fotografia diaria da inadimplencia por contrato. Cada rodada da DAG apaga e regrava a data_referencia, entao o historico acumula.';;
ALTER TABLE {db}.fato_inadimplencia_contrato_dia ADD COLUMN IF NOT EXISTS empresa LowCardinality(String);;
ALTER TABLE {db}.fato_inadimplencia_contrato_dia DELETE WHERE data_referencia = toDate('{ref}') SETTINGS mutations_sync = 2;;
INSERT INTO {db}.fato_inadimplencia_contrato_dia (data_referencia, contrato_id, cliente_id, tenant_id, empresa, status_contrato, provedor_pagamento, valor_principal, parcelas_vencidas_em_aberto, valor_vencido_em_aberto, saldo_em_aberto, maior_atraso_dias, faixa_atraso, em_inadimplencia)
SELECT toDate('{ref}'), p.contrato_id, any(p.cliente_id), any(p.tenant_id), any(p.empresa), any(d.status), any(d.provedor_pagamento), any(d.valor_principal),
       countIf(p.vencida_em_aberto), sumIf(p.valor_em_aberto, p.vencida_em_aberto), sum(p.valor_em_aberto), max(p.dias_atraso_em_aberto),
       multiIf(max(p.dias_atraso_em_aberto) = 0, 'em_dia', max(p.dias_atraso_em_aberto) <= 7, '01_a_07_dias', max(p.dias_atraso_em_aberto) <= 30, '08_a_30_dias',
               max(p.dias_atraso_em_aberto) <= 60, '31_a_60_dias', max(p.dias_atraso_em_aberto) <= 90, '61_a_90_dias', 'acima_de_90_dias'),
       countIf(p.vencida_em_aberto) > 0
FROM {db}.fato_parcela p JOIN {db}.dim_contrato d ON d.contrato_id = p.contrato_id
WHERE NOT p.contrato_excluido AND d.status != 'CANCELLED' GROUP BY p.contrato_id
"""))

# ---------------------------------------------------------------- MARTS
T.append(("cliente_360", ["dim_cliente", "fato_lead_funil", "fato_contrato", "dim_lead", "fato_relatorio_credito"], """
CREATE OR REPLACE TABLE {db}.cliente_360 ENGINE = MergeTree ORDER BY cliente_id AS
WITH rel AS (
    SELECT contato_id, argMax(score_pf, criado_em) AS score_pf, argMax(faixa_pf, criado_em) AS faixa_pf,
           argMax(taxa_inadimplencia_pf_pct, criado_em) AS taxa_inadimplencia_pf_pct, argMax(score_pj, criado_em) AS score_pj,
           max(criado_em) AS relatorio_em, count() AS qtd_relatorios
    FROM {db}.fato_relatorio_credito GROUP BY contato_id
),
tr AS (
    SELECT cliente_id, countIf(reaplicou_ate_3d) AS reaplicacoes_ate_3d, countIf(quitou_antes_do_prazo) AS quitacoes_antecipadas,
           countIf(marcado_fuga_garantia) AS contratos_fuga_garantia, max(maior_salto_valor_historico) AS maior_salto_valor,
           sum(chamados_suporte_qtd) AS chamados_suporte_qtd, sum(interacoes_qtd) AS interacoes_qtd, sum(cobrancas_whatsapp_qtd) AS cobrancas_whatsapp_qtd
    FROM {db}.fato_contrato GROUP BY cliente_id
)
SELECT c.cliente_id AS cliente_id, c.* EXCEPT (cliente_id), tr.reaplicacoes_ate_3d AS reaplicacoes_ate_3d, tr.quitacoes_antecipadas AS quitacoes_antecipadas, tr.contratos_fuga_garantia AS contratos_fuga_garantia,
       tr.maior_salto_valor AS maior_salto_valor, tr.chamados_suporte_qtd AS chamados_suporte_qtd, tr.interacoes_qtd AS interacoes_qtd,
       tr.cobrancas_whatsapp_qtd AS cobrancas_whatsapp_qtd,
       l.qtd_conversas AS chatwoot_qtd_conversas, l.msgs_do_cliente AS chatwoot_msgs_do_cliente, l.msgs_enviadas AS chatwoot_msgs_enviadas,
       l.primeira_conversa_em AS chatwoot_primeira_conversa_em, l.ultima_atividade_em AS chatwoot_ultima_atividade_em,
       l.estagio_atual AS chatwoot_estagio_atual, l.classe_estagio_atual AS chatwoot_classe_estagio_atual, l.marca_linha AS chatwoot_marca_linha,
       l.qtd_transicoes AS chatwoot_qtd_transicoes, ifNull(l.ja_foi_problematico, false) AS chatwoot_ja_foi_problematico,
       ifNull(l.ja_foi_credito_reprovado, false) AS chatwoot_ja_foi_credito_reprovado, ifNull(l.ja_foi_vendido, false) AS chatwoot_ja_foi_vendido,
       c.contratos_em_atraso > 0 AS rotulo_crm_inadimplente, ifNull(tr.contratos_fuga_garantia, 0) > 0 AS rotulo_crm_fuga_garantia,
       ifNull(l.ja_foi_problematico, false) AS rotulo_chatwoot_problematico,
       rel.score_pf AS score_pf, rel.faixa_pf AS faixa_pf, rel.taxa_inadimplencia_pf_pct AS taxa_inadimplencia_pf_pct, rel.score_pj AS score_pj,
       rel.relatorio_em AS relatorio_credito_em, rel.contato_id != 0 AS tem_relatorio_credito
FROM {db}.dim_cliente c
LEFT JOIN tr ON tr.cliente_id = c.cliente_id
LEFT JOIN rel ON rel.contato_id = c.contato_chatwoot_principal_id AND c.tem_vinculo_chatwoot
LEFT JOIN {db}.dim_lead l ON l.contato_id = c.contato_chatwoot_principal_id AND c.tem_vinculo_chatwoot
WHERE NOT c.excluido
"""))

T.append(("carteira_safra_mensal", ["fato_contrato", "dim_tenant"], """
CREATE OR REPLACE TABLE {db}.carteira_safra_mensal ENGINE = MergeTree ORDER BY (empresa, tenant, safra) AS
SELECT t.empresa AS empresa, t.tenant AS tenant, toStartOfMonth(f.data_inicio) AS safra, count() AS contratos, sum(f.valor_principal) AS valor_originado,
       round(avg(f.valor_principal), 2) AS ticket_medio, countIf(f.status = 'PAID_OFF') AS quitados, countIf(f.em_atraso) AS em_atraso,
       countIf(f.status = 'ACTIVE') AS ativos, round(countIf(f.em_atraso) / count(), 4) AS pct_em_atraso,
       round(avg(f.dias_ate_quitar), 1) AS media_dias_ate_quitar, round(avg(f.maior_atraso_em_aberto_dias), 1) AS media_maior_atraso_dias,
       sum(f.valor_vencido_em_aberto) AS valor_vencido_em_aberto, sum(f.saldo_em_aberto) AS saldo_em_aberto
FROM {db}.fato_contrato f JOIN {db}.dim_tenant t ON t.tenant_id = f.tenant_id GROUP BY empresa, tenant, safra
"""))

T.append(("conversao_lead_mensal", ["fato_lead_funil"], """
CREATE OR REPLACE TABLE {db}.conversao_lead_mensal ENGINE = MergeTree ORDER BY (safra_lead, marca_linha) AS
SELECT safra_lead, marca_linha, count() AS leads, countIf(citou_documento) AS leads_que_citaram_documento, countIf(tem_vinculo_crm) AS leads_com_vinculo_crm,
       countIf(virou_contrato) AS leads_com_contrato, countIf(situacao_conversao = 'virou_contrato_apos_contato') AS virou_contrato_apos_contato,
       countIf(situacao_conversao = 'cliente_existente_antes_do_contato') AS clientes_existentes,
       round(countIf(situacao_conversao = 'virou_contrato_apos_contato') / count(), 4) AS taxa_conversao_nova,
       round(avgIf(dias_ate_primeiro_contrato, situacao_conversao = 'virou_contrato_apos_contato'), 1) AS media_dias_ate_contrato,
       countIf(funil_marcou_vendido) AS marcados_vendido_no_funil, sum(valor_contratado) AS valor_contratado
FROM {db}.fato_lead_funil GROUP BY safra_lead, marca_linha
"""))

T.append(("funil_chatwoot_vs_carteira", ["cliente_360"], """
CREATE OR REPLACE TABLE {db}.funil_chatwoot_vs_carteira ENGINE = MergeTree ORDER BY (empresa, chatwoot_classe_estagio_atual, chatwoot_estagio_atual) AS
SELECT empresa, multiIf(NOT tem_vinculo_chatwoot, 'sem_contato_chatwoot', ifNull(chatwoot_classe_estagio_atual, '') = '', 'sem_estagio', assumeNotNull(chatwoot_classe_estagio_atual)) AS chatwoot_classe_estagio_atual,
       multiIf(NOT tem_vinculo_chatwoot, '(sem contato no Chatwoot)', ifNull(chatwoot_estagio_atual, '') = '', '(conversa sem estagio)', assumeNotNull(chatwoot_estagio_atual)) AS chatwoot_estagio_atual,
       count() AS clientes, countIf(qtd_contratos > 0) AS clientes_com_contrato, countIf(rotulo_crm_inadimplente) AS inadimplentes,
       countIf(rotulo_crm_fuga_garantia) AS fuga_garantia, sum(ifNull(total_emprestado, 0)) AS total_emprestado,
       round(countIf(rotulo_crm_inadimplente) / nullIf(countIf(qtd_contratos > 0), 0), 4) AS pct_inadimplentes
FROM {db}.cliente_360 GROUP BY empresa, chatwoot_classe_estagio_atual, chatwoot_estagio_atual
"""))

T.append(("score_vs_inadimplencia", ["cliente_360"], """
CREATE OR REPLACE TABLE {db}.score_vs_inadimplencia ENGINE = MergeTree ORDER BY (empresa, faixa_score_pf) AS
SELECT empresa, if(tem_relatorio_credito, ifNull(nullIf(faixa_pf, ''), 'sem_faixa'), 'sem_relatorio') AS faixa_score_pf,
       count() AS clientes, countIf(qtd_contratos > 0) AS clientes_com_contrato, countIf(rotulo_crm_inadimplente) AS inadimplentes,
       round(countIf(rotulo_crm_inadimplente) / nullIf(countIf(qtd_contratos > 0), 0), 4) AS pct_inadimplentes,
       round(avg(score_pf), 0) AS score_pf_medio, sum(ifNull(total_emprestado, 0)) AS total_emprestado, sum(ifNull(valor_vencido_em_aberto, 0)) AS valor_vencido_em_aberto
FROM {db}.cliente_360 GROUP BY empresa, faixa_score_pf
"""))

T.append(("inadimplencia_aging", ["fato_inadimplencia_contrato_dia", "dim_tenant"], """
CREATE OR REPLACE TABLE {db}.inadimplencia_aging ENGINE = MergeTree ORDER BY (data_referencia, empresa, tenant, faixa_atraso) AS
SELECT f.data_referencia AS data_referencia, t.empresa AS empresa, t.tenant AS tenant, f.faixa_atraso AS faixa_atraso, count() AS contratos,
       sum(f.valor_vencido_em_aberto) AS valor_vencido_em_aberto, sum(f.saldo_em_aberto) AS saldo_em_aberto, sum(f.valor_principal) AS valor_principal
FROM {db}.fato_inadimplencia_contrato_dia f JOIN {db}.dim_tenant t ON t.tenant_id = f.tenant_id GROUP BY data_referencia, empresa, tenant, faixa_atraso
"""))

TABLES = T
ORDER = [n for n, _, _ in T]
