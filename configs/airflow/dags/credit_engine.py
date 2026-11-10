# Motor de credito da Capital Certo, parte 3: treino diario, escolha do melhor modelo por etapa, calibracao, inferencia e monitoramento.
#
# Desenho (decidido com base no laboratorio de 2026-11-09, ver spec):
#   * O motor tem UM modelo por idade do contrato (etapas D+0, D+1, D+2, D+3, D+5, D+7, D+10, D+14). O alvo de cada etapa e "calote":
#     20% ou mais das parcelas com vencimento depois de D+k e ate D+45 ainda em aberto e vencidas (com 30 dias de prazo para regularizar).
#   * As features da etapa k so usam o que se sabia em D+k (pagamentos feitos ate aquele dia). Sem vazamento.
#   * Na contratacao (D+0) os dados do CRM, do Chatwoot e da Receita NAO separam bom de mau pagador (AUC ~0,5 fora do tempo); o motor
#     diz isso (confianca 'nenhuma') em vez de fingir. O sinal vem do comportamento de pagamento: D+7 ~0,8 e D+14 ~0,85 de AUC.
#   * Todo dia: reconstroi as features, refaz a validacao temporal (treina so com rotulo ja fechado), escolhe a melhor configuracao por
#     etapa, calibra com as previsoes fora do tempo, re-treina com tudo e pontua os contratos ativos.
import io
import json
import os
import re

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

import credit_experimentos as ex
import credit_features as cf

VERSAO_CODIGO = "motor-2026-11-10.2"
ALVOS = {"calote": "ye_{k}", "inadimplencia": "yi_{k}", "cluster_ruim": "cluster_ruim"}   # alvo -> coluna de rotulo por etapa k
FAIXAS = [(0.15, "baixo"), (0.30, "medio"), (0.50, "alto"), (1.01, "muito_alto")]
MESES_RECENTES = 5            # a validacao e a escolha do modelo usam so os 5 meses de teste mais recentes
JANELA_ATIVA_DIAS = 50          # contratos mais velhos ja tem o resultado observado: nao se pontua
DEGRADACAO_AUC = 0.05           # queda de AUC fora do tempo que aciona o fallback para o modelo anterior
BUCKET_MODELOS = "models"


def faixa_de(p):
    for lim, nome in FAIXAS:
        if p < lim:
            return nome


def confianca_de(auc):
    if auc is None or auc != auc:
        return "nenhuma"
    return "alta" if auc >= 0.78 else "media" if auc >= 0.68 else "baixa" if auc >= 0.58 else "nenhuma"


ETAPAS_AMPLIADAS = (1, 2, 3)   # idades em que a bateria 3 achou ganho com informacao de cadastro alem do comportamento de pagamento
PENALIDADE_AMPLIADO = 0.01      # um candidato ampliado so ganha do simples se superar por mais que isto (evita escolher por sorte entre muitos candidatos)


DESLIGADAS = set()   # variaveis que o administrador desligou na aba Motor de credito do Data IA Hub (hub_sandbox.motor_parametros); carregadas a cada execucao


def carregar_desligadas(ch):
    global DESLIGADAS
    try:
        r = ch.query("SELECT variavel FROM hub_sandbox.motor_parametros FINAL WHERE ativa = 0 AND estado = 'desligado'").result_rows
        DESLIGADAS = {x[0] for x in r}
    except Exception:  # noqa  (tabela ainda nao existe: nada desligado)
        DESLIGADAS = set()
    if DESLIGADAS:
        print("variaveis DESLIGADAS pelo administrador (fora do treino):", sorted(DESLIGADAS), flush=True)
    return DESLIGADAS


def _base(c):
    return re.sub(r"^e\d+_", "", c)


def _sem_desligadas(cfgs):
    """Tira das configuracoes as variaveis desligadas (a de comportamento de pagamento sai em todos os dias: e7_pct_no_prazo -> pct_no_prazo)."""
    if not DESLIGADAS:
        return cfgs
    out = {}
    for chave, (cats, nums) in cfgs.items():
        c2, n2 = [c for c in cats if _base(c) not in DESLIGADAS], [n for n in nums if _base(n) not in DESLIGADAS]
        if c2 or n2:
            out[chave] = (c2, n2)
    return out


def configs_da_etapa(df, k):
    return _sem_desligadas(_configs_da_etapa(df, k))


