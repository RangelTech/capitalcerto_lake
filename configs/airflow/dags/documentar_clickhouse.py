# Documentacao do ClickHouse da Capital Certo, pensada para leitura por pessoas e por IA (Claude Code, Hermes):
#   1. comentario de BANCO, de TABELA e de COLUNA em tudo que ainda nao tem (nada que ja tem comentario e sobrescrito);
#   2. views de catalogo: analytics.catalogo_tabelas e analytics.catalogo_colunas (descricao, linhas, camada, atualizacao);
#   3. marca as tabelas do estudo inicial de outubro como substituidas pelo motor (continuam como historico de testes).
# Roda no fim do gold_motor (todo dia) e pode rodar sozinho: python documentar_clickhouse.py
import re

import credit_features as cf

BANCOS = {
    "analytics": "Gold da Capital Certo: dimensoes e fatos de negocio (cliente, contrato, parcela, pagamento, funil, bureau) e as tabelas de consumo do motor de credito (score, clusters, contrato_risco_360, contrato_consulta). Fonte para Metabase e API de risco.",
    "analytics_ml": "Motor de credito e matriz de testes: variaveis e rotulos por contrato, execucoes e acerto do modelo, importancia dos fatores, e TODOS os testes e cruzamentos ja feitos (correlacoes, combinacoes, matriz de modelos, efeito ajustado, sazonalidade). Consultar antes de testar de novo. Comecar por motor_testes_resumo.",
    "analytics_stg": "Area temporaria de preparo da Gold (staging). Pode ser recriada.",
    "backup_trusted_credhub_20261109": "QUARENTENA: copia da trusted_credhub antes da recarga fria de 09/11/2026. Pode ser apagada depois de 10/12/2026.",
    "trusted_chatwoot": "Espelho tratado do Chatwoot da Capital Certo (conversas, mensagens, notas, contatos, kanban) e os anexos processados (imagem, PDF, audio, video, OCR).",
    "trusted_credhub": "Espelho tratado do CredHub (CRM de credito): clientes, contratos, parcelas, pagamentos, cobrancas (Efi e Pluggy), auditoria. Carga diaria pelo portal.",
    "trusted_rfb": "Receita Federal (CNPJ aberto): empresas, estabelecimentos, socios, simples e tabelas de apoio. Usada para enriquecer o cadastro por CNPJ.",
}

