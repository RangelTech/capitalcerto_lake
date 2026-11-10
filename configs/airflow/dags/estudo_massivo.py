# Estudo massivo da Capital Certo: onde (se em algum lugar) ha sinal de inadimplencia alem do cohort.
# Numpy e pandas puros, paralelizado por processos. Separado das DAGs de producao.
#
# Desenho:
#  1. Blocos de variaveis: COH (cohort: gateway, valor, mes, cadastro sem perfil), CAD (cadastro, localidade, sexo, idade, CPF/CNPJ),
#     RFB (Receita: CNAE, idade da empresa, porte, MEI/Simples, capital, socios, rede de socios), CONV (conversa: volume, horario, LLM v1 e v2,
#     estimativa do proprio LLM), EMB (embedding semantico da conversa: componentes principais e cluster).
#  2. Triagem: os 15 subconjuntos dos 4 blocos CAD/RFB/CONV/EMB, com e sem COH, x 9 algoritmos x 5 alvos, CV 5x2.
#  3. Validacao dura dos melhores: CV 5x5; acaso por permutacao simples E por permutacao ESTRATIFICADA por cohort (embaralha o alvo dentro de
#     gateway x terco de mes). Declara sinal so quando passa do p95 do acaso estratificado e ganha do melhor modelo que so tem o cohort.
#  4. Selecao recursiva (forward, aninhada): quais variaveis entram, na ordem, e o AUC honesto do procedimento.
#  5. Mineração de regras: todas as conjuncoes de ate 3 condicoes (suporte >= 25), com limite de familia (max-z sob permutacao estratificada).
#  6. Embeddings: clusters da conversa x desfecho, kNN, regressao em componentes e a estimativa do LLM como preditor.
import math
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

import ml_inadimplencia as ml
import ml_modelo as mm
import ml_quadros as mq

SEED = 23
ALVOS = ["yatraso", "y30", "q_paga_em_dia", "q_pagou_e_parou", "q_atrasa_mas_paga"]
G = {}  # dados globais herdados pelos processos filhos (fork)


# ----------------------------------------------------------------------------- dados
SQL_CONV = """
SELECT cl.pessoa_id AS pessoa_id, count() AS msgs, dateDiff('day', min(m.created_at), max(m.created_at)) AS dur_dias, uniqExact(c.id) AS n_conversas,
       avg(toHour(m.created_at - INTERVAL 3 HOUR) < 6) AS frac_noite, avg(length(ifNull(m.content, ''))) AS tam_medio
FROM analytics.cliente_360 cl JOIN trusted_chatwoot.conversations c FINAL ON c.contact_id = cl.contato_chatwoot_principal_id
JOIN trusted_chatwoot.messages m FINAL ON m.conversation_id = c.id
WHERE m.message_type = 0 AND m.created_at < cl.primeiro_contrato_em + INTERVAL 1 DAY AND cl.tem_vinculo_chatwoot AND cl.empresa = 'Capital Certo' GROUP BY cl.pessoa_id
"""
SQL_REDE = """
SELECT c.pessoa_id AS pessoa_id, max(r.qtd_empresas_do_socio) AS rede_max_empresas, max(r.qtd_empresas_ativas_do_socio) AS rede_max_ativas,
       max(r.outros_clientes_com_o_mesmo_socio) AS rede_outros_clientes
FROM analytics.ponte_rede_socios r JOIN analytics.dim_cliente c ON c.cliente_id = r.cliente_id GROUP BY c.pessoa_id
"""
SQL_PF_SOCIO = "SELECT c.pessoa_id AS pessoa_id, count() AS pf_empresas_como_socio, sum(situacao_cadastral = 'ativa') AS pf_empresas_ativas FROM analytics.ponte_cliente_como_socio p JOIN analytics.dim_cliente c ON c.cliente_id = p.cliente_id GROUP BY c.pessoa_id"


def pca(M, k):
    Z = M - M.mean(0)
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    return U[:, :k] * S[:k]


def kmeans(X, k, seed=SEED, it=50):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)]
    for _ in range(it):
        d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
        a = d.argmin(1)
        Cn = np.array([X[a == j].mean(0) if (a == j).any() else C[j] for j in range(k)])
        if np.allclose(Cn, C):
            break
        C = Cn
    return a, C