def _configs_da_etapa(df, k):
    cats, nums = ex.CAT_CRM + ex.CAT_GEO, ex.NUM_CONTRATO + ex.NUM_CLIENTE + ex.NUM_GEO + ex.NUM_HIST
    novos = ["cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para", "dom_inicio"]   # variaveis achadas na bateria de 10/11 (compartilhamento, inconsistencia, cluster do anterior)
    nums = nums + novos
    ek = ex.colunas_etapa(df, k)
    if k == 0:
        parc_nums = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]
        # comparacao de algoritmos de 10/11 (credit_scan3): no dia 0 o conjunto enxuto vence os largos; floresta e gradient boosting ~0,63, logistica L1 ~0,62 em y_calote_30
        parc_novos = parc_nums + ["cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para"]
        # estudo do dia 0 (bateria_dia0, 10/11): o CANAL de cobranca (provedor) sozinho ja da o maximo que os dados permitem (AUC 0,65 a 0,67 fora do tempo); acrescentar produto curto,
        # tom, endereco dividido, valor/faturamento ou idade NAO melhora (0,59 a 0,65). Entram os candidatos simples; vence quem validar melhor (empate favorece o mais simples).
        return {("so_provedor", "logistica_L1"): (["provedor_pagamento"], []), ("provedor+pix", "logistica_L1"): (["provedor_pagamento"], ["pix_agendado"]),
                ("provedor+pix", "rf"): (["provedor_pagamento"], ["pix_agendado"]),
                ("so_base", "lightgbm"): (cats, nums), ("parcimonioso+compart", "rf"): (["provedor_pagamento"], parc_novos), ("parcimonioso+compart", "hgb"): (["provedor_pagamento"], parc_novos),
                ("parcimonioso+compart", "lightgbm"): (["provedor_pagamento"], parc_novos), ("parcimonioso+compart", "logistica_L1"): (["provedor_pagamento"], parc_novos), ("parcimonioso", "lightgbm"): (["provedor_pagamento"], parc_nums), ("parcimonioso", "rf"): (["provedor_pagamento"], parc_nums),
                ("parcimonioso", "hgb"): (["provedor_pagamento"], parc_nums), ("parcimonioso", "logistica_L1"): (["provedor_pagamento"], parc_nums)}
    cfgs = {
        ("so_comportamento", "logistica"): ([], ek),
        ("so_comportamento", "lightgbm"): ([], ek),
        ("base+comportamento", "logistica"): (cats, nums + ek),
        ("base+comportamento", "lightgbm"): (cats, nums + ek),
    }
    if k in ETAPAS_AMPLIADAS:
        # bateria 3 (10/11, matriz por idade): so nos primeiros dias, quando quase nao ha pagamento para olhar, informacao de cadastro (endereco dividido, contrato anterior,
        # inconsistencias) melhora o modelo de forma significativa (D+1: ganho medio +0,02 a +0,06, IC95 acima de zero) e arvores aleatorias vencem a logistica. De D+5 em diante
        # acrescentar informacao NAO ajuda (ganho medio -0,04 a -0,05): o motor segue so com comportamento. Estes candidatos pagam uma penalidade de 0,01 de AUC (ver treinar).
        amp = ek + ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio", "cmp_end", "cmp_qualquer", "inconsistencias", "anterior_paga_e_para"]
        for modelo in ("rf", "extratrees", "logistica_L1"):
            cfgs[("ampliado+cadastro", modelo)] = (["provedor_pagamento"], amp)
    return cfgs


def _limpa(df, cats, nums):
    out = df[nums + cats].copy()
    for c in cats:
        out[c] = out[c].astype(object).where(out[c].notna(), np.nan)
    for c in nums:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    return out


def ajustar(treino, alvo, cats, nums, modelo):
    """Treina com todo o rotulo disponivel. Devolve (pipeline, nums, cats) ja filtrados."""
    nf = [c for c in nums if pd.to_numeric(treino[c], errors="coerce").notna().sum() >= 8 and pd.to_numeric(treino[c], errors="coerce").nunique() > 1]
    cf_ = [c for c in cats if treino[c].notna().sum() >= 8 and treino[c].nunique() > 1]
    pipe = ex.pipeline(modelo, cf_, nf)
    pipe.fit(_limpa(treino, cf_, nf), treino[alvo].astype(int))
    return pipe, nf, cf_


def calibrador(o):
    """Calibra a probabilidade com as previsoes FORA DO TEMPO (a mesma incerteza que o modelo vai ter em producao)."""
    y, p = o["y"].values, o["p"].values
    if len(o) >= 800 and 0 < y.sum() < len(y):  # isotonica so com bastante dado; com poucos pontos ela vai a extremos (0,1%) e nao e confiavel
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999).fit(p, y)
        return ("isotonic", iso)
    lr = LogisticRegression(C=1e6, max_iter=1000).fit(np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4))).reshape(-1, 1), y)
    return ("platt", lr)


