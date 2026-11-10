# Consolida TODOS os testes e cruzamentos ja feitos no motor de credito em duas tabelas para consulta por IA (Claude Code, Hermes) e por pessoas:
#   analytics_ml.motor_testes_resumo      1 linha por familia de teste: o que foi cruzado, como, quantos testes, o que se achou, o que o motor faz com isso, onde esta o detalhe
#   analytics_ml.motor_testes_realizados  1 linha por teste (formato unico): tipo, variaveis, alvo, estrato, idade, algoritmo, n, metrica, valor, p, q (FDR), passa na correcao, detalhe, tabela de origem
# As combinacoes de 1 a 4 variaveis entram so com q<0,10 (a tabela completa tem 391 mil perfis: motor_combinacoes_1a4).
# Roda depois de qualquer bateria nova: python consolidar_testes.py
import credit_features as cf

COLS = ("tipo_teste String COMMENT 'familia do teste: univariado, combinacao, efeito ajustado, sazonalidade, matriz de modelos, ganho por idade, tom, redundancia...', "
        "familia String COMMENT 'grupo de variaveis testado (cadastro, bureau, midia, conversa, cobranca...)', variaveis String COMMENT 'variavel(is) testada(s)', alvo String COMMENT 'o que se tenta prever (calote, inadimplencia, cluster ruim...)', "
        "estrato String COMMENT 'em quais contratos o teste foi feito (todos, sem Pix, com Pix...)', idade_contrato Nullable(Int16) COMMENT 'dia do contrato em que o teste foi feito; vazio = dia 0 ou nao se aplica', "
        "algoritmo String COMMENT 'algoritmo, quando o teste compara modelos', n Nullable(UInt32) COMMENT 'contratos usados no teste', metrica String COMMENT 'o que o campo valor mede (auc, taxa, odds ratio, informacao mutua, rho...)', "
        "valor Nullable(Float64) COMMENT 'resultado da metrica', p Nullable(Float64) COMMENT 'p-valor', q_fdr Nullable(Float64) COMMENT 'p corrigido por falsa descoberta (FDR); menor que 0,10 = sinal', "
        "passa_correcao Nullable(UInt8) COMMENT '1 = o achado resistiu a correcao de falsa descoberta e/ou ao ganho sobre a base', detalhe String COMMENT 'perfil, nivel ou observacao do teste', "
        "tabela_origem String COMMENT 'tabela com o detalhe completo', rodado_em DateTime COMMENT 'quando o teste foi rodado'")