def montar_dados(ref):
    ch = ml._ch()
    df, tem_rfb, _ = mm.montar(ref)
    df["sem_perfil"] = ((df["sexo"] == "sem_info") | (df["segmento"] == "sem_info")).astype(int)
    df["msgs_pre_log"] = np.log1p(df["msgs_pre"])
    for sql in (SQL_CONV, SQL_REDE, SQL_PF_SOCIO):
        try:
            t = ch.query_df(sql)
            for c in t.columns:
                if c != "pessoa_id":
                    t[c] = pd.to_numeric(t[c], errors="coerce").astype(float)
            df = df.merge(t, on="pessoa_id", how="left")
        except Exception as e:
            print("aviso:", str(e)[:100])
    for c in ["rede_max_empresas", "rede_max_ativas", "rede_outros_clientes", "pf_empresas_como_socio", "pf_empresas_ativas"]:
        if c in df:
            df[c] = df[c].fillna(0)
    # LLM v2
    v2 = ch.query_df("SELECT * EXCEPT (data_referencia) FROM analytics_ml.llm_v2_features")
    v2 = v2.rename(columns={c: "v2_" + c for c in v2.columns if c != "pessoa_id"})
    for c in v2.columns:
        if c != "pessoa_id" and v2[c].dtype == object:
            v2[c] = v2[c].astype(str)
        elif c != "pessoa_id":
            v2[c] = pd.to_numeric(v2[c], errors="coerce").astype(float)
    df = df.merge(v2, on="pessoa_id", how="left")
    # embeddings
    em = ch.query_df("SELECT pessoa_id, embedding FROM analytics_ml.embeddings_conversa")
    M = np.array(em["embedding"].tolist(), dtype=float)
    M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
    P = pca(M, 20)
    pcs = pd.DataFrame(P, columns=[f"emb_pc{i+1}" for i in range(20)])
    pcs["pessoa_id"] = em["pessoa_id"].values
    cl, _ = kmeans(P[:, :10], 8)
    pcs["emb_cluster"] = cl.astype(str)
    df = df.merge(pcs, on="pessoa_id", how="left")
    G["emb_pessoas"], G["emb_M"], G["emb_P"] = em["pessoa_id"].values, M, P
    df["tem_conversa"] = df["emb_pc1"].notna()
    for q in ("paga_em_dia", "pagou_e_parou", "atrasa_mas_paga"):
        df[f"q_{q}"] = np.where(df["quadro"].isna(), np.nan, (df["quadro"] == q).astype(float))
    df["estrato"] = df["gateway_1o_contrato"].astype(str) + "_" + pd.qcut(df["meses_desde_out25"].rank(method="first"), 3, labels=False).astype(str)
    return df


def blocos(df):
    cat_cad = ["sexo", "faixa_etaria", "uf_endereco", "uf_pelo_ddd", "tipo_pessoa", "segmento", "email_tipo", "nome_forma_juridica", "cpf_regiao_fiscal", "cnpj_matriz_ou_filial", "porte_asaas", "ddd"]
    cat_rfb = [c for c in ["rfb_cnae_div", "rfb_porte", "rfb_natureza", "rfb_uf_bate_com_ddd", "rfb_filial", "rfb_mei", "rfb_simples", "rfb_cnae_sec"] if c in df]
    num_rfb = [c for c in ["rfb_idade_meses_na_contratacao", "rfb_log_capital", "rfb_socios", "rede_max_empresas", "rede_max_ativas", "rede_outros_clientes", "pf_empresas_como_socio", "pf_empresas_ativas"] if c in df]
    cat_conv = ["finalidade", "tom", "v2_finalidade", "v2_tom", "v2_tipo_de_negocio"] + ["menciona_divida_ou_negativacao", "dificuldade_de_caixa", "pede_valor_maior_que_o_oferecido", "negocia_taxa_ou_prazo", "demora_ou_resiste_a_enviar_documentos"]
    num_conv = ["msgs_pre_log", "dur_dias", "n_conversas", "frac_noite", "tam_medio", "urgencia", "consistencia_das_informacoes", "informacao_voluntaria", "v2_urgencia", "v2_consistencia_das_informacoes",
                "v2_informacao_voluntaria", "v2_anos_de_negocio_mencionados", "v2_faturamento_mensal_mencionado_reais", "v2_usa_maquininha", "v2_recebe_por_pix", "v2_cliente_indicado_por_terceiro",
                "v2_linguagem_formal", "v2_erros_de_escrita", "v2_tamanho_da_conversa", "v2_prob_atraso", "v2_menciona_divida_ou_negativacao", "v2_dificuldade_de_caixa", "v2_negocia_taxa_ou_prazo"]
    emb = ([], [f"emb_pc{i+1}" for i in range(20)])
    out = {"COH": (["gateway_1o_contrato"], ["valor_1o_contrato", "meses_desde_out25", "sem_perfil"]), "CAD": (cat_cad, []), "RFB": (cat_rfb, num_rfb),
           "CONV": ([c for c in cat_conv if c in df], [c for c in num_conv if c in df]), "EMB": (["emb_cluster"], emb[1])}
    return out