def aplicar_calibrador(cal, p):
    tipo, obj = cal
    p = np.asarray(p, dtype=float)
    if tipo == "isotonic":
        return np.clip(obj.predict(p), 0.02, 0.98)
    lg = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4))).reshape(-1, 1)
    return np.clip(obj.predict_proba(lg)[:, 1], 0.02, 0.98)


def explicar(pipe, modelo, X, nomes_num, nomes_cat, top=3):
    """Os `top` fatores que mais empurram o risco para cima em cada linha de X (nome, efeito)."""
    pre, m = pipe.named_steps["p"], pipe.named_steps["m"]
    Z = pre.transform(X)
    if hasattr(Z, "toarray"):
        Z = Z.toarray()
    if modelo in ("logistica", "logistica_L1"):
        nomes = list(pre.get_feature_names_out())
        contrib = Z * m.coef_[0]
    elif modelo in ("rf", "hgb", "extratrees"):
        # perturbacao por linha: cada variavel tratada como desconhecida (imputada pela mediana do treino) e queda da probabilidade
        p0 = pipe.predict_proba(X)[:, 1]
        cols = list(nomes_num) + list(nomes_cat)
        nomes = cols
        contrib = np.zeros((len(X), len(cols)))
        for j, c in enumerate(cols):
            X2 = X.copy()
            X2[c] = pd.Series([np.nan] * len(X2), index=X2.index, dtype=object) if c in nomes_cat else np.nan
            contrib[:, j] = p0 - pipe.predict_proba(X2)[:, 1]
    else:
        nomes = nomes_num + nomes_cat
        contrib = m.booster_.predict(Z, pred_contrib=True)[:, :-1]
    saidas = []
    for i in range(len(contrib)):
        ordem = np.argsort(-contrib[i])[:top]
        saidas.append(json.dumps([{"fator": nomes[j].replace("n__", "").replace("c__", ""), "efeito": round(float(contrib[i][j]), 3)} for j in ordem if contrib[i][j] > 0],
                                 ensure_ascii=False))
    return saidas


def importancia(pipe, modelo, nomes_num, nomes_cat):
    m = pipe.named_steps["m"]
    if modelo in ("logistica", "logistica_L1"):
        nomes = list(pipe.named_steps["p"].get_feature_names_out())
        return sorted(zip(nomes, np.abs(m.coef_[0])), key=lambda x: -x[1])[:15]
    if modelo in ("rf", "hgb", "extratrees"):
        nomes = list(pipe.named_steps["p"].get_feature_names_out())
        imp = getattr(m, "feature_importances_", np.zeros(len(nomes)))
        return sorted(zip(nomes, imp), key=lambda x: -x[1])[:15]
    return sorted(zip(nomes_num + nomes_cat, m.booster_.feature_importance(importance_type="gain")), key=lambda x: -x[1])[:15]


def treinar(df, hoje=None, etapas=None, tipo="calote"):
    """Treina todas as etapas para um alvo (calote, inadimplencia ou cluster_ruim). Devolve (modelos, tabela_de_execucoes, previsoes_fora_do_tempo, importancias)."""
    modelos, linhas, previsoes, imps = {}, [], [], []
    for k in (etapas if etapas is not None else cf.ETAPAS):
        alvo = ALVOS[tipo].format(k=k)
        if alvo not in df.columns:
            continue
        d = df[df[alvo].notna()]
        if len(d) < 120 or d[alvo].nunique() < 2:
            print(f"[{tipo}] etapa D+{k}: sem rotulo suficiente ({len(d)}), pulada", flush=True)
            continue
        melhor = None
        for (nome, modelo), (cats, nums) in configs_da_etapa(df, k).items():
            o = ex.avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, [c for c in cats if c in df.columns], [c for c in nums if c in df.columns], modelo,
                                   ultimos_meses=MESES_RECENTES)
            if not len(o):
                continue
            m = ex.metricas(o)
            linhas.append({"alvo": tipo, "etapa": k, "features": nome, "modelo": modelo, "n_treino_final": len(d), **m, "escolhido": 0})
            chave = (m["auc"] - (0.005 if modelo == "lightgbm" else 0) - (PENALIDADE_AMPLIADO if nome.startswith("ampliado") else 0), nome, modelo)  # empate favorece o modelo mais simples
            if melhor is None or chave[0] > melhor[0][0]:
                melhor = (chave, o, cats, nums, modelo, nome, m)
        if melhor is None:
            continue
        _, o, cats, nums, modelo, nome, m = melhor
        pipe, nf, cf_ = ajustar(d, alvo, [c for c in cats if c in df.columns], [c for c in nums if c in df.columns], modelo)
        cal = calibrador(o)
        o = o.assign(alvo=tipo, etapa=k, p_calibrado=aplicar_calibrador(cal, o["p"].values))
        previsoes.append(o)
        for lin in linhas:
            if lin["etapa"] == k and lin["alvo"] == tipo and lin["features"] == nome and lin["modelo"] == modelo:
                lin["escolhido"] = 1
        for fator, valor in importancia(pipe, modelo, nf, cf_):
            imps.append({"alvo": tipo, "etapa": k, "fator": fator, "importancia": float(valor)})
        modelos[k] = {"pipe": pipe, "nums": nf, "cats": cf_, "modelo": modelo, "features": nome, "cal": cal, "auc_oot": m["auc"], "brier_oot": m["brier"],
                      "n": len(d), "taxa": float(d[alvo].mean())}
        print(f"[{tipo}] etapa D+{k}: escolhido {nome}/{modelo} AUC fora do tempo {m['auc']} (n={m['n']}) confianca={confianca_de(m['auc'])}", flush=True)
    return modelos, pd.DataFrame(linhas), (pd.concat(previsoes) if previsoes else pd.DataFrame()), pd.DataFrame(imps)