PARTES = [
    # univariados (varredura de todas as familias de variaveis)
    "SELECT 'univariado' AS tipo_teste, bloco AS familia, variavel AS variaveis, alvo, 'todos' AS estrato, CAST(NULL AS Nullable(Int16)) AS idade_contrato, '' AS algoritmo, toUInt32(n) AS n, toString(efeito_tipo) AS metrica, "
    "toFloat64(efeito) AS valor, toFloat64(p) AS p, toFloat64(q_fdr) AS q_fdr, toUInt8(significativo) AS passa_correcao, concat(toString(tipo), ' | ', toString(nivel)) AS detalhe, 'motor_correlacoes' AS tabela_origem, rodado_em FROM analytics_ml.motor_correlacoes",
    "SELECT 'combinacao de 1 a 3 variaveis', 'combinacoes por modo de cobranca', variaveis, alvo, estrato, CAST(NULL AS Nullable(Int16)), '', toUInt32(n), 'taxa do alvo no perfil', toFloat64(taxa), toFloat64(p), toFloat64(q_fdr), toUInt8(q_fdr < 0.1), "
    "concat(perfil, ' | taxa do estrato ', toString(round(base_estrato, 3))), 'motor_combinacoes_condicionadas', toDateTime('2026-11-09 23:07:00') FROM analytics_ml.motor_combinacoes_condicionadas WHERE q_fdr < 0.1",
    "SELECT 'combinacao de 1 a 4 variaveis', 'combinacoes por modo de cobranca', variaveis, alvo, estrato, CAST(NULL AS Nullable(Int16)), '', toUInt32(n), 'taxa do alvo no perfil', toFloat64(taxa), toFloat64(p), toFloat64(q_fdr), toUInt8(q_fdr < 0.1), "
    "concat(perfil, ' | taxa do estrato ', toString(round(base_estrato, 3))), 'motor_combinacoes_1a4 (completa, 391 mil perfis)', toDateTime('2026-11-10 01:07:30') FROM analytics_ml.motor_combinacoes_1a4 WHERE q_fdr < 0.1",
    "SELECT 'efeito ajustado por Pix, provedor e mes', 'regressao logistica', variavel, alvo, 'todos (descontando Pix, provedor e mes)', CAST(NULL AS Nullable(Int16)), '', toUInt32(n), 'odds ratio por desvio padrao', toFloat64(odds_ratio_por_dp), toFloat64(p), toFloat64(q_fdr), toUInt8(q_fdr < 0.1), "
    "concat('IC95 ', toString(round(ic95_inf, 2)), ' a ', toString(round(ic95_sup, 2))), 'motor_efeito_ajustado', toDateTime('2026-11-10 01:07:32') FROM analytics_ml.motor_efeito_ajustado",
    "SELECT 'sazonalidade controlada', 'sazonalidade', efeito, alvo, estrato, CAST(NULL AS Nullable(Int16)), '', toUInt32(n), 'p da razao de verossimilhanca', toFloat64(p_lr), toFloat64(p_lr), CAST(NULL AS Nullable(Float64)), toUInt8(p_lr < 0.05), "
    "'controla provedor e, quando aplicavel, Pix', 'motor_sazonalidade_controlada', toDateTime('2026-11-10 01:07:32') FROM analytics_ml.motor_sazonalidade_controlada",
    "SELECT 'matriz de modelos no dia 0', 'modelos', conjunto, alvo, estrato, CAST(NULL AS Nullable(Int16)), algoritmo, toUInt32(n), 'auc fora do tempo', toFloat64(auc), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(UInt8)), "
    "'validacao temporal, 5 meses', 'motor_matriz_modelos', toDateTime('2026-11-10 01:07:32') FROM analytics_ml.motor_matriz_modelos",
    "SELECT 'matriz de modelos por idade do contrato', 'modelos', conjunto, alvo, 'todos', toInt16(idade), algoritmo, toUInt32(n), 'auc fora do tempo', toFloat64(auc), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(UInt8)), "
    "'validacao temporal, 5 meses', 'motor_matriz_por_idade', now() FROM analytics_ml.motor_matriz_por_idade",
    "SELECT 'ganho sobre so comportamento por idade', 'modelos', conjunto, alvo, 'todos', toInt16(idade), algoritmo, CAST(NULL AS Nullable(UInt32)), 'ganho de auc', toFloat64(ganho_auc), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), toUInt8(significativo), "
    "concat('IC95 do ganho ', toString(round(ic95_inf, 3)), ' a ', toString(round(ic95_sup, 3))), 'motor_ganho_por_idade', now() FROM analytics_ml.motor_ganho_por_idade",
    "SELECT 'tom da conversa por LLM', 'conversa', variavel, alvo, estrato, CAST(NULL AS Nullable(Int16)), '', toUInt32(n), 'auc (abaixo de 0,5 = protege)', toFloat64(auc), toFloat64(p), toFloat64(q_fdr), toUInt8(q_fdr < 0.1), "
    "concat('metade antiga ', toString(round(auc_1a_metade, 3)), ' | metade nova ', toString(round(auc_2a_metade, 3))), 'motor_tom_testes', toDateTime('2026-11-10 00:55:00') FROM analytics_ml.motor_tom_testes",
    "SELECT 'dia 0 por modo de cobranca e canal', 'dia 0', conjunto, alvo, estrato, CAST(NULL AS Nullable(Int16)), melhor_algoritmo, toUInt32(n), 'auc fora do tempo', toFloat64(auc), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(UInt8)), "
    "concat('taxa de calote ', toString(round(taxa, 2))), 'motor_dia0_modelos', now() FROM analytics_ml.motor_dia0_modelos",
    "SELECT 'comparacao de algoritmos (primeira rodada)', 'modelos', features, alvo, 'todos', CAST(NULL AS Nullable(Int16)), algoritmo, toUInt32(n), 'auc fora do tempo', toFloat64(auc), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(UInt8)), "
    "concat('IC95 ', toString(round(auc_ic_lo, 2)), ' a ', toString(round(auc_ic_hi, 2))), 'motor_algoritmos', rodado_em FROM analytics_ml.motor_algoritmos",
    "SELECT 'informacao mutua', 'nao linear', variavel, alvo, 'todos', CAST(NULL AS Nullable(Int16)), '', CAST(NULL AS Nullable(UInt32)), 'informacao mutua', toFloat64(informacao_mutua), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(UInt8)), "
    "'0 = sem relacao com o alvo', 'motor_informacao_mutua', toDateTime('2026-11-09 23:07:00') FROM analytics_ml.motor_informacao_mutua",
    "SELECT 'redundancia entre variaveis', 'correlacao entre variaveis', concat(var_a, ' + ', var_b), '', 'todos', CAST(NULL AS Nullable(Int16)), '', CAST(NULL AS Nullable(UInt32)), 'spearman', toFloat64(rho), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(Float64)), CAST(NULL AS Nullable(UInt8)), "
    "'variaveis quase iguais: uma pode sair do motor', 'motor_pares_redundantes', toDateTime('2026-11-09 23:07:00') FROM analytics_ml.motor_pares_redundantes",
]