def montar_X(df, B, nomes_blocos):
    cats, nums = [], []
    for b in nomes_blocos:
        cats += B[b][0]
        nums += B[b][1]
    for c in cats:
        df[c] = df[c].astype(object).where(df[c].notna(), "sem_info").astype(str)
    for c in nums:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    X, nomes = mm.matriz(df, cats, nums)
    return X, nomes


# ----------------------------------------------------------------------------- algoritmos (X = matriz padronizada, S = cortes binarios)
sigm = mm.sigm


def cand_split(X, k=5):
    return mm.candidatos(X, k)


def fit_tree(S, y, idx, depth, rng, mf, minleaf=8):
    if depth == 0 or len(idx) < 2 * minleaf or y[idx].min() == y[idx].max():
        return ("leaf", (y[idx].sum() + 1) / (len(idx) + 2))
    sub = S[idx]
    cols = rng.choice(S.shape[1], size=min(mf, S.shape[1]), replace=False) if mf < S.shape[1] else np.arange(S.shape[1])
    sub = sub[:, cols]
    n = len(idx)
    nl = sub.sum(0)
    pl = sub.T @ y[idx]
    nr, pr = n - nl, y[idx].sum() - pl
    ok = (nl >= minleaf) & (nr >= minleaf)
    if not ok.any():
        return ("leaf", (y[idx].sum() + 1) / (n + 2))
    gl = 1 - (pl / np.maximum(nl, 1)) ** 2 - ((nl - pl) / np.maximum(nl, 1)) ** 2
    gr = 1 - (pr / np.maximum(nr, 1)) ** 2 - ((nr - pr) / np.maximum(nr, 1)) ** 2
    imp = np.where(ok, (nl * gl + nr * gr) / n, np.inf)
    j = cols[int(np.argmin(imp))]
    m = S[idx, j] > 0
    return ("split", j, fit_tree(S, y, idx[m], depth - 1, rng, mf, minleaf), fit_tree(S, y, idx[~m], depth - 1, rng, mf, minleaf))


def pred_tree(t, S):
    out = np.zeros(len(S))
    def rec(node, rows):
        if node[0] == "leaf":
            out[rows] = node[1]
            return
        m = S[rows, node[1]] > 0
        rec(node[2], rows[m])
        rec(node[3], rows[~m])
    rec(t, np.arange(len(S)))
    return out


def run_algo(algo, Xtr, Str, ytr, Xte, Ste, seed=0):
    rng = np.random.default_rng(seed)
    if algo == "logit_l2":
        return mm.pred_logit(mm.fit_logit(Xtr, ytr, 3.0), Xte)
    if algo == "logit_l1":
        n, p = Xtr.shape
        w = np.zeros(p + 1)
        A = np.column_stack([np.ones(n), Xtr])
        lam, step = 0.02 * n, 1.0 / (0.25 * np.linalg.norm(A, 2) ** 2 + 1e-9)
        for _ in range(150):
            g = A.T @ (sigm(A @ w) - ytr)
            w = w - step * g
            w[1:] = np.sign(w[1:]) * np.maximum(np.abs(w[1:]) - step * lam, 0)
        return np.column_stack([np.ones(len(Xte)), Xte]) @ w
    if algo == "naive_bayes":
        s = 0
        for c in (0, 1):
            Z = Xtr[ytr == c]
            mu, var = Z.mean(0), Z.var(0) + 1e-1
            ll = -0.5 * (((Xte - mu) ** 2) / var + np.log(var)).sum(1) + math.log(len(Z) / len(ytr))
            s = ll if c == 1 else s
            if c == 0:
                l0 = ll
        return s - l0
    if algo == "knn_recomendacao":
        a = Xtr - Xtr.mean(0)
        b = Xte - Xtr.mean(0)
        sim = (b @ a.T) / (np.linalg.norm(b, axis=1, keepdims=True) * np.linalg.norm(a, axis=1)[None] + 1e-9)
        k = 25
        top = np.argsort(-sim, axis=1)[:, :k]
        w = np.take_along_axis(sim, top, 1).clip(min=1e-3)
        return (w * ytr[top]).sum(1) / w.sum(1)
    if algo == "arvore":
        return pred_tree(fit_tree(Str, ytr, np.arange(len(ytr)), 3, rng, Str.shape[1]), Ste)
    if algo == "floresta":
        pr = np.zeros(len(Ste))
        mf = max(8, int(Str.shape[1] ** 0.5 * 2))
        for _ in range(40):
            b = rng.integers(0, len(ytr), len(ytr))
            pr += pred_tree(fit_tree(Str[b], ytr[b], np.arange(len(b)), 3, rng, mf), Ste)
        return pr / 40
    if algo == "boosting":
        return mm.pred_boost(mm.fit_boost(Xtr, ytr), Xte)
    if algo == "kmeans_taxa":
        a, C = kmeans(Xtr, 8, seed=seed + 1)
        taxa = np.array([(ytr[a == j].sum() + ytr.mean() * 5) / ((a == j).sum() + 5) for j in range(8)])
        d = ((Xte[:, None, :] - C[None]) ** 2).sum(-1)
        return taxa[d.argmin(1)]
    raise ValueError(algo)