def pontuar(df, modelos, hoje=None):
    """Pontua os contratos ativos (idade de 1 a JANELA_ATIVA_DIAS) com o modelo da maior etapa k <= idade."""
    hoje = pd.Timestamp(hoje or pd.Timestamp.now().normalize())
    d = df.copy()
    d["idade_dias"] = (hoje - pd.to_datetime(d["data_inicio"])).dt.days
    d = d[d["idade_dias"] >= 0]      # todo contrato existente e pontuado; os antigos usam a etapa mais alta (D+14) e trazem o resultado observado
    saidas = []
    d["etapa"] = d["idade_dias"].map(lambda a: max([k for k in cf.ETAPAS if k <= a and k in modelos], default=-1))
    for k, g in d[d["etapa"] >= 0].groupby("etapa"):
        mod = modelos[k]
        X = _limpa(g, mod["cats"], mod["nums"])
        p_bruto = mod["pipe"].predict_proba(X)[:, 1]
        p = aplicar_calibrador(mod["cal"], p_bruto)
        saidas.append(pd.DataFrame({
            "contrato_id": g["contrato_id"].values, "cliente_id": g["cliente_id"].values, "pessoa_id": g["pessoa_id"].values,
            "data_inicio": pd.to_datetime(g["data_inicio"]).dt.date.values, "idade_dias": g["idade_dias"].values, "etapa": k,
            "prob_calote": p, "prob_bruta": p_bruto, "faixa_risco": [faixa_de(x) for x in p],
            "confianca_modelo": confianca_de(mod["auc_oot"]), "auc_fora_do_tempo": mod["auc_oot"], "modelo": mod["modelo"], "features": mod["features"],
            "motivos": explicar(mod["pipe"], mod["modelo"], X, mod["nums"], mod["cats"]),
            "valor_principal": g["valor_principal"].astype(float).values,
            "fase": np.where(g["idade_dias"].values <= JANELA_ATIVA_DIAS, "ativo", "encerrado"),
            "calote_observado": pd.to_numeric(g[f"ye_{k}"], errors="coerce").values if f"ye_{k}" in g.columns else np.nan,
        }))
    return pd.concat(saidas) if saidas else pd.DataFrame()