# resumo por familia (texto escrito a mao; os numeros de testes vem das tabelas)
RESUMO = [
    ("Varredura univariada", "Cada variavel sozinha contra calote, inadimplencia, cluster e alvos alternativos: cadastro, geografia, nome, conversa, bureau, Receita/CNPJ, socios, midia, embeddings, aquisicao, funil", "Mann-Whitney ou Fisher, correcao FDR por alvo, estabilidade entre metades do tempo",
     "motor_correlacoes", "Passam: Pix agendado, comportamento de pagamento, idade, historico de atraso anterior, CEP vazio, dias ate contratar, linha de WhatsApp. Sem sinal estavel: bureau, sexo, UF, DDD, nome, CNAE/porte, socios, midia, embedding do texto, funil.", "Entram no motor so as que passam e se mantem fora do tempo."),
    ("Combinacoes de 1 a 3 variaveis", "Perfis de ate 3 variaveis dentro de cada modo de cobranca (Pix agendado, sem Pix, sem Pix desde junho)", "teste exato, FDR", "motor_combinacoes_condicionadas",
     "Muitos perfis passam, mas se sobrepoem (mesmo grupo de clientes). Pix agendado separa tudo; dentro de sem Pix: provedor MANUAL, parcelas menores que 60, endereco dividido.", "Usado como leitura do dia 0 (risco_dia0_perfil), nao como modelo."),
    ("Combinacoes de 1 a 4 variaveis", "391 mil perfis de ate 4 variaveis, com tom da conversa, endereco dividido, valor sobre faturamento e cluster do contrato anterior", "teste hipergeometrico, FDR", "motor_combinacoes_1a4",
     "Dentro de Pix agendado nada passa (poucos contratos); sem Pix passam milhares, dominados por provedor MANUAL e parcelas menores que 60.", "Confirma Pix e provedor como eixos; nenhum perfil de 4 variaveis melhora o modelo fora do tempo."),
    ("Efeito ajustado", "Efeito de 29 informacoes controlando Pix, provedor e mes de inicio", "regressao logistica, odds ratio por desvio padrao, FDR", "motor_efeito_ajustado",
     "Passam (q<0,10): dado dividido com outro cliente (aumenta o risco), tom agressivo e recusa (protegem). Tom evasivo e engajamento protegem com q proximo de 0,11.", "Dado dividido entra como candidato no dia 0. Tom fica em observacao (precisa de coleta diaria)."),
    ("Sazonalidade controlada", "Dia da semana, semana do mes e mes de inicio, controlando provedor e Pix", "razao de verossimilhanca", "motor_sazonalidade_controlada",
     "Mes de inicio (safra) tem efeito real mesmo descontando Pix e provedor; semana do mes tem efeito na inadimplencia; dia da semana nao.", "A safra pede calibracao recente: o motor valida so nos 5 meses mais recentes."),
    ("Matriz de modelos no dia 0", "11 algoritmos e o comite x 10 conjuntos de informacoes x 4 alvos x 2 estratos", "validacao temporal (5 meses)", "motor_matriz_modelos",
     "Nenhum algoritmo nem conjunto e claramente melhor: AUC medio de 0,47 a 0,56, diferencas dentro da margem de erro. Floresta aleatoria tem a melhor media.", "O motor testa floresta, boosting e logistica todo dia e escolhe por etapa."),
    ("Matriz de modelos por idade", "Em cada idade (dia 1 a 14): 8 algoritmos e o comite x 6 conjuntos de informacoes, para calote e inadimplencia", "validacao temporal; ganho sobre so comportamento com bootstrap", "motor_matriz_por_idade e motor_ganho_por_idade",
     "Acrescentar informacao ao comportamento de pagamento PIORA o acerto em media (-0,04 a -0,05) e nao ajuda de D+5 em diante. Ganho significativo (IC95 acima de zero) em 29 de 350 comparacoes, quase todas em D+1 (o comportamento ainda e vazio): ganho medio +0,02 a +0,06; calote D+1 sobe de 0,53 para 0,69 com endereco dividido e inconsistencias em arvores extras; inadimplencia D+1 de 0,44 para 0,65. Entre algoritmos, com so comportamento, as diferencas sao de ate 0,02 de D+5 em diante (floresta e arvores extras tem a melhor media, 0,74).",
     "Motor ampliado (versao 10/11.2): nos dias 1, 2 e 3 entram candidatos com cadastro (endereco dividido, contrato anterior, inconsistencias) em floresta, arvores extras e logistica L1, com penalidade de 0,01 de AUC. De D+5 em diante segue so com comportamento."),
    ("Tom da conversa (LLM)", "12 perguntas sim/nao sobre o tom do cliente, lidas por LLM, em 654 contratos", "AUC, Mann-Whitney, FDR, estabilidade por metade, modelo temporal", "motor_tom_testes",
     "Quem fala menos e engaja menos da mais calote (AUC 0,39 sem Pix, p=0,0002), estavel; metade do efeito e tamanho do texto; o modelo do dia 0 nao melhora de forma confiavel.", "Observacao. Para virar variavel, o pipeline diario precisa gerar o tom em CPU (delta) e validar a concordancia com o modelo maior."),
    ("Estudo do dia 0", "Modelos separados por modo de cobranca (Pix agendado, sem Pix) e por canal, com produto curto (59 parcelas), valor e taxa, endereco dividido, contrato anterior, tom da conversa, valor sobre faturamento e dias ate contratar", "validacao temporal; 5 algoritmos e comite", "motor_dia0_modelos",
     "So o canal de cobranca (provedor) ja da o maximo: AUC 0,65 a 0,67 fora do tempo. Acrescentar qualquer outra informacao nao melhora (0,59 a 0,65). Canal: Pluggy ~10% de calote, Efi automatico 6% a 31%, Manual 42%, Pix agendado 74%. Contratos de 59 parcelas sao um produto novo de junho (valor mediano R$ 500, juros 100%) com calote de 13% contra 34%, mas isso ja e explicado pelo canal.",
     "Candidatos simples no D+0: so provedor e provedor + Pix. O ganho real esta em decisao de produto (canal de cobranca) e em dados novos (Open Finance), nao em mais variaveis."),
    ("Primeira rodada de algoritmos", "10 algoritmos x 6 conjuntos de variaveis (logistica L1/L2, floresta, extra trees, boosting, NB, kNN, SVM, MLP, comite)", "validacao temporal", "motor_algoritmos",
     "Arvores e boosting vencem no dia 0 com o conjunto enxuto (AUC 0,63); mais variaveis pioram; logistica regularizada vence de D+5 em diante.", "Base da escolha atual de algoritmos por etapa."),
    ("Informacao mutua e redundancia", "Relacao nao linear de cada variavel com o alvo e pares de variaveis quase iguais", "informacao mutua; Spearman", "motor_informacao_mutua, motor_pares_redundantes, motor_matriz_associacao",
     "Informacao mutua baixa em quase tudo no dia 0; 44 pares redundantes (|rho| >= 0,7).", "Variaveis redundantes nao entram juntas."),
    ("Midia e OCR", "Imagem (CLIP), PDF, audio, video e OCR dos prints de maquininha contra calote", "univariado com FDR", "motor_correlacoes (bloco midia); trusted_chatwoot.anexo_*",
     "Nenhuma midia passa. OCR: so 26 prints com total vendido, sem poder estatistico.", "Fora do motor."),
    ("Bureau (Dora e DeskData)", "Score PF/PJ, faixa, dividas, protestos, processos, veredito, identidade, vendas, vinculo da maquininha", "univariado, modelo de aprovacao, comparacao entre aprovados", "motor_correlacoes; analytics.fato_bureau_*",
     "Nao separa calote entre os aprovados (AUC ~0,5); prediz muito bem quem vira contrato (AUC 0,87), ou seja, o bureau decide a aprovacao e ha restricao de faixa.", "Fora do motor de calote; vale no funil de aprovacao."),
    ("Estudo inicial de outubro", "Primeiro estudo de cluster, embeddings, regras e modelos por cliente", "varios", "tabelas estudo_*, modelo_*, quadros_*, llm_*, conversas_*",
     "Substituido pelo motor; fica como historico.", "Nao usar como fonte atual."),
]