def rank01(v):
    return pd.Series(v).rank().values / len(v)


ALGOS = ["logit_l2", "logit_l1", "naive_bayes", "knn_recomendacao", "arvore", "floresta", "boosting", "kmeans_taxa", "blend3"]


def predict(algo, Xtr, Str, ytr, Xte, Ste, seed=0):
    if algo == "blend3":
        return sum(rank01(run_algo(a, Xtr, Str, ytr, Xte, Ste, seed)) for a in ("logit_l2", "floresta", "boosting")) / 3
    return run_algo(algo, Xtr, Str, ytr, Xte, Ste, seed)


def cv_auc(X, S, y, algo, folds=5, rep=2, seed=SEED, estrato=None, permutar=False):
    rng = np.random.default_rng(seed)
    aucs, oof = [], np.zeros(len(y))
    for r in range(rep):
        yy = y.copy()
        if permutar:
            if estrato is not None:
                for e in np.unique(estrato):
                    ix = np.where(estrato == e)[0]
                    yy[ix] = rng.permutation(y[ix])
            else:
                yy = rng.permutation(y)
        perm = rng.permutation(len(yy))
        fold = np.zeros(len(yy), dtype=int)
        for g in (perm[yy[perm] == 1], perm[yy[perm] == 0]):
            fold[g] = np.arange(len(g)) % folds
        pred = np.zeros(len(yy))
        for f in range(folds):
            tr, te = fold != f, fold == f
            pred[te] = predict(algo, X[tr], S[tr], yy[tr], X[te], S[te], seed + f)
        aucs.append(ml.auc(yy, pred))
        oof += pred / rep
    return float(np.mean(aucs)), float(np.std(aucs)), oof


# ----------------------------------------------------------------------------- tarefas paralelas
def _alvo_rows(alvo):
    df = G["df"]
    if alvo == "y30":
        m = df["elegivel_y30"].values & df["y30"].notna().values
    elif alvo == "yatraso":
        m = np.ones(len(df), dtype=bool)
    else:
        m = df[alvo].notna().values
    return m


def tarefa_triagem(args):
    alvo, regime, bl, algo = args
    m = _alvo_rows(alvo)
    d = G["df"][m].reset_index(drop=True)
    nomes = (["COH"] if regime == "com_cohort" else []) + list(bl)
    if "EMB" in nomes or "CONV" in nomes:
        d = d[d["tem_conversa"]].reset_index(drop=True)
    y = d[{"yatraso": "yatraso", "y30": "y30"}.get(alvo, alvo)].values.astype(float)
    if y.sum() < 20 or (len(y) - y.sum()) < 20:
        return None
    X, _ = montar_X(d.copy(), G["B"], nomes)
    S, _ = cand_split(X)
    a, sd, _ = cv_auc(X, S, y, algo)
    return dict(alvo=alvo, regime=regime, blocos="+".join(nomes), algoritmo=algo, clientes=len(y), prevalencia=float(y.mean()), variaveis=X.shape[1], auc_cv=a, auc_desvio=sd)