# nomes de coluna que se repetem em varias tabelas da Gold e do motor
COL = {
    "cliente_id": "identificador do cliente no CredHub", "contrato_id": "identificador do contrato no CredHub", "pessoa_id": "hash do CPF/CNPJ; igual nos dois CNPJs da Capital Certo",
    "nome": "nome do cliente", "nome_busca": "nome em minusculas, para busca", "telefone": "telefone do cliente, so digitos", "email": "e-mail do cliente",
    "data_inicio": "data de inicio do contrato", "data_referencia": "dia em que a linha foi calculada", "run_id": "execucao do motor que gerou a linha (data e hora)",
    "tipo_contrato": "primeiro contrato do cliente ou renovacao", "ordem_contrato": "posicao do contrato na historia do cliente (1 = primeiro)",
    "valor_principal": "valor emprestado (R$)", "valor_parcela": "valor de cada parcela (R$)", "qtd_parcelas": "numero de parcelas contratadas", "taxa_juros_pct": "taxa de juros do contrato (%)",
    "provedor_pagamento": "provedor de cobranca (Pluggy, Efi, Efi automatico, manual)", "pix_agendado": "1 = cobranca por Pix agendado; 0 = sem",
    "tipo_pessoa": "pessoa fisica (CPF) ou juridica (CNPJ)", "segmento": "ramo do negocio informado no cadastro", "sexo": "sexo informado no cadastro", "uf": "estado do cadastro",
    "uf_cep": "estado do CEP", "uf_ddd": "estado do DDD do telefone", "idade_na_contratacao": "idade do cliente na data do contrato (anos)", "tempo_de_casa_dias": "dias entre o cadastro do cliente e este contrato",
    "idade_dias": "dias desde o inicio do contrato ate a data de referencia", "etapa": "ponto de avaliacao do motor (dia 0, 1, 2, 3, 5, 7, 10 ou 14 do contrato); usa o maior ponto ja alcancado",
    "etapa_motor": "ponto de avaliacao do motor usado no score (dia 0, 1, 2, 3, 5, 7, 10 ou 14)", "etapa_anterior": "avaliacao anterior do contrato (dia)", "etapa_atual": "avaliacao mais recente do contrato (dia)",
    "fase": "ativo (ate 50 dias, a previsao vale) ou encerrado (mais antigo: o score e retroativo)", "status": "situacao do contrato no CredHub",
    "prob_calote": "chance calibrada de calote: 20% ou mais das parcelas, da avaliacao ate o dia 45, vencidas e em aberto (0 a 1)",
    "prob_inadimplencia": "chance calibrada de inadimplencia: 30% ou mais das parcelas ate o dia 45 pagas com mais de 3 dias de atraso ou em aberto (0 a 1)",
    "prob_cluster_ruim": "chance calibrada de o contrato cair no cluster ruim: paga no comeco e para, ou quase nao paga (0 a 1)", "prob_bruta": "saida do modelo antes da calibracao",
    "prob_anterior": "chance de calote na avaliacao anterior (0 a 1)", "prob_atual": "chance de calote na avaliacao atual (0 a 1)", "piora": "prob_atual menos prob_anterior (0 a 1)",
    "prioridade": "chance de calote x valor do contrato x (1 + piora); maior = olhar primeiro", "posicao": "ordem de atencao: 1 = contrato que mais exige atencao",
    "faixa_risco": "nivel de risco: baixo (<15%), medio (15-30%), alto (30-50%), muito_alto (50% ou mais)", "faixa_anterior": "nivel de risco na avaliacao anterior", "faixa_atual": "nivel de risco na avaliacao atual",
    "confianca_modelo": "confianca do modelo na etapa: nenhuma, baixa, media, alta (pelo acerto fora do tempo)", "confianca": "confianca do modelo: nenhuma, baixa, media, alta",
    "motivos": "JSON com ate 3 fatores que mais empurram o risco do contrato e o tamanho do efeito", "score_em": "data do score",
    "calote_observado": "calote realmente observado (1 sim, 0 nao); vazio se o contrato ainda nao amadureceu", "calote_observado_d45": "calote real ate o dia 45 (1 sim, 0 nao); vazio se ainda nao amadureceu",
    "calote_d45": "rotulo: calote real nas parcelas depois do dia 0 ate o dia 45", "calote_30d": "rotulo: calote real nas parcelas ate o dia 30", "inadimplencia_d45": "rotulo: inadimplencia real ate o dia 45",
    "cluster": "grupo de comportamento de pagamento", "cluster_observado": "grupo de comportamento em que o contrato realmente caiu (so contratos maduros)", "cluster_trajetoria": "grupo de comportamento de pagamento em que o contrato caiu (so contratos maduros)",
    "cluster_ruim": "1 = cluster paga no comeco e para, ou quase nao paga", "probabilidade": "chance (0 a 1)", "pct_parcelas_no_prazo": "% de parcelas pagas no prazo ate o dia 45", "pct_parcelas_abertas": "% de parcelas vencidas e em aberto ate o dia 45",
    "atraso_medio_dias": "atraso medio das parcelas pagas (dias)", "cmp_dado_compartilhado": "quantos tipos de dado (telefone, e-mail, endereco, CPF do responsavel) o cliente divide com outros clientes",
    "cmp_endereco_compartilhado": "quantos OUTROS clientes dividem o mesmo endereco", "endereco_compartilhado": "quantos OUTROS clientes dividem o mesmo endereco", "inconsistencias": "indice 0 a 4 de inconsistencias cadastrais",
    "anterior_paga_e_para": "1 = o contrato anterior do mesmo CPF/CNPJ caiu no cluster paga no comeco e para", "anterior_pct_atrasadas": "% de parcelas atrasadas no contrato anterior", "anterior_atraso_max": "maior atraso no contrato anterior (dias)",
    "valor_contrato_anterior": "valor do contrato anterior (R$)", "razao_valor_vs_anterior": "valor deste contrato dividido pelo do anterior", "dias_desde_quitacao_anterior": "dias entre a quitacao do contrato anterior e o inicio deste",
    "maior_salto_valor_historico": "maior aumento de valor entre contratos consecutivos do cliente", "parcelas_pagas": "parcelas ja pagas", "parcelas_atrasadas": "parcelas atrasadas", "maior_atraso_em_aberto_dias": "maior atraso em aberto (dias)",
    "maior_atraso_dias": "maior atraso em aberto (dias)", "valor_vencido_em_aberto": "soma das parcelas vencidas e nao pagas (R$)", "saldo_em_aberto": "saldo total em aberto (R$)", "versao_codigo": "versao do codigo do motor",
    "alvo": "o que o modelo preve: calote, inadimplencia ou cluster_ruim", "features": "conjunto de informacoes usado (so comportamento, cadastro + comportamento, cadastro enxuto...)", "modelo": "algoritmo (logistica, lightgbm, floresta...)",
    "n": "contratos usados no teste", "n_treino_final": "contratos com resultado fechado usados no treino final", "auc": "acerto fora do tempo (AUC: 0,5 = chute, 1 = perfeito)", "auc_fora_do_tempo": "acerto do modelo da etapa fora do tempo (AUC)",
    "auc_ic_inf": "limite inferior do intervalo de 95% do acerto", "auc_ic_sup": "limite superior do intervalo de 95% do acerto", "pr_auc": "area sob a curva precisao x recall", "ks": "estatistica KS (separacao entre bons e maus)",
    "brier": "erro de Brier (menor e melhor)", "brier_base": "erro de Brier de prever sempre a taxa media", "lift_top_decil": "taxa de calote nos 10% de maior risco dividida pela taxa geral", "fallback": "1 = o retreino piorou e o motor manteve o modelo anterior",
    "contratos": "numero de contratos", "pct_dos_contratos": "participacao no total de contratos", "pct_no_prazo_medio": "media de % de parcelas pagas no prazo", "pct_abertas_medio": "media de % de parcelas em aberto", "taxa_calote_d45": "taxa de calote real ate o dia 45",
    "valor_medio": "valor medio do contrato (R$)", "valor_total": "soma dos valores dos contratos (R$)", "pct_pix_agendado": "% de contratos com Pix agendado", "pct_renovacao": "% de renovacoes", "pct_endereco_compartilhado": "% com endereco dividido com outro cliente",
    "modo_cobranca": "Pix agendado ou sem Pix", "flag": "perfil conhecido ao assinar (endereco, valor, parcelas...)", "calotes": "contratos com calote", "taxa_calote": "taxa de calote (calotes dividido por contratos)", "ic95_inf": "limite inferior do intervalo de 95%", "ic95_sup": "limite superior do intervalo de 95%",
    # estudo inicial de outubro/2026 (substituido)
    "k": "numero de grupos", "cramers_v": "forca da associacao grupo x desfecho (V de Cramer)", "acaso_medio": "valor medio esperado ao acaso", "p_estratificado": "p-valor pela permutacao estratificada", "maior_cluster": "tamanho do maior grupo",
    "taxa_min": "menor taxa do desfecho entre os grupos", "taxa_max": "maior taxa do desfecho entre os grupos", "entrada": "o que entrou no modelo", "algoritmo": "algoritmo testado", "auc_cv": "AUC em validacao cruzada", "auc_desvio": "desvio padrao do AUC",
    "acaso_estratificado_p95": "AUC no percentil 95 ao acaso (permutacao estratificada)", "passa": "1 = o resultado passa do acaso", "clientes": "clientes usados", "regra": "regra testada", "condicoes": "condicoes da regra", "taxa": "taxa do desfecho",
    "taxa_base": "taxa do desfecho na base inteira", "lift": "taxa da regra dividida pela taxa base", "z": "estatistica z", "p_familia": "p-valor corrigido pela familia de testes", "significativa": "1 = passa na correcao", "combinacoes_testadas": "quantas regras foram testadas",
    "z_critico_5pct": "z critico a 5% apos a correcao", "passo": "passo da selecao", "variavel": "variavel", "bloco": "bloco de variaveis", "auc_aninhado": "AUC da validacao aninhada", "auc_interno": "AUC da validacao interna", "regime": "regime de validacao",
    "blocos": "blocos de variaveis usados", "prevalencia": "taxa do desfecho", "variaveis": "variaveis usadas", "acaso_simples_p95": "AUC no percentil 95 ao acaso (permutacao simples)", "acaso_estratificado_medio": "AUC medio ao acaso (estratificado)",
    "melhor_so_cohort": "melhor AUC usando so a safra", "ganho_sobre_cohort": "ganho sobre usar so a safra", "passa_acaso_estratificado": "1 = passa do acaso estratificado", "passa_cohort": "1 = passa a linha de base da safra",
    "conjunto": "conjunto de variaveis", "coeficiente": "coeficiente da regressao", "odds_ratio": "razao de chances", "decil": "grupo de risco (1 = menor, 10 = maior)", "taxa_desfecho": "taxa do desfecho no grupo", "prob_media": "probabilidade media prevista no grupo",
    "auc_nulo_p95": "AUC no percentil 95 do modelo nulo", "auc_nulo_medio": "AUC medio do modelo nulo", "acima_do_acaso": "1 = acima do acaso", "ganho_vs_cohort": "ganho sobre a linha de base da safra", "campeao": "1 = modelo escolhido",
    "quadro_real": "quadro de pagamento real do cliente", "p_nunca_pagou": "chance de nunca pagar", "p_pagou_e_parou": "chance de pagar e parar", "p_atrasa_mas_paga": "chance de atrasar mas pagar", "p_paga_em_dia": "chance de pagar em dia",
    "quadro": "quadro de pagamento", "quadro_pt": "nome do quadro em portugues", "clientes_no_quadro": "clientes no quadro", "desfecho": "desfecho observado", "finalidade": "finalidade do emprestimo na conversa", "urgencia": "urgencia percebida",
    "tom": "tom da conversa", "consistencia_das_informacoes": "consistencia das informacoes dadas", "informacao_voluntaria": "se o cliente deu informacao sem ser perguntado", "menciona_divida_ou_negativacao": "menciona divida ou nome sujo",
    "dificuldade_de_caixa": "menciona dificuldade de caixa", "pede_valor_maior_que_o_oferecido": "pede valor maior que o oferecido", "negocia_taxa_ou_prazo": "negocia taxa ou prazo", "demora_ou_resiste_a_enviar_documentos": "demora ou resiste a enviar documentos",
    "tipo_de_negocio": "tipo de negocio citado", "anos_de_negocio_mencionados": "anos de negocio mencionados", "faturamento_mensal_mencionado_reais": "faturamento mensal citado (R$)", "usa_maquininha": "usa maquininha", "recebe_por_pix": "recebe por Pix",
    "cliente_indicado_por_terceiro": "foi indicado por terceiro", "linguagem_formal": "usa linguagem formal", "erros_de_escrita": "quantidade de erros de escrita", "tamanho_da_conversa": "tamanho da conversa",
    # Chatwoot (espelho)
    "id": "identificador do registro", "account_id": "conta do Chatwoot (sempre a mesma na Capital Certo)", "inbox_id": "caixa de entrada (linha de WhatsApp) do Chatwoot", "contact_id": "contato do Chatwoot", "conversation_id": "conversa do Chatwoot",
    "created_at": "quando o registro foi criado (UTC)", "updated_at": "ultima atualizacao do registro (UTC)", "content": "texto da mensagem ou da nota", "message_type": "tipo da mensagem: 0 recebida, 1 enviada, 2 atividade, 3 modelo",
    "sender_id": "quem enviou (id do agente ou contato)", "sender_type": "tipo de quem enviou (usuario ou contato)", "private": "1 = nota interna, nao visivel ao cliente", "user_id": "agente (usuario) do Chatwoot", "phone_number": "telefone do contato",
    "additional_attributes": "atributos adicionais em JSON", "custom_attributes": "atributos personalizados em JSON (inclui o status do DeskData e a proposta escolhida)", "file_type": "tipo do anexo: 0 imagem, 1 audio, 2 video, 3 arquivo", "external_url": "endereco do anexo",
    "extension": "extensao do arquivo", "filename": "nome do arquivo", "byte_size": "tamanho do arquivo em bytes", "checksum": "soma de verificacao do arquivo", "content_type": "tipo MIME do arquivo", "key": "chave do arquivo no armazenamento",
    "name": "nome", "title": "titulo", "description": "descricao", "email_address": "e-mail", "display_id": "numero da conversa visto pelo agente", "assignee_id": "agente responsavel", "team_id": "equipe", "label": "etiqueta", "stage_id": "estagio do funil",
    "funnel_id": "funil do kanban", "position": "posicao", "from_stage_id": "estagio de origem", "to_stage_id": "estagio de destino", "status_changed_at": "quando a situacao mudou", "first_response_time": "tempo ate a primeira resposta (segundos)",
}