# ---------------------------------------------------------------- persistencia (ClickHouse + MinIO)
DDL = {
    "analytics.score_contrato": """
        CREATE TABLE IF NOT EXISTS analytics.score_contrato (
            data_referencia Date COMMENT 'dia em que o motor rodou e gerou este score',
            run_id String COMMENT 'execucao do motor (motor_execucoes.run_id)',
            contrato_id String COMMENT 'contrato (analytics.dim_contrato)',
            cliente_id String COMMENT 'cliente (analytics.dim_cliente)',
            pessoa_id String COMMENT 'hash do CPF/CNPJ, igual nos dois CNPJs da Capital Certo',
            data_inicio Date COMMENT 'inicio do contrato',
            idade_dias Int32 COMMENT 'dias desde o inicio do contrato na data de referencia',
            etapa Int8 COMMENT 'etapa do motor usada: maior idade k (0,1,2,3,5,7,10,14) menor ou igual a idade do contrato',
            prob_calote Float32 COMMENT 'probabilidade calibrada de calote: 20% ou mais das parcelas com vencimento depois da etapa e ate 45 dias ficarem em aberto e vencidas',
            prob_bruta Float32 COMMENT 'saida do modelo antes da calibracao',
            faixa_risco LowCardinality(String) COMMENT 'baixo (<15%), medio (15-30%), alto (30-50%), muito_alto (>=50%)',
            confianca_modelo LowCardinality(String) COMMENT 'confianca da etapa pelo AUC fora do tempo: nenhuma (<0,58), baixa, media (>=0,68), alta (>=0,78). Em nenhuma, a faixa nao ordena risco',
            auc_fora_do_tempo Float32 COMMENT 'AUC da etapa na validacao temporal deste dia',
            modelo LowCardinality(String) COMMENT 'logistica ou lightgbm',
            features LowCardinality(String) COMMENT 'conjunto de features: so_base, so_comportamento ou base+comportamento',
            motivos String COMMENT 'JSON com ate 3 fatores que mais aumentam o risco neste contrato e o tamanho do efeito',
            valor_principal Float64 COMMENT 'valor principal do contrato',
            versao_codigo LowCardinality(String) COMMENT 'versao do codigo do motor',
            fase LowCardinality(String) COMMENT 'ativo (ate 50 dias de contrato) ou encerrado (mais antigo: o score e o que o motor daria em D+14)',
            calote_observado Nullable(Int8) COMMENT 'calote realmente observado para a etapa (1 sim, 0 nao); vazio se o contrato ainda nao amadureceu. Serve para auditar previsto x real'
        ) ENGINE = MergeTree ORDER BY (data_referencia, contrato_id)
        COMMENT 'Score diario do motor de credito por contrato ativo (historico: uma linha por contrato e por dia). Ver a view score_contrato_atual.'""",
    "analytics_ml.motor_execucoes": """
        CREATE TABLE IF NOT EXISTS analytics_ml.motor_execucoes (
            run_id String COMMENT 'identificador da execucao diaria', data_referencia Date COMMENT 'dia da execucao', etapa Int8 COMMENT 'idade do contrato em dias',
            features LowCardinality(String) COMMENT 'conjunto de features', modelo LowCardinality(String) COMMENT 'algoritmo',
            n_treino_final Int32 COMMENT 'contratos com rotulo fechado usados no treino final', n Int32 COMMENT 'previsoes fora do tempo na validacao',
            taxa Float32 COMMENT 'taxa de calote nas previsoes fora do tempo', auc Float32 COMMENT 'AUC fora do tempo', auc_ic_lo Float32 COMMENT 'limite inferior do IC 95%',
            auc_ic_hi Float32 COMMENT 'limite superior do IC 95%', pr_auc Float32 COMMENT 'area sob a curva precisao x recall', ks Float32 COMMENT 'estatistica KS',
            brier Float32 COMMENT 'erro de Brier (menor e melhor)', brier_base Float32 COMMENT 'Brier de prever sempre a taxa media', logloss Float32 COMMENT 'log loss',
            lift_top_decil Float32 COMMENT 'taxa de calote no 10% de maior risco dividida pela taxa geral', escolhido UInt8 COMMENT '1 = configuracao escolhida para a etapa neste dia',
            fallback UInt8 COMMENT '1 = o retreino piorou mais que o limite e o motor usou o modelo anterior', versao_codigo LowCardinality(String) COMMENT 'versao do codigo'
        ) ENGINE = MergeTree ORDER BY (data_referencia, etapa, features, modelo)
        COMMENT 'Resultado da validacao temporal de cada configuracao testada, por etapa, a cada execucao diaria do motor.'""",
    "analytics_ml.motor_backtest": """
        CREATE TABLE IF NOT EXISTS analytics_ml.motor_backtest (
            run_id String COMMENT 'execucao', etapa Int8 COMMENT 'etapa', contrato_id String COMMENT 'contrato', mes String COMMENT 'mes de teste (fora do tempo)',
            y UInt8 COMMENT 'calote observado (1) ou nao (0)', p Float32 COMMENT 'probabilidade bruta', p_calibrado Float32 COMMENT 'probabilidade calibrada', n_treino Int32 COMMENT 'contratos de treino da dobra'
        ) ENGINE = MergeTree ORDER BY (run_id, etapa, contrato_id)
        COMMENT 'Previsoes fora do tempo da execucao: base para curvas de calibracao e analises no Metabase.'""",
    "analytics_ml.motor_importancia": """
        CREATE TABLE IF NOT EXISTS analytics_ml.motor_importancia (
            run_id String COMMENT 'execucao', data_referencia Date COMMENT 'dia', etapa Int8 COMMENT 'etapa', fator String COMMENT 'feature',
            importancia Float64 COMMENT 'ganho (lightgbm) ou valor absoluto do coeficiente (logistica)'
        ) ENGINE = MergeTree ORDER BY (data_referencia, etapa, importancia)
        COMMENT 'Os 15 fatores mais importantes do modelo escolhido por etapa, por dia.'""",
}