def tarefa_validacao(args):
    alvo, regime, bl, algo = args
    m = _alvo_rows(alvo)
    d = G["df"][m].reset_index(drop=True)
    nomes = (["COH"] if regime == "com_cohort" else []) + list(bl)
    if "EMB" in nomes or "CONV" in nomes:
        d = d[d["tem_conversa"]].reset_index(drop=True)
    y = d[{"yatraso": "yatraso", "y30": "y30"}.get(alvo, alvo)].values.astype(float)
    X, _ = montar_X(d.copy(), G["B"], nomes)
    S, _ = cand_split(X)
    a, sd, _ = cv_auc(X, S, y, algo, rep=4, seed=SEED + 100)
    estr = d["estrato"].values
    nulo_simples = np.array([cv_auc(X, S, y, algo, rep=1, seed=1000 + i, permutar=True)[0] for i in range(24)])
    nulo_estrat = np.array([cv_auc(X, S, y, algo, rep=1, seed=2000 + i, permutar=True, estrato=estr)[0] for i in range(24)])
    # melhor modelo "so cohort" para medir o ganho de verdade
    Xc, _ = montar_X(d.copy(), G["B"], ["COH"])
    Sc, _ = cand_split(Xc)
    base = max(cv_auc(Xc, Sc, y, al, rep=4, seed=SEED + 100)[0] for al in ("logit_l2", "boosting", "floresta"))
    return dict(alvo=alvo, regime=regime, blocos="+".join(nomes), algoritmo=algo, clientes=len(y), prevalencia=float(y.mean()), auc_cv=a, auc_desvio=sd,
                acaso_simples_p95=float(np.quantile(nulo_simples, 0.95)), acaso_estratificado_p95=float(np.quantile(nulo_estrat, 0.95)),
                acaso_estratificado_medio=float(nulo_estrat.mean()), melhor_so_cohort=float(base), ganho_sobre_cohort=a - float(base),
                passa_acaso_estratificado=bool(a > np.quantile(nulo_estrat, 0.95)), passa_cohort=bool(a - base > 2 * sd))


def tarefa_selecao(alvo):
    m = _alvo_rows(alvo)
    d = G["df"][m].reset_index(drop=True)
    y = d[{"yatraso": "yatraso", "y30": "y30"}.get(alvo, alvo)].values.astype(float)
    grupos = {}
    for b, (cats, nums) in G["B"].items():
        if b in ("EMB",):
            continue
        for c in cats + nums:
            if c in d.columns and d[c].notna().sum() > 40 and (b != "CONV" or d["tem_conversa"].sum() > 0):
                grupos[c] = (b, c in cats)
    mats = {}
    for c, (b, is_cat) in grupos.items():
        dd = d.copy()
        try:
            Xc, nm = mm.matriz(dd, [c] if is_cat else [], [] if is_cat else [c])
            mats[c] = Xc
        except Exception:
            pass

    def auc_conj(vars_, idx=None, folds=3):
        X = np.column_stack([mats[v] for v in vars_]) if vars_ else np.zeros((len(y), 1))
        rows = np.arange(len(y)) if idx is None else idx
        rng = np.random.default_rng(5)
        perm = rng.permutation(rows)
        yy = y[perm]
        fold = np.zeros(len(perm), dtype=int)
        for g in (np.where(yy == 1)[0], np.where(yy == 0)[0]):
            fold[g] = np.arange(len(g)) % folds
        pred = np.zeros(len(perm))
        for f in range(folds):
            tr, te = fold != f, fold == f
            pred[te] = mm.pred_logit(mm.fit_logit(X[perm][tr], yy[tr], 3.0), X[perm][te])
        return ml.auc(yy, pred)

    def selecionar(idx, passos=8):
        escolhidas, melhor = [], 0.5
        hist = []
        for _ in range(passos):
            cand = [(auc_conj(escolhidas + [c], idx), c) for c in mats if c not in escolhidas]
            if not cand:
                break
            a, c = max(cand)
            if a <= melhor + 0.003:
                break
            escolhidas.append(c)
            melhor = a
            hist.append((c, a))
        return escolhidas, hist

    # procedimento aninhado: seleciona em treino, mede em teste
    rng = np.random.default_rng(9)
    perm = rng.permutation(len(y))
    fold = np.zeros(len(y), dtype=int)
    for g in (perm[y[perm] == 1], perm[y[perm] == 0]):
        fold[g] = np.arange(len(g)) % 5
    pred = np.zeros(len(y))
    for f in range(5):
        tr, te = np.where(fold != f)[0], np.where(fold == f)[0]
        esc, _ = selecionar(tr)
        X = np.column_stack([mats[v] for v in esc]) if esc else np.zeros((len(y), 1))
        pred[te] = mm.pred_logit(mm.fit_logit(X[tr], y[tr], 3.0), X[te])
    auc_honesto = ml.auc(y, pred)
    esc, hist = selecionar(np.arange(len(y)))
    return dict(alvo=alvo, auc_aninhado=float(auc_honesto), passos=[(c, float(a), grupos[c][0]) for c, a in hist])