ESTUDO_ANTIGO = ("conversas_modelo", "conversas_score_decil", "conversas_termos", "embeddings_conversa", "estudo_clusters_perfil", "estudo_embeddings_clusters", "estudo_embeddings_modelos", "estudo_regras", "estudo_selecao_recursiva",
                 "estudo_triagem", "estudo_validacao", "llm_piloto_associacao", "llm_piloto_features", "llm_piloto_modelo", "llm_v2_features", "modelo_coeficientes", "modelo_decis", "modelo_execucoes", "modelo_prob_quadros",
                 "modelo_quadros_metricas", "modelo_score_cliente", "perfil_controles", "perfil_inadimplencia", "quadros_atributo", "quadros_nivel", "quadros_receita_atributo", "quadros_receita_nivel", "quadros_resumo")


def esc(s):
    return str(s).replace("\\", "\\\\").replace("'", " ")


def descricao_coluna(db, tabela, col):
    if col in COL:
        return COL[col]
    m = re.match(r"^(e\d+)_(.+)$", col)
    if m:
        return f"comportamento de pagamento como estava no dia {m.group(1)[1:]} do contrato: {m.group(2).replace('_', ' ')}"
    if re.match(r"^(ye|yi)_\d+$", col):
        return "rotulo: calote (ye) ou inadimplencia (yi) nas parcelas depois da etapa, ate o dia 45"
    if col.startswith("tom_"):
        return "tom da conversa lido por IA (0 a 1): " + col[4:].replace("_", " ")
    if col.startswith("tj_"):
        return "metrica da trajetoria de pagamento D+1..D+45 (so contrato maduro; nao usar como previsor)"
    origem = {"trusted_chatwoot": "Chatwoot", "trusted_rfb": "Receita Federal", "trusted_credhub": "CredHub"}.get(db, "sistema de origem")
    if db.startswith("trusted") or db.startswith("backup"):
        return f"coluna {col} da tabela {tabela} do {origem}, sem transformacao"
    return f"{col.replace('_', ' ')} (tabela {tabela})"