def garantir_tabelas(ch):
    ch.command("CREATE DATABASE IF NOT EXISTS analytics_ml")
    for ddl in DDL.values():
        ch.command(ddl)
    # tabelas criadas antes das colunas novas
    for t in ("motor_execucoes", "motor_backtest", "motor_importancia"):
        ch.command(f"ALTER TABLE analytics_ml.{t} ADD COLUMN IF NOT EXISTS alvo LowCardinality(String) DEFAULT 'calote' COMMENT 'alvo do modelo: calote, inadimplencia (parcelas pagas com atraso ou abertas) ou cluster_ruim (trajetoria paga e para / quase nao paga)'")
    ch.command("ALTER TABLE analytics.score_contrato ADD COLUMN IF NOT EXISTS prob_inadimplencia Nullable(Float32) COMMENT 'probabilidade calibrada de INADIMPLENCIA: 30% ou mais das parcelas ate D+45 pagas com mais de 3 dias de atraso ou ainda em aberto'")
    ch.command("ALTER TABLE analytics.score_contrato ADD COLUMN IF NOT EXISTS prob_cluster_ruim Nullable(Float32) COMMENT 'probabilidade calibrada de o contrato cair no cluster de trajetoria ruim (paga no comeco e para, ou quase nao paga)'")
    ch.command("ALTER TABLE analytics.score_contrato ADD COLUMN IF NOT EXISTS fase LowCardinality(String) COMMENT 'ativo (ate 50 dias) ou encerrado (score = o que o motor daria em D+14)'")
    ch.command("ALTER TABLE analytics.score_contrato ADD COLUMN IF NOT EXISTS calote_observado Nullable(Int8) COMMENT 'calote observado para a etapa (1 sim, 0 nao); vazio se nao amadureceu'")
    ch.command("""CREATE OR REPLACE VIEW analytics.score_contrato_atual AS
                  SELECT * FROM analytics.score_contrato WHERE data_referencia = (SELECT max(data_referencia) FROM analytics.score_contrato)""")


def gravar(ch, run_id, data_ref, execs, previsoes, imps, scores):
    data_ref = pd.Timestamp(data_ref).date()  # coluna Date do ClickHouse nao aceita texto
    if len(execs):
        e = execs.copy()
        e.insert(0, "run_id", run_id)
        e.insert(1, "data_referencia", data_ref)
        e["fallback"] = e.get("fallback", 0)
        e["versao_codigo"] = VERSAO_CODIGO
        cols = ["run_id", "data_referencia", "alvo", "etapa", "features", "modelo", "n_treino_final", "n", "taxa", "auc", "auc_ic_lo", "auc_ic_hi", "pr_auc", "ks", "brier",
                "brier_base", "logloss", "lift_top_decil", "escolhido", "fallback", "versao_codigo"]
        ch.insert_df("analytics_ml.motor_execucoes", e[cols])
    if len(previsoes):
        b = previsoes.rename(columns={"p_calibrado": "p_calibrado"}).copy()
        b.insert(0, "run_id", run_id)
        ch.insert_df("analytics_ml.motor_backtest", b[["run_id", "alvo", "etapa", "contrato_id", "mes", "y", "p", "p_calibrado", "n_treino"]])
    if len(imps):
        i = imps.copy()
        i.insert(0, "run_id", run_id)
        i.insert(1, "data_referencia", data_ref)
        ch.insert_df("analytics_ml.motor_importancia", i[["run_id", "data_referencia", "alvo", "etapa", "fator", "importancia"]])
    if len(scores):
        s = scores.copy()
        s.insert(0, "data_referencia", data_ref)
        s.insert(1, "run_id", run_id)
        s["versao_codigo"] = VERSAO_CODIGO
        s["calote_observado"] = pd.to_numeric(s["calote_observado"], errors="coerce").astype("Int8")
        ch.command(f"ALTER TABLE analytics.score_contrato DELETE WHERE data_referencia = '{data_ref.isoformat()}'", settings={"mutations_sync": 1})
        ch.insert_df("analytics.score_contrato", s[["data_referencia", "run_id", "contrato_id", "cliente_id", "pessoa_id", "data_inicio", "idade_dias", "etapa", "prob_calote",
                                                     "prob_bruta", "faixa_risco", "confianca_modelo", "auc_fora_do_tempo", "modelo", "features", "motivos", "valor_principal",
                                                     "versao_codigo", "fase", "calote_observado", "prob_inadimplencia", "prob_cluster_ruim"]])