def main():
    ch = cf.conecta()
    ch.command("DROP TABLE IF EXISTS analytics_ml.motor_testes_realizados")
    ch.command(f"CREATE TABLE analytics_ml.motor_testes_realizados ({COLS}) ENGINE = MergeTree ORDER BY (tipo_teste, alvo, estrato) "
               "COMMENT 'TODOS os testes e cruzamentos ja feitos no motor de credito, em formato unico (um teste por linha). Consultar antes de testar de novo. Combinacoes de 1 a 4 variaveis entram so com q<0,10; a lista completa esta em motor_combinacoes_1a4. Resumo por familia: motor_testes_resumo.'")
    for sql in PARTES:
        ch.command("INSERT INTO analytics_ml.motor_testes_realizados " + sql)
    n = ch.query("SELECT count() FROM analytics_ml.motor_testes_realizados").result_rows[0][0]
    print("motor_testes_realizados", n, flush=True)
    # resumo
    ch.command("DROP TABLE IF EXISTS analytics_ml.motor_testes_resumo")
    ch.command("CREATE TABLE analytics_ml.motor_testes_resumo (familia String COMMENT 'familia de teste', o_que_foi_cruzado String COMMENT 'quais variaveis e contra o que', metodo String COMMENT 'como foi testado', "
               "tabela_detalhe String COMMENT 'tabela com o detalhe', n_testes UInt64 COMMENT 'quantos testes ou linhas ha na tabela de detalhe', achados String COMMENT 'o que se descobriu', "
               "uso_no_motor String COMMENT 'o que o motor faz com isso') ENGINE = MergeTree ORDER BY familia "
               "COMMENT 'Resumo por familia de teste: o que foi cruzado, como, quantos testes, o que se achou e o que o motor faz com isso. Comece por aqui; o detalhe esta em motor_testes_realizados e nas tabelas indicadas.'")
    contagens = {
        "motor_correlacoes": "analytics_ml.motor_correlacoes", "motor_combinacoes_condicionadas": "analytics_ml.motor_combinacoes_condicionadas", "motor_combinacoes_1a4": "analytics_ml.motor_combinacoes_1a4",
        "motor_efeito_ajustado": "analytics_ml.motor_efeito_ajustado", "motor_sazonalidade_controlada": "analytics_ml.motor_sazonalidade_controlada", "motor_matriz_modelos": "analytics_ml.motor_matriz_modelos",
        "motor_matriz_por_idade e motor_ganho_por_idade": "analytics_ml.motor_ganho_por_idade", "motor_dia0_modelos": "analytics_ml.motor_dia0_modelos", "motor_tom_testes": "analytics_ml.motor_tom_testes", "motor_algoritmos": "analytics_ml.motor_algoritmos",
        "motor_informacao_mutua, motor_pares_redundantes, motor_matriz_associacao": "analytics_ml.motor_informacao_mutua",
    }
    linhas = []
    for fam, o_que, metodo, tab, achados, uso in RESUMO:
        alvo = contagens.get(tab)
        try:
            nt = ch.query(f"SELECT count() FROM {alvo}").result_rows[0][0] if alvo else 0
        except Exception:  # noqa
            nt = 0
        linhas.append((fam, o_que, metodo, tab, nt, achados, uso))
    ch.insert("analytics_ml.motor_testes_resumo", linhas, column_names=["familia", "o_que_foi_cruzado", "metodo", "tabela_detalhe", "n_testes", "achados", "uso_no_motor"])
    print("motor_testes_resumo", len(linhas), flush=True)


if __name__ == "__main__":
    main()