def main():
    ch = cf.conecta()
    for db, txt in BANCOS.items():
        try:
            ch.command(f"ALTER DATABASE {db} MODIFY COMMENT '{esc(txt)}'")
        except Exception as e:  # noqa
            print("banco sem comentario:", db, str(e)[:80])
    tabelas = ch.query("""SELECT database, name, engine, comment FROM system.tables
                          WHERE database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema') AND engine NOT IN ('View', 'MaterializedView')""").result_rows
    n_col = 0
    for db, t, eng, com in tabelas:
        cols = ch.query("SELECT name FROM system.columns WHERE database = {d:String} AND table = {t:String} AND comment = ''", parameters={"d": db, "t": t}).result_rows
        if cols:
            partes = ", ".join(f"COMMENT COLUMN `{c[0]}` '{esc(descricao_coluna(db, t, c[0]))}'" for c in cols)
            try:
                ch.command(f"ALTER TABLE {db}.`{t}` {partes}")
                n_col += len(cols)
            except Exception as e:  # noqa
                print("falhou", db, t, str(e)[:100])
        if db == "analytics_ml" and t in ESTUDO_ANTIGO and not (com or "").startswith("[ESTUDO"):
            ch.command(f"ALTER TABLE {db}.`{t}` MODIFY COMMENT '{esc('[ESTUDO INICIAL DE OUTUBRO/2026, SUBSTITUIDO PELO MOTOR; fica como historico dos testes] ' + (com or ''))}'")
    ch.command("""CREATE OR REPLACE VIEW analytics.catalogo_tabelas COMMENT 'Catalogo de todas as tabelas do ClickHouse: banco, tabela, camada, descricao, linhas, tamanho e ultima atualizacao. Para IA e pessoas descobrirem o que existe.' AS
        SELECT database AS banco, name AS tabela, multiIf(startsWith(database, 'trusted'), 'trusted (espelho da origem)', database = 'analytics', 'gold (consumo)', database = 'analytics_ml', 'motor de credito e testes',
               startsWith(database, 'backup'), 'quarentena (backup)', 'apoio') AS camada, engine AS tipo, comment AS descricao, total_rows AS linhas, round(total_bytes / 1000000, 2) AS megabytes,
               metadata_modification_time AS atualizado_em
        FROM system.tables WHERE database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema') ORDER BY database, name""")
    ch.command("""CREATE OR REPLACE VIEW analytics.catalogo_colunas COMMENT 'Catalogo de todas as colunas do ClickHouse: banco, tabela, coluna, tipo e descricao. Colunas de views herdam a descricao da coluna de mesmo nome na tabela de origem do motor.' AS
        SELECT c.database AS banco, c.table AS tabela, c.name AS coluna, c.type AS tipo, c.position AS posicao,
               if(c.comment != '', c.comment, coalesce(nullIf(s.comment, ''), '')) AS descricao
        FROM system.columns c LEFT JOIN (SELECT name, any(comment) AS comment FROM system.columns WHERE database IN ('analytics', 'analytics_ml') AND table IN ('score_contrato', 'contrato_risco_360') GROUP BY name) s ON s.name = c.name
        WHERE c.database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema') ORDER BY c.database, c.table, c.position""")
    sem = ch.query("SELECT count() FROM system.columns WHERE comment = '' AND database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema')").result_rows[0][0]
    print(f"colunas documentadas agora: {n_col}; colunas ainda sem comentario (views): {sem}", flush=True)


if __name__ == "__main__":
    main()