def salvar_modelos(modelos, data_ref):
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    try:
        s3.create_bucket(Bucket=BUCKET_MODELOS)
    except Exception:  # noqa
        pass
    buf = io.BytesIO()
    joblib.dump(modelos, buf)
    s3.put_object(Bucket=BUCKET_MODELOS, Key=f"credit_motor/{data_ref}/modelos.joblib", Body=buf.getvalue())
    s3.put_object(Bucket=BUCKET_MODELOS, Key="credit_motor/ultimo/modelos.joblib", Body=buf.getvalue())
    s3.put_object(Bucket=BUCKET_MODELOS, Key=f"credit_motor/{data_ref}/manifesto.json",
                  Body=json.dumps({k: {x: v[x] for x in ("modelo", "features", "auc_oot", "brier_oot", "n", "taxa")} for k, v in modelos.items()}, indent=1).encode())


def salvar_extra(extra, data_ref):
    """Modelos dos outros alvos (inadimplencia, cluster_ruim) em arquivo separado, para nao quebrar quem le o modelos.joblib de calote."""
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    buf = io.BytesIO()
    joblib.dump(extra, buf)
    s3.put_object(Bucket=BUCKET_MODELOS, Key=f"credit_motor/{data_ref}/modelos_extra.joblib", Body=buf.getvalue())
    s3.put_object(Bucket=BUCKET_MODELOS, Key="credit_motor/ultimo/modelos_extra.joblib", Body=buf.getvalue())


def carregar_modelos_anteriores():
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    s3 = S3Hook(aws_conn_id="minio_s3").get_conn()
    try:
        return joblib.load(io.BytesIO(s3.get_object(Bucket=BUCKET_MODELOS, Key="credit_motor/ultimo/modelos.joblib")["Body"].read()))
    except Exception:  # noqa
        return {}


def gravar_features(ch, df):
    """Tabela de features e rotulos por contrato (Gold de apoio ao motor e a analise por humanos ou pelo Claude Code). Reconstruida a cada execucao."""
    cols = [c for c in df.columns if not c.startswith("_") and c not in ("documento_final", "cidade")]
    d = df[cols].copy()
    tipos, ddl = {}, []
    for c in cols:
        s = d[c]
        if pd.api.types.is_bool_dtype(s):
            t, d[c] = "Nullable(UInt8)", s.astype("float").astype("Int64")
        elif pd.api.types.is_integer_dtype(s):
            t = "Nullable(Int64)"
        elif pd.api.types.is_float_dtype(s):
            t = "Nullable(Float64)"
        elif pd.api.types.is_datetime64_any_dtype(s):
            t, d[c] = "Nullable(DateTime64(3))", pd.to_datetime(s).dt.tz_localize(None)
        else:
            t, d[c] = "Nullable(String)", s.astype(object).where(s.notna(), None).map(lambda x: None if x is None else str(x))
        ddl.append(f"`{c}` {t} COMMENT '{descricao_feature(c).replace(chr(39), ' ')}'")
    ch.command("DROP TABLE IF EXISTS analytics_ml.motor_features_contrato__novo")
    ch.command(f"CREATE TABLE analytics_ml.motor_features_contrato__novo ({', '.join(ddl)}) ENGINE = MergeTree ORDER BY tuple() "
               "COMMENT 'Uma linha por contrato com as features do motor de credito (conhecidas na contratacao e nos primeiros dias), os comportamentos por etapa e os rotulos de calote. Reconstruida a cada execucao diaria.'")
    ch.insert_df("analytics_ml.motor_features_contrato__novo", d)
    existe = ch.query("select count() from system.tables where database='analytics_ml' and name='motor_features_contrato'").result_rows[0][0]
    if existe:
        ch.command("EXCHANGE TABLES analytics_ml.motor_features_contrato AND analytics_ml.motor_features_contrato__novo")
        ch.command("DROP TABLE analytics_ml.motor_features_contrato__novo")
    else:
        ch.command("RENAME TABLE analytics_ml.motor_features_contrato__novo TO analytics_ml.motor_features_contrato")


