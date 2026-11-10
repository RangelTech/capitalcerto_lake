# Experimento de modelagem da Capital Certo: probabilidade de inadimplencia e de cada "quadro de pagamento" a partir de dados
# cadastrais, Receita Federal (CNPJ) e conversas. Numpy e pandas puros. Separado das DAGs de producao.
#
# Desenho (preregistrado, para nao cair em p-hacking):
#   alvos: y30  = no 1o contrato, alguma parcela vencida nos primeiros 30 dias ficou mais de 7 dias atrasada (horizonte fixo: tira
#                 o vies de "cliente antigo teve mais tempo para atrasar"; so contratos com 40+ dias);
#          yatraso = tem parcela vencida em aberto hoje; quadros = nunca pagou / pagou e parou / atrasa mas paga / paga em dia.
#   conjuntos de variaveis (cumulativos): A controles de cohort, B + cadastro, C + Receita Federal, D + conversas/LLM.
#   modelos: regressao logistica L2 e boosting de stumps (Newton). Validacao cruzada estratificada 5x3, AUC, comparada com o
#   percentil 95 do AUC obtido embaralhando o alvo (acaso). So se declara sinal quando o AUC passa do acaso.
#   Vazamento: so entram variaveis conhecidas ANTES do contrato ou que nao dependem do desfecho. A situacao cadastral atual da
#   Receita (baixada/inapta) pode ser consequencia da inadimplencia e fica fora dos conjuntos principais (so em ablacao).
# MLOps: cada execucao grava run, parametros, metricas e versao do codigo em analytics_ml.modelo_execucoes; coeficientes,
# scores fora da amostra por cliente e tabela de decis (lift) ficam versionados por run_id.
import hashlib
import math
import os
import uuid

import numpy as np
import pandas as pd

import ml_inadimplencia as ml
import ml_quadros as mq

QUADROS = mq.QUADROS
SEED = 17

SQL_BASE = """
WITH prim AS (
    SELECT pessoa_id, argMin(contrato_id, (data_inicio, criado_em)) AS contrato_id, min(data_inicio) AS inicio,
           toFloat64(argMin(valor_principal, (data_inicio, criado_em))) AS valor1
    FROM analytics.fato_contrato WHERE empresa = 'Capital Certo' GROUP BY pessoa_id
),
alvo AS (
    SELECT p.pessoa_id AS pessoa_id, p.inicio AS inicio, p.valor1 AS valor1, p.contrato_id AS contrato_id,
           countIf((f.status = 'PAID' AND f.dias_atraso_ate_pagar > 7) OR (f.status != 'PAID' AND f.dias_atraso_em_aberto > 7)) AS atrasadas_gt7, count() AS parcelas30
    FROM prim p JOIN analytics.fato_parcela f ON f.contrato_id = p.contrato_id
    WHERE f.data_vencimento <= p.inicio + 30 AND f.status != 'CANCELLED' GROUP BY p.pessoa_id, p.inicio, p.valor1, p.contrato_id
),
msgs AS (
    SELECT cl.pessoa_id AS pessoa_id, count() AS msgs_pre FROM analytics.cliente_360 cl
    JOIN trusted_chatwoot.conversations c FINAL ON c.contact_id = cl.contato_chatwoot_principal_id
    JOIN trusted_chatwoot.messages m FINAL ON m.conversation_id = c.id
    WHERE m.message_type = 0 AND m.created_at < cl.primeiro_contrato_em + INTERVAL 1 DAY AND cl.tem_vinculo_chatwoot AND cl.empresa = 'Capital Certo' GROUP BY cl.pessoa_id
)
SELECT a.pessoa_id AS pessoa_id, a.inicio AS inicio_1o_contrato, a.valor1 AS valor_1o_contrato, a.atrasadas_gt7 AS atrasadas_gt7, a.parcelas30 AS parcelas30,
       ifNull(m.msgs_pre, 0) AS msgs_pre, d.provedor_pagamento AS gateway_1o_contrato
FROM alvo a LEFT JOIN msgs m ON m.pessoa_id = a.pessoa_id
LEFT JOIN analytics.dim_contrato d ON d.contrato_id = a.contrato_id
"""