def _wrap(f, a):
    try:
        return f(a)
    except Exception as e:
        return dict(erro=str(e)[:200], args=str(a))


def paralelo(fn, tarefas, workers=10):
    with ProcessPoolExecutor(workers) as ex:
        return list(ex.map(_wrap, [fn] * len(tarefas), tarefas, chunksize=1))


# ----------------------------------------------------------------------------- regras (conjuncoes ate 3 condicoes)
def indicadores(df, B, max_ind=90):
    cols, nomes = [], []
    for b in ("CAD", "RFB", "CONV"):
        cats, nums = B[b]
        for c in cats:
            if c not in df:
                continue
            s = df[c].astype(object).where(df[c].notna(), "sem_info").astype(str)
            for v, k in s.value_counts().items():
                if 25 <= k <= len(df) - 25 and v not in ("sem_info", "outros"):
                    cols.append((s == v).values)
                    nomes.append(f"{c} = {v}")
        for c in nums:
            if c not in df:
                continue
            x = pd.to_numeric(df[c], errors="coerce")
            if x.notna().sum() < 100 or x.nunique() < 4:
                continue
            for q, rot in ((0.75, ">= p75"), (0.25, "<= p25")):
                t = x.quantile(q)
                m = (x >= t) if q > 0.5 else (x <= t)
                m = m.fillna(False).values
                if 25 <= m.sum() <= len(df) - 25:
                    cols.append(m)
                    nomes.append(f"{c} {rot} ({t:.3g})")
    M = np.column_stack(cols)
    # mantem os indicadores de maior variancia em torno de 50% (mais informativos) ate o limite
    ordem = np.argsort(np.abs(M.mean(0) - 0.5))[:max_ind]
    return M[:, ordem], [nomes[i] for i in ordem]


def regras(df, B, alvo, perms=150, min_sup=25, top=25):
    m = _alvo_rows(alvo)
    d = df[m & df["tem_conversa"].values].reset_index(drop=True)
    y = d[{"yatraso": "yatraso", "y30": "y30"}.get(alvo, alvo)].values.astype(float)
    base = y.mean()
    I, nomes = indicadores(d, B)
    n, mi = I.shape
    I8 = I.astype(np.uint8)
    combos, idxs = [], []
    for i in range(mi):
        combos.append(I8[:, i:i + 1]); idxs.append((i,))
    pares = []
    for i in range(mi):
        for j in range(i + 1, mi):
            v = I8[:, i] & I8[:, j]
            if v.sum() >= min_sup:
                combos.append(v[:, None]); idxs.append((i, j)); pares.append((i, j, v))
    for i, j, v in pares:
        ks = np.arange(j + 1, mi)
        w = (I8[:, ks] & v[:, None])
        ok = w.sum(0) >= min_sup
        for k, col in zip(ks[ok], w[:, ok].T):
            combos.append(col[:, None]); idxs.append((i, j, int(k)))
    C = np.concatenate(combos, axis=1).astype(np.float32)
    sup = C.sum(0)
    pos = C.T @ y.astype(np.float32)
    z = (pos / sup - base) / np.sqrt(base * (1 - base) / sup)
    # permutacao ESTRATIFICADA por cohort: max |z| sob o acaso (limite de familia)
    estr = d["estrato"].values
    rng = np.random.default_rng(31)
    maxz = np.empty(perms)
    Yp = np.empty((n, perms), dtype=np.float32)
    for p in range(perms):
        yy = y.copy()
        for e in np.unique(estr):
            ix = np.where(estr == e)[0]
            yy[ix] = rng.permutation(y[ix])
        Yp[:, p] = yy
    POS = C.T @ Yp
    Z = (POS / sup[:, None] - base) / np.sqrt(base * (1 - base) / sup[:, None])
    maxz = np.abs(Z).max(0)
    p_fam = np.array([(np.sum(maxz >= abs(zz)) + 1) / (perms + 1) for zz in z])
    ordem = np.argsort(-np.abs(z))[:top]
    linhas = []
    for o in ordem:
        linhas.append(dict(alvo=alvo, regra=" E ".join(nomes[i] for i in idxs[o]), condicoes=len(idxs[o]), clientes=int(sup[o]), taxa=float(pos[o] / sup[o]), taxa_base=float(base),
                           lift=float(pos[o] / sup[o] / base), z=float(z[o]), p_familia=float(p_fam[o]), significativa=bool(p_fam[o] < 0.05), combinacoes_testadas=int(C.shape[1]),
                           z_critico_5pct=float(np.quantile(maxz, 0.95))))
    return pd.DataFrame(linhas)