def descricao_feature(c):
    m = re.match(r"e(\d+)_(.+)", c)
    if m:
        return f"comportamento de pagamento COMO ESTAVA em D+{m.group(1)}: {m.group(2)}"
    if c.startswith("ye_"):
        return f"rotulo: calote nas parcelas depois de D+{c[3:]} ate D+45 (1 = 20% ou mais das parcelas ainda em aberto e vencidas)"
    if c.startswith("yi_"):
        return f"rotulo: INADIMPLENCIA nas parcelas depois de D+{c[3:]} ate D+45 (1 = 30% ou mais das parcelas pagas com mais de 3 dias de atraso ou ainda em aberto)"
    if c == "cluster":
        return "cluster de trajetoria de pagamento (k-means em D+1..D+45, so contratos maduros)"
    if c == "cluster_ruim":
        return "rotulo: 1 se o cluster e paga no comeco e para ou quase nao paga"
    if c.startswith("tj_"):
        return "metrica da trajetoria de pagamento D+1..D+45 (SO PARA CONTRATO MADURO, usada para formar o cluster; nao usar como feature de previsao)"
    if c.startswith("y_calote_"):
        return f"rotulo: calote nas parcelas ate D+{c[9:]}"
    if c.startswith("pct_calote_"):
        return f"proporcao de parcelas ate D+{c[11:]} em aberto e vencidas hoje"
    if c.startswith("cmp_"):
        return "quantos OUTROS clientes dividem o mesmo dado de identidade (tel, email, end = endereco, resp = CPF do responsavel; qualquer = quantos tipos)"
    if c.startswith("anterior_paga"):
        return "o contrato anterior do mesmo CPF/CNPJ caiu neste cluster de trajetoria (so vale se o anterior ja estava maduro ao iniciar este)"
    if c == "inconsistencias":
        return "indice 0-4 de inconsistencias cadastrais: sem CEP, sem idade, DDD diferente do CEP, dado compartilhado"
    if c.startswith("prev_"):
        return "historico do cliente em contratos anteriores, so com parcelas vencidas antes do inicio deste contrato"
    return c.replace("'", " ").replace("_", " ")


def rodar_dia(data_ref=None):
    """Execucao completa do dia: features -> treino/validacao -> escolha -> calibracao -> pontuacao -> gravacao -> artefatos."""
    import pendulum

    data_ref = data_ref or pendulum.now("America/Sao_Paulo").to_date_string()
    run_id = f"{data_ref}-{pd.Timestamp.now().strftime('%H%M%S')}"
    ch = cf.conecta()
    garantir_tabelas(ch)
    hoje = pd.Timestamp(data_ref)
    carregar_desligadas(ch)
    df = cf.montar(ch, hoje=hoje)
    print(f"features: {len(df)} contratos, {df.shape[1]} colunas", flush=True)
    gravar_features(ch, df)
    modelos, execs, previsoes, imps = treinar(df, hoje)
    extra = {}
    for tipo in ("inadimplencia", "cluster_ruim"):
        try:
            m2, e2, p2, i2 = treinar(df, hoje, tipo=tipo)
            extra[tipo] = m2
            execs, previsoes, imps = pd.concat([execs, e2]), pd.concat([previsoes, p2]), pd.concat([imps, i2])
        except Exception as ex_:  # noqa
            print(f"ALERTA alvo {tipo} falhou: {str(ex_)[:200]}", flush=True)
    anteriores = carregar_modelos_anteriores()
    execs["fallback"] = execs.get("fallback", 0)
    execs["fallback"] = 0
    for k in list(modelos):
        ant = anteriores.get(k)
        if ant and modelos[k]["auc_oot"] < ant["auc_oot"] - DEGRADACAO_AUC:
            print(f"ALERTA etapa D+{k}: AUC caiu de {ant['auc_oot']} para {modelos[k]['auc_oot']}; mantem o modelo anterior", flush=True)
            modelos[k] = ant
            execs.loc[(execs["alvo"] == "calote") & (execs["etapa"] == k) & (execs["escolhido"] == 1), "fallback"] = 1
    scores = pontuar(df, modelos, hoje)
    for tipo, col in (("inadimplencia", "prob_inadimplencia"), ("cluster_ruim", "prob_cluster_ruim")):
        if extra.get(tipo) and len(scores):
            s2 = pontuar(df, extra[tipo], hoje)
            scores[col] = scores["contrato_id"].map(s2.drop_duplicates("contrato_id").set_index("contrato_id")["prob_calote"]) if len(s2) else np.nan
        else:
            scores[col] = np.nan
    gravar(ch, run_id, data_ref, execs, previsoes, imps, scores)
    salvar_modelos(modelos, data_ref)
    salvar_extra(extra, data_ref)
    print(f"{run_id}: {len(scores)} contratos pontuados; faixas: {scores['faixa_risco'].value_counts().to_dict() if len(scores) else {}}", flush=True)
    return run_id