SQL_RFB = """
SELECT pessoa_id, any(ifNull(situacao_cadastral, '')) AS rfb_situacao, any(toString(data_abertura)) AS rfb_abertura, any(ifNull(porte_receita, '')) AS rfb_porte,
       any(toFloat64(ifNull(capital_social, 0))) AS rfb_capital, any(ifNull(natureza_juridica, '')) AS rfb_natureza, any(ifNull(cnae_divisao, '')) AS rfb_cnae_div,
       any(ifNull(uf_receita, '')) AS rfb_uf, any(toString(toUInt8(optante_mei))) AS rfb_mei, any(toString(toUInt8(optante_simples))) AS rfb_simples,
       any(toFloat64(qtd_socios)) AS rfb_socios, any(ifNull(matriz_ou_filial, '')) AS rfb_filial, any(toString(toUInt8(tem_cnae_secundario))) AS rfb_cnae_sec
FROM analytics.dim_cnpj_cliente GROUP BY pessoa_id
"""


# ----------------------------------------------------------------------------- dados
def montar(ref):
    ch = ml._ch()
    base = ch.query_df(SQL_BASE)
    perfil = ch.query_df(mq.SQL.replace("{ref}", ref))
    for c in ["pagas", "venc", "atr_pago"]:
        perfil[c] = pd.to_numeric(perfil[c]).fillna(0)
    perfil["quadro"] = perfil.apply(mq._quadro, axis=1)
    perfil = mq._derivar(perfil)
    df = perfil.merge(base, on="pessoa_id", how="inner")
    df["inicio_1o_contrato"] = pd.to_datetime(df["inicio_1o_contrato"])
    refd = pd.Timestamp(ref)
    df["y30"] = (df["atrasadas_gt7"] > 0).astype(int)
    df["elegivel_y30"] = (refd - df["inicio_1o_contrato"]).dt.days >= 40
    df["yatraso"] = (df["venc"] > 0).astype(int)
    df["idade_na_contratacao"] = np.nan  # faixa etaria atual e suficiente aqui
    df["meses_desde_out25"] = (df["inicio_1o_contrato"].dt.year - 2025) * 12 + df["inicio_1o_contrato"].dt.month - 10
    # RFB (opcional: so existe depois que a DAG rfb_cnpj_mensal rodou)
    tem_rfb = ch.query("SELECT count() FROM system.tables WHERE database='analytics' AND name='dim_cnpj_cliente'").result_rows[0][0] > 0
    if tem_rfb:
        r = ch.query_df(SQL_RFB)
        df = df.merge(r, on="pessoa_id", how="left")
        for c in r.columns:
            if c == "pessoa_id":
                continue
            if c in ("rfb_capital", "rfb_socios"):
                df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
            else:
                df[c] = df[c].astype(object).where(df[c].notna(), "")
        ab = pd.to_datetime(df["rfb_abertura"], errors="coerce")
        df["rfb_idade_meses_na_contratacao"] = ((df["inicio_1o_contrato"] - ab).dt.days / 30.4).where(ab.notna())
        for c in ["rfb_porte", "rfb_natureza", "rfb_cnae_div", "rfb_mei", "rfb_simples", "rfb_filial", "rfb_cnae_sec"]:
            df[c] = df[c].fillna("").replace("", "sem_rfb")
        df["rfb_log_capital"] = np.log1p(pd.to_numeric(df["rfb_capital"], errors="coerce"))
        df["rfb_uf_bate_com_ddd"] = np.where(df["rfb_uf"].fillna("") == "", "sem_rfb", np.where(df["rfb_uf"] == df["uf_pelo_ddd"], "bate", "diferente"))
        df["rfb_nao_ativa_hoje"] = np.where(df["rfb_situacao"].fillna("") == "", "sem_rfb", np.where(df["rfb_situacao"] == "ativa", "ativa", "nao_ativa"))
    # LLM (opcional)
    llm = ch.query("SELECT count() FROM system.tables WHERE database='analytics_ml' AND name='llm_piloto_features'").result_rows[0][0] > 0
    if llm:
        f = ch.query_df("SELECT * EXCEPT (data_referencia, inadimplente) FROM analytics_ml.llm_piloto_features")
        df = df.merge(f, on="pessoa_id", how="left")
    return df, tem_rfb, llm