# ----------------------------------------------------------------------------- embeddings
def analise_embeddings(df, perms=300):
    d = df[df["tem_conversa"]].reset_index(drop=True)
    P = d[[f"emb_pc{i+1}" for i in range(20)]].values
    linhas_cl, linhas_m = [], []
    estr = d["estrato"].values
    for alvo in ("yatraso", "q_paga_em_dia", "q_pagou_e_parou"):
        mm_ = d[alvo].notna().values
        dd = d[mm_].reset_index(drop=True)
        y = dd[alvo].values.astype(float)
        Pd = dd[[f"emb_pc{i+1}" for i in range(20)]].values
        e_ = dd["estrato"].values
        for k in (4, 6, 8, 12):
            a, C = kmeans(Pd[:, :10], k)
            tab = np.array([[np.sum((a == j) & (y == c)) for c in (0, 1)] for j in range(k)], dtype=float)
            chi, v = mq._cramers(tab)
            rng = np.random.default_rng(3)
            nulo = []
            for _ in range(perms):
                yy = y.copy()
                for e in np.unique(e_):
                    ix = np.where(e_ == e)[0]
                    yy[ix] = rng.permutation(y[ix])
                t2 = np.array([[np.sum((a == j) & (yy == c)) for c in (0, 1)] for j in range(k)], dtype=float)
                nulo.append(mq._cramers(t2)[1])
            p = (np.sum(np.array(nulo) >= v) + 1) / (perms + 1)
            linhas_cl.append(dict(alvo=alvo, k=k, cramers_v=float(v), acaso_medio=float(np.mean(nulo)), p_estratificado=float(p), maior_cluster=int(tab.sum(1).max()),
                                  taxa_min=float((tab[:, 1] / tab.sum(1)).min()), taxa_max=float((tab[:, 1] / tab.sum(1)).max())))
        for nome, X in [("pca5", Pd[:, :5]), ("pca10", Pd[:, :10]), ("pca20", Pd), ("embedding_768_pca", np.column_stack([Pd, dd[["v2_prob_atraso"]].fillna(dd["v2_prob_atraso"].median()).values]))]:
            Xs = (X - X.mean(0)) / (X.std(0) + 1e-9)
            S, _ = cand_split(Xs)
            for al in ("logit_l2", "knn_recomendacao"):
                a, sd, _ = cv_auc(Xs, S, y, al, rep=3)
                nul = [cv_auc(Xs, S, y, al, rep=1, seed=500 + i, permutar=True, estrato=e_)[0] for i in range(20)]
                linhas_m.append(dict(alvo=alvo, entrada=nome, algoritmo=al, auc_cv=a, auc_desvio=sd, acaso_estratificado_p95=float(np.quantile(nul, 0.95)), passa=bool(a > np.quantile(nul, 0.95)), clientes=len(y)))
        if "v2_prob_atraso" in dd:
            j = dd["v2_prob_atraso"].fillna(dd["v2_prob_atraso"].median()).values
            a = ml.auc(y, j)
            rng = np.random.default_rng(8)
            nul = []
            for _ in range(300):
                yy = y.copy()
                for e in np.unique(e_):
                    ix = np.where(e_ == e)[0]
                    yy[ix] = rng.permutation(y[ix])
                nul.append(ml.auc(yy, j))
            linhas_m.append(dict(alvo=alvo, entrada="estimativa_do_LLM_(prob_atraso)", algoritmo="zero_shot", auc_cv=float(a), auc_desvio=0.0, acaso_estratificado_p95=float(np.quantile(nul, 0.95)), passa=bool(a > np.quantile(nul, 0.95)), clientes=len(y)))
    return pd.DataFrame(linhas_cl), pd.DataFrame(linhas_m)