def conjuntos(df, tem_rfb, llm):
    cat_cohort = ["gateway_1o_contrato"]
    num_cohort = ["valor_1o_contrato", "meses_desde_out25"]
    cat_cad = ["sexo", "faixa_etaria", "uf_endereco", "uf_pelo_ddd", "tipo_pessoa", "segmento", "email_tipo", "nome_forma_juridica", "cpf_regiao_fiscal", "cnpj_matriz_ou_filial", "porte_asaas"]
    cat_rfb = ["rfb_cnae_div", "rfb_porte", "rfb_natureza", "rfb_uf_bate_com_ddd", "rfb_filial", "rfb_mei", "rfb_simples", "rfb_cnae_sec"]
    num_rfb = ["rfb_idade_meses_na_contratacao", "rfb_log_capital", "rfb_socios"]
    cat_llm = ["finalidade", "tom", "menciona_divida_ou_negativacao", "dificuldade_de_caixa", "pede_valor_maior_que_o_oferecido", "negocia_taxa_ou_prazo", "demora_ou_resiste_a_enviar_documentos"]
    num_llm = ["urgencia", "consistencia_das_informacoes", "informacao_voluntaria"]
    df["sem_perfil"] = ((df["sexo"] == "sem_info") | (df["segmento"] == "sem_info")).astype(int)
    df["msgs_pre_log"] = np.log1p(df["msgs_pre"])
    A = (cat_cohort, num_cohort + ["sem_perfil"])
    B = (A[0] + cat_cad, A[1])
    out = {"A_cohort": A, "B_cohort_e_cadastro": B}
    if tem_rfb:
        C = (B[0] + cat_rfb, B[1] + num_rfb)
        out["C_mais_receita_federal"] = C
    base = out["C_mais_receita_federal"] if tem_rfb else B
    if llm:
        out["D_mais_conversas_e_llm"] = (base[0] + cat_llm, base[1] + num_llm + ["msgs_pre_log"])
    else:
        out["D_mais_volume_de_conversa"] = (base[0], base[1] + ["msgs_pre_log"])
    return out


def matriz(df, cats, nums, min_freq=15):
    cols, nomes = [], []
    for c in cats:
        s = df[c].astype(str).fillna("sem_info")
        s = s.where(s.map(s.value_counts()) >= min_freq, "outros")
        ref = s.value_counts().index[0]
        for v in sorted(s.unique()):
            if v == ref:
                continue
            cols.append((s == v).astype(float).values)
            nomes.append(f"{c}={v}")
    for c in nums:
        x = pd.to_numeric(df[c], errors="coerce")
        falta = x.isna()
        if falta.any():
            cols.append(falta.astype(float).values)
            nomes.append(f"{c}_ausente")
        x = x.fillna(x.median() if x.notna().any() else 0.0)
        sd = x.std() or 1.0
        cols.append(((x - x.mean()) / sd).values)
        nomes.append(c)
    return np.column_stack(cols), nomes