# ----------------------------------------------------------------------------- orquestracao
def rodar(ref=None, workers=10, rapido=False):
    t0 = time.time()
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    df = montar_dados(ref)
    G["df"], G["B"] = df, blocos(df)
    run_id = uuid.uuid4().hex[:10]
    print(f"dados: {df.shape}, com conversa {int(df['tem_conversa'].sum())}", flush=True)
    sub = [("CAD",), ("RFB",), ("CONV",), ("EMB",), ("CAD", "RFB"), ("CAD", "CONV"), ("CAD", "EMB"), ("RFB", "CONV"), ("RFB", "EMB"), ("CONV", "EMB"),
           ("CAD", "RFB", "CONV"), ("CAD", "RFB", "EMB"), ("CAD", "CONV", "EMB"), ("RFB", "CONV", "EMB"), ("CAD", "RFB", "CONV", "EMB")]
    algos = ALGOS[:4] if rapido else ALGOS
    tri = [(a, r, b, al) for a in ALVOS for r in ("com_cohort", "sem_cohort") for b in sub for al in algos]
    tri += [(a, "com_cohort", (), al) for a in ALVOS for al in algos]  # so cohort
    print("triagem:", len(tri), "configs", flush=True)
    R = [r for r in paralelo(tarefa_triagem, tri, workers) if r and "erro" not in r]
    T = pd.DataFrame(R)
    T["run_id"], T["data_referencia"] = run_id, ref
    print(f"triagem pronta: {len(T)} configs em {time.time()-t0:.0f}s", flush=True)
    # validacao dura: top 6 por alvo (sem repetir o so-cohort) + o melhor so-cohort
    val = []
    for a, g in T.groupby("alvo"):
        top = g[g["blocos"] != "COH"].nlargest(6, "auc_cv")
        for _, r in top.iterrows():
            val.append((a, "com_cohort" if r["blocos"].startswith("COH") else "sem_cohort", tuple(x for x in r["blocos"].split("+") if x != "COH"), r["algoritmo"]))
    V = pd.DataFrame([r for r in paralelo(tarefa_validacao, val, workers) if r and "erro" not in r])
    V["run_id"], V["data_referencia"] = run_id, ref
    print(f"validacao pronta em {time.time()-t0:.0f}s", flush=True)
    S = [r for r in paralelo(tarefa_selecao, ["yatraso", "y30", "q_paga_em_dia"], 3) if r and "erro" not in r]
    SEL = pd.DataFrame([dict(alvo=s["alvo"], auc_aninhado=s["auc_aninhado"], passo=i + 1, variavel=c, bloco=b, auc_interno=a) for s in S for i, (c, a, b) in enumerate(s["passos"])]
                       + [dict(alvo=s["alvo"], auc_aninhado=s["auc_aninhado"], passo=0, variavel="(procedimento completo)", bloco="", auc_interno=np.nan) for s in S])
    SEL["run_id"], SEL["data_referencia"] = run_id, ref
    print(f"selecao pronta em {time.time()-t0:.0f}s", flush=True)
    RG = pd.concat([regras(df, G["B"], a) for a in ("yatraso", "y30", "q_paga_em_dia")], ignore_index=True)
    RG["run_id"], RG["data_referencia"] = run_id, ref
    print(f"regras prontas em {time.time()-t0:.0f}s", flush=True)
    CL, EM = analise_embeddings(df)
    for t in (CL, EM):
        t["run_id"], t["data_referencia"] = run_id, ref
    print(f"embeddings prontos em {time.time()-t0:.0f}s", flush=True)
    for nome, tab, desc in [("estudo_triagem", T, "Estudo massivo. AUC (CV 5x2) de cada combinacao alvo x regime x blocos de variaveis x algoritmo."),
                            ("estudo_validacao", V, "Estudo massivo. Os melhores da triagem validados contra o acaso simples e o acaso ESTRATIFICADO por cohort, e contra o melhor modelo so com cohort."),
                            ("estudo_selecao_recursiva", SEL, "Estudo massivo. Selecao recursiva (forward) de variaveis, com AUC honesto do procedimento aninhado."),
                            ("estudo_regras", RG, "Estudo massivo. Conjuncoes de ate 3 condicoes mais discrepantes, com p ajustado para a familia inteira sob permutacao estratificada por cohort."),
                            ("estudo_embeddings_clusters", CL, "Estudo massivo. Clusters da conversa (embedding Qwen) x desfecho, com teste de permutacao estratificado por cohort."),
                            ("estudo_embeddings_modelos", EM, "Estudo massivo. Modelos sobre o embedding da conversa e a estimativa propria do LLM, contra o acaso estratificado.")]:
        mm._gravar(ch, nome, tab, desc, True)
    print("FIM", run_id, f"{time.time()-t0:.0f}s", flush=True)
    return T, V, SEL, RG, CL, EM


if __name__ == "__main__":
    rodar(rapido=bool(os.environ.get("RAPIDO")))