# ----------------------------------------------------------------------------- modelos
def sigm(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def fit_logit(X, y, l2=3.0):
    b, _ = ml.logistica(np.column_stack([np.ones(len(y)), X]), y, l2=l2, iters=25)
    return b


def pred_logit(b, X):
    return np.column_stack([np.ones(len(X)), X]) @ b


def candidatos(X, k=6):
    """indicadores de corte (n x m): binarios viram um corte; numericos viram k cortes por quantil"""
    cols, info = [], []
    for j in range(X.shape[1]):
        v = X[:, j]
        u = np.unique(v)
        if len(u) <= 2:
            cols.append((v > u.min()).astype(float))
            info.append((j, float(u.min()) if len(u) == 2 else 0.0))
        else:
            for q in np.quantile(v, np.linspace(0.1, 0.9, k)):
                cols.append((v > q).astype(float))
                info.append((j, float(q)))
    return np.column_stack(cols), info


def fit_boost(X, y, rounds=80, lr=0.1, lam=5.0):
    S, info = candidatos(X)
    F = np.full(len(y), math.log(y.mean() / (1 - y.mean())))
    arvores = []
    for _ in range(rounds):
        p = sigm(F)
        g, h = y - p, np.clip(p * (1 - p), 1e-6, None)
        GL, HL = S.T @ g, S.T @ h
        G, H = g.sum(), h.sum()
        ganho = GL ** 2 / (HL + lam) + (G - GL) ** 2 / (H - HL + lam) - G ** 2 / (H + lam)
        k = int(np.argmax(ganho))
        wl, wr = GL[k] / (HL[k] + lam), (G - GL[k]) / (H - HL[k] + lam)
        arvores.append((info[k], wl, wr))
        F += lr * np.where(S[:, k] > 0, wl, wr)
    return arvores, math.log(y.mean() / (1 - y.mean())), lr


def pred_boost(m, X):
    arvores, f0, lr = m
    F = np.full(len(X), f0)
    for (j, thr), wl, wr in arvores:
        F += lr * np.where(X[:, j] > thr, wl, wr)
    return F


def cv(X, y, modelo="logit", folds=5, rep=3, seed=SEED, devolve_oof=False):
    rng = np.random.default_rng(seed)
    oof = np.zeros(len(y))
    aucs = []
    for _ in range(rep):
        perm = rng.permutation(len(y))
        fold = np.zeros(len(y), dtype=int)
        for g in (perm[y[perm] == 1], perm[y[perm] == 0]):
            fold[g] = np.arange(len(g)) % folds
        pred = np.zeros(len(y))
        for f in range(folds):
            tr, te = fold != f, fold == f
            if modelo == "logit":
                pred[te] = pred_logit(fit_logit(X[tr], y[tr]), X[te])
            else:
                pred[te] = pred_boost(fit_boost(X[tr], y[tr]), X[te])
        aucs.append(ml.auc(y, pred))
        oof += pred / rep
    return (float(np.mean(aucs)), float(np.std(aucs)), oof) if devolve_oof else (float(np.mean(aucs)), float(np.std(aucs)))


def nulo_p95(X, y, n=25, modelo="logit", seed=SEED + 5):
    rng = np.random.default_rng(seed)
    v = [cv(X, rng.permutation(y), modelo, rep=1, seed=int(rng.integers(1e6)))[0] for _ in range(n)]
    return float(np.quantile(v, 0.95)), float(np.mean(v))


# ----------------------------------------------------------------------------- execucao
def rodar(ref=None):
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    df, tem_rfb, llm = montar(ref)
    sets = conjuntos(df, tem_rfb, llm)
    run_id = uuid.uuid4().hex[:12]
    versao = hashlib.sha1(open(__file__, "rb").read()).hexdigest()[:10]
    execs, coefs, oof_champ = [], [], {}
    alvos = {"y30": (df[df["elegivel_y30"]], "y30"), "yatraso": (df, "yatraso")}
    for nome_alvo, (d, col) in alvos.items():
        y = d[col].values.astype(float)
        base_auc = None
        for nome_set, (cats, nums) in sets.items():
            X, nomes = matriz(d, cats, nums)
            for modelo in ("logit", "boost"):
                a, sd, oof = cv(X, y, modelo, devolve_oof=True)
                p95, mnulo = nulo_p95(X, y, 15 if modelo == "boost" else 25, modelo)
                if nome_set == "A_cohort" and modelo == "logit":
                    base_auc = a
                execs.append(dict(run_id=run_id, alvo=nome_alvo, conjunto=nome_set, modelo=modelo, clientes=len(y), prevalencia=float(y.mean()), variaveis=X.shape[1],
                                  auc_cv=a, auc_desvio=sd, auc_nulo_p95=p95, auc_nulo_medio=mnulo, acima_do_acaso=bool(a > p95), ganho_vs_cohort=a - (base_auc if base_auc is not None else a),
                                  versao_codigo=versao, data_referencia=ref))
                print(nome_alvo, nome_set, modelo, round(a, 3), "nulo p95", round(p95, 3), flush=True)
                if modelo == "logit":
                    b = fit_logit(X, y)
                    for n_, c_ in zip(nomes, b[1:]):
                        coefs.append(dict(run_id=run_id, alvo=nome_alvo, conjunto=nome_set, variavel=n_, coeficiente=float(c_), odds_ratio=float(math.exp(c_)), data_referencia=ref))
                    oof_champ[(nome_alvo, nome_set)] = (d["pessoa_id"].values, oof, y)
    E = pd.DataFrame(execs)
    # campeao por alvo: maior AUC que passa do acaso
    E["campeao"] = False
    for alvo, g in E.groupby("alvo"):
        ok = g[g["acima_do_acaso"]]
        if len(ok):
            E.loc[ok["auc_cv"].idxmax(), "campeao"] = True
    # scores e decis do melhor conjunto logit por alvo (campeao se houver, senao o melhor AUC)
    S = []
    for alvo, g in E[E["modelo"] == "logit"].groupby("alvo"):
        escolhido = g.loc[g["auc_cv"].idxmax(), "conjunto"]
        ids, oof, y = oof_champ[(alvo, escolhido)]
        dec = pd.qcut(pd.Series(oof).rank(method="first"), 10, labels=False) + 1
        S.append(pd.DataFrame(dict(run_id=run_id, alvo=alvo, conjunto=escolhido, pessoa_id=ids, probabilidade=sigm(oof), decil=dec.values, desfecho=y.astype(int), data_referencia=ref)))
    S = pd.concat(S)
    # quadros: probabilidade de cada quadro (um contra todos), melhor conjunto disponivel
    q = df[df["quadro"].notna()]
    cats, nums = sets[list(sets)[-1]]
    X, nomes = matriz(q, cats, nums)
    Q = pd.DataFrame({"run_id": run_id, "pessoa_id": q["pessoa_id"].values, "quadro_real": q["quadro"].values, "data_referencia": ref})
    qex = []
    for quadro in QUADROS:
        y = (q["quadro"] == quadro).values.astype(float)
        if y.sum() < 15:
            continue
        a, sd, oof = cv(X, y, "logit", devolve_oof=True)
        p95, _ = nulo_p95(X, y, 25, "logit")
        Q[f"p_{quadro}"] = sigm(oof)
        qex.append(dict(run_id=run_id, quadro=quadro, quadro_pt=mq.ROTULOS[quadro], clientes=len(y), clientes_no_quadro=int(y.sum()), auc_cv=a, auc_desvio=sd, auc_nulo_p95=p95,
                        acima_do_acaso=bool(a > p95), conjunto=list(sets)[-1], data_referencia=ref))
        print("quadro", quadro, round(a, 3), "nulo p95", round(p95, 3), flush=True)
    # lift por decil (campeao)
    L = S.groupby(["alvo", "conjunto", "decil"]).agg(clientes=("desfecho", "size"), taxa_desfecho=("desfecho", "mean"), prob_media=("probabilidade", "mean")).reset_index()
    L["run_id"], L["data_referencia"] = run_id, ref
    C = pd.DataFrame(coefs)
    guarda = [("modelo_execucoes", E, "Experimento. Cada linha e uma combinacao alvo x conjunto de variaveis x modelo, com AUC de validacao cruzada, percentil 95 do AUC por acaso, ganho sobre o cohort e versao do codigo. Historico acumulado por run_id.", True),
              ("modelo_coeficientes", C, "Experimento. Coeficientes e odds ratio da regressao logistica por run, alvo e conjunto (variaveis padronizadas).", True),
              ("modelo_score_cliente", S, "Experimento. Probabilidade fora da amostra por cliente (CPF/CNPJ em hash) no melhor conjunto de cada alvo, com decil e desfecho real.", True),
              ("modelo_decis", L, "Experimento. Taxa do desfecho por decil de probabilidade (lift) no melhor conjunto de cada alvo.", True),
              ("modelo_prob_quadros", Q, "Experimento. Probabilidade fora da amostra de cada quadro de pagamento por cliente (um contra todos).", True),
              ("modelo_quadros_metricas", pd.DataFrame(qex), "Experimento. AUC por quadro de pagamento comparado ao acaso.", True)]
    for nome, tab, desc, acumula in guarda:
        _gravar(ch, nome, tab, desc, acumula)
    print("run", run_id, "versao", versao)
    return E, pd.DataFrame(qex)


def _gravar(ch, nome, df, desc, acumula):
    """Acumula historico (MLOps): cria a tabela se nao existe e so acrescenta o run novo."""
    ch.command(f"CREATE DATABASE IF NOT EXISTS {ml.DB_ML}")
    existe = ch.query(f"SELECT count() FROM system.tables WHERE database='{ml.DB_ML}' AND name='{nome}'").result_rows[0][0]
    if not existe:
        ml._salvar(ch, nome, df.iloc[0:0], desc, {})
        ch.command(f"DROP TABLE {ml.DB_ML}.{nome}")
        defs = []
        for c in df.columns:
            if pd.api.types.is_bool_dtype(df[c]):
                t = "Bool"
            elif pd.api.types.is_integer_dtype(df[c]):
                t = "Int64"
            elif pd.api.types.is_float_dtype(df[c]):
                t = "Nullable(Float64)"
            else:
                t = "String"
            defs.append(f"`{c}` {t}")
        ch.command(f"CREATE TABLE {ml.DB_ML}.{nome} ({', '.join(defs)}) ENGINE = MergeTree ORDER BY tuple() COMMENT '{desc.replace(chr(39), ' ')}'")
    dados = df.astype(object).where(pd.notnull(df), None)
    ch.insert(f"{ml.DB_ML}.{nome}", dados.values.tolist(), column_names=list(df.columns))


# ----------------------------------------------------------------------------- Receita x quadros (atributo por atributo)
ATR_RFB = {"rfb_cnae_div": "Divisão do CNAE", "rfb_idade_faixa": "Idade da empresa na contratação", "rfb_porte": "Porte na Receita", "rfb_natureza": "Natureza jurídica",
           "rfb_mei": "Optante MEI", "rfb_simples": "Optante Simples", "rfb_filial": "Matriz ou filial", "rfb_uf_bate_com_ddd": "UF da Receita bate com o DDD",
           "rfb_capital_faixa": "Capital social", "rfb_socios_faixa": "Quantidade de sócios", "rfb_cnae_sec": "Tem CNAE secundário", "rfb_nao_ativa_hoje": "Situação cadastral hoje (pode ser consequência)"}


def receita_x_quadros(ref=None):
    """Cramer V normalizado (descontado o acaso por permutacao) de cada atributo da Receita contra o quadro de pagamento, so clientes CNPJ."""
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    df, tem_rfb, _ = montar(ref)
    if not tem_rfb:
        raise RuntimeError("dim_cnpj_cliente nao existe: rode a DAG rfb_cnpj_mensal antes")
    d = df[(df["quadro"].notna()) & (df["rfb_situacao"] != "")].copy()
    d["rfb_idade_faixa"] = pd.cut(d["rfb_idade_meses_na_contratacao"], [-1e9, 12, 36, 84, 1e9], labels=["menos de 1 ano", "1 a 3 anos", "3 a 7 anos", "mais de 7 anos"]).astype(str)
    d["rfb_capital_faixa"] = pd.cut(d["rfb_capital"].fillna(0), [-1, 0, 1000, 10000, 1e15], labels=["zero", "ate 1 mil", "1 a 10 mil", "mais de 10 mil"]).astype(str)
    d["rfb_socios_faixa"] = pd.cut(d["rfb_socios"].fillna(0), [-1, 0, 1, 1e9], labels=["nenhum", "1", "2 ou mais"]).astype(str)
    mq.ATRIBUTOS.update(ATR_RFB)
    assoc, lv = [], []
    for a in ATR_RFB:
        if d[a].nunique() < 2:
            continue
        assoc.append(mq.associacao(d, a, "so_cnpj"))
        lv += mq.niveis(d, a, "so_cnpj")
    A, L = pd.DataFrame(assoc), pd.DataFrame(lv)
    A["rank_z"] = A["z"].rank(ascending=False, method="first").astype(int)
    A["sinal"] = np.where((A["p_permutacao"] < 0.05) & (A["z"] > 2), "acima do acaso", "dentro do acaso")
    for t in (A, L):
        t["data_referencia"] = ref
    ml._salvar(ch, "quadros_receita_atributo", A, "Experimento. Atributos da Receita Federal (CNAE, idade da empresa, porte, MEI, Simples, capital, socios...) x quadro de pagamento, so clientes CNPJ, com V de Cramer descontado do acaso por permutacao.",
               dict(atributo="codigo", atributo_pt="nome", escopo="so_cnpj", clientes="clientes CNPJ", niveis="niveis", cramers_v="V bruto", v_acaso_medio="V medio do acaso", v_excedente="V menos acaso", z="sinal sobre ruido",
                    p_permutacao="chance de sair por sorte", rank_z="ranking por z", sinal="acima ou dentro do acaso", data_referencia="data de corte"))
    ml._salvar(ch, "quadros_receita_nivel", L, "Experimento. Participacao de cada quadro de pagamento em cada nivel dos atributos da Receita.",
               dict(atributo="codigo", atributo_pt="nome", nivel="valor", escopo="so_cnpj", quadro="codigo do quadro", quadro_pt="quadro", clientes_no_nivel="clientes no nivel", clientes_no_quadro="clientes no quadro",
                    pct_no_nivel="participacao", pct_ic_baixo="Wilson inf", pct_ic_alto="Wilson sup", pct_geral="participacao geral", lift="lift", pct_suavizado="suavizada", lift_suavizado="lift suavizado",
                    grupo_pequeno="menos de 30", data_referencia="data de corte"))
    print(A.sort_values("rank_z")[["atributo", "niveis", "cramers_v", "v_acaso_medio", "z", "p_permutacao", "sinal"]].round(3).to_string())
    return A, L


# ----------------------------------------------------------------------------- alerta precoce (comportamento nos primeiros dias)
SQL_PRECOCE = """
WITH prim AS (SELECT pessoa_id, argMin(contrato_id, (data_inicio, criado_em)) AS contrato_id, min(data_inicio) AS inicio,
                     toFloat64(argMin(valor_principal, (data_inicio, criado_em))) AS valor1 FROM analytics.fato_contrato WHERE empresa = 'Capital Certo' GROUP BY pessoa_id)
SELECT p.pessoa_id AS pessoa_id, p.valor1 AS valor1, p.inicio AS inicio,
       countIf(f.data_vencimento <= p.inicio + {h}) AS venc_ate_h,
       countIf(f.data_vencimento <= p.inicio + {h} AND f.status = 'PAID' AND f.data_pagamento <= p.inicio + {h}) AS pagas_ate_h,
       countIf(f.data_vencimento <= p.inicio + {h} AND f.status = 'PAID' AND f.data_pagamento <= f.data_vencimento + 1) AS pagas_no_prazo_ate_h,
       countIf(f.data_vencimento > p.inicio + {h} AND f.data_vencimento <= p.inicio + {h2} AND ((f.status = 'PAID' AND f.dias_atraso_ate_pagar > 7) OR (f.status != 'PAID' AND f.dias_atraso_em_aberto > 7))) AS atrasadas_depois
FROM prim p JOIN analytics.fato_parcela f ON f.contrato_id = p.contrato_id
WHERE f.status != 'CANCELLED' AND p.inicio <= toDate('{ref}') - {h2} - 8 GROUP BY p.pessoa_id, p.valor1, p.inicio
"""


def alerta_precoce(ref=None, h=7, h2=45):
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    d = ch.query_df(SQL_PRECOCE.replace("{h}", str(h)).replace("{h2}", str(h2)).replace("{ref}", ref))
    base = montar(ref)[0][["pessoa_id", "gateway_1o_contrato", "meses_desde_out25", "sem_perfil"]] if False else None
    df, tem_rfb, llm = montar(ref)
    d = d.merge(df[["pessoa_id", "gateway_1o_contrato", "meses_desde_out25"]].assign(sem_perfil=((df["sexo"] == "sem_info") | (df["segmento"] == "sem_info")).astype(int)), on="pessoa_id", how="inner")
    d = d[d["venc_ate_h"] >= 3].copy()
    d["pct_pago_ate_h"] = d["pagas_ate_h"] / d["venc_ate_h"]
    d["pct_no_prazo_ate_h"] = d["pagas_no_prazo_ate_h"] / d["venc_ate_h"]
    y = (d["atrasadas_depois"] > 0).values.astype(float)
    sets = {"A_cohort": (["gateway_1o_contrato"], ["valor1", "meses_desde_out25", "sem_perfil"]),
            "E_cohort_e_primeiros_7_dias": (["gateway_1o_contrato"], ["valor1", "meses_desde_out25", "sem_perfil", "pct_pago_ate_h", "pct_no_prazo_ate_h", "venc_ate_h"])}
    linhas, run_id = [], uuid.uuid4().hex[:12]
    versao = hashlib.sha1(open(__file__, "rb").read()).hexdigest()[:10]
    for nome, (cats, nums) in sets.items():
        X, nomes = matriz(d, cats, nums)
        for modelo in ("logit", "boost"):
            a, sd, oof = cv(X, y, modelo, devolve_oof=True)
            p95, mn = nulo_p95(X, y, 25 if modelo == "logit" else 15, modelo)
            linhas.append(dict(run_id=run_id, alvo=f"alerta_precoce_atraso_dias_{h+1}_a_{h2}", conjunto=nome, modelo=modelo, clientes=len(y), prevalencia=float(y.mean()), variaveis=X.shape[1], auc_cv=a,
                               auc_desvio=sd, auc_nulo_p95=p95, auc_nulo_medio=mn, acima_do_acaso=bool(a > p95), ganho_vs_cohort=0.0, campeao=False, versao_codigo=versao, data_referencia=ref))
            print(nome, modelo, round(a, 3), "nulo p95", round(p95, 3), "n", len(y), "prev", round(y.mean(), 3), flush=True)
    E = pd.DataFrame(linhas)
    base = E[(E["conjunto"] == "A_cohort") & (E["modelo"] == "logit")]["auc_cv"].iloc[0]
    E["ganho_vs_cohort"] = E["auc_cv"] - base
    _gravar(ch, "modelo_execucoes", E, "", True)
    return E


if __name__ == "__main__":
    rodar()
