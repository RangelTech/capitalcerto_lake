# Experimento de ML da Capital Certo: sinais de inadimplencia. Separado das DAGs de producao.
# So numpy e pandas (a imagem do Airflow nao tem scikit-learn e nao vamos mexer nela por um experimento).
#
# Parte A, perfil x inadimplencia, com fator normalizador:
#   - taxa bruta com intervalo de Wilson 95%;
#   - indice (lift) = taxa do grupo / taxa geral;
#   - taxa suavizada: puxa grupos pequenos para a taxa geral (empirical Bayes simples, K = 20);
#   - odds ratio AJUSTADO por regressao logistica, controlando por valor emprestado, tempo desde o primeiro
#     contrato, quantidade de contratos e se tem contrato no gateway antigo (Asaas). E a medida que tira o efeito
#     de "cliente antigo" e de "cliente grande" do perfil.
# Parte B, padrao das conversas: modelo de texto (TF-IDF + regressao logistica) com SO as mensagens do cliente
#   ANTERIORES ao contrato, validado por validacao cruzada e comparado com uma distribuicao nula (rotulos embaralhados).
# Nada disso e para decidir credito. Raca/cor nao existe em nenhuma fonte e nao e inferida.
import math
import os
import re
import unicodedata
from collections import Counter

import numpy as np
import pandas as pd

DB_ML = "analytics_ml"
K_SUAVIZACAO = 20
SEED = 7


def _ch():
    import clickhouse_connect

    return clickhouse_connect.get_client(host=os.environ["CLICKHOUSE_HOST"], password=os.environ["CLICKHOUSE_PASSWORD"])


# ----------------------------------------------------------------------------- estatistica basica
def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def logistica(X, y, l2=1e-3, iters=40, penalizar_intercepto=False):
    """Regressao logistica por Newton-Raphson com L2 leve. Devolve (beta, cov). X ja inclui a coluna do intercepto na posicao 0."""
    n, p = X.shape
    beta = np.zeros(p)
    pen = np.full(p, l2)
    if not penalizar_intercepto:
        pen[0] = 0.0
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 30)
        mu = 1 / (1 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-6, None)
        g = X.T @ (y - mu) - pen * beta
        H = (X * w[:, None]).T @ X + np.diag(pen)
        passo = np.linalg.solve(H, g)
        beta = beta + passo
        if np.max(np.abs(passo)) < 1e-7:
            break
    eta = np.clip(X @ beta, -30, 30)
    mu = 1 / (1 + np.exp(-eta))
    w = np.clip(mu * (1 - mu), 1e-6, None)
    H = (X * w[:, None]).T @ X + np.diag(pen)
    cov = np.linalg.inv(H)
    return beta, cov


def auc(y, s):
    y = np.asarray(y)
    s = np.asarray(s)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return np.nan
    ranks = pd.Series(s).rank().values
    return (ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


# ----------------------------------------------------------------------------- parte A
SQL_PERFIL = """
SELECT c.pessoa_id AS pessoa_id, any(c.sexo) AS sexo, any(c.faixa_etaria) AS faixa_etaria, any(if(c.uf IN ('', 'NI'), 'sem_info', c.uf)) AS uf,
       any(c.cidade) AS cidade, any(if(c.segmento = '', 'sem_info', c.segmento)) AS segmento, any(c.tipo_pessoa) AS tipo_pessoa,
       sum(c.qtd_contratos) AS qtd_contratos, toFloat64(sum(ifNull(c.total_emprestado, 0))) AS total_emprestado,
       dateDiff('day', min(c.primeiro_contrato_em), toDate('{ref}')) AS dias_desde_primeiro, max(c.contratos_em_atraso > 0) AS inadimplente,
       max(a.tem_asaas) AS tem_asaas
FROM analytics.cliente_360 c
LEFT JOIN (SELECT cliente_id, max(provedor_pagamento = 'ASAAS') AS tem_asaas FROM analytics.dim_contrato WHERE NOT excluido GROUP BY cliente_id) a ON a.cliente_id = c.cliente_id
WHERE c.empresa = 'Capital Certo' AND c.qtd_contratos > 0
GROUP BY c.pessoa_id
"""


def _norm_cidade(x):
    x = unicodedata.normalize("NFD", str(x).lower())
    x = re.sub(r"[^a-z ]", "", x).strip()
    return "sem_info" if x in ("", "nao informado") else x.title()


def parte_perfil(ref):
    ch = _ch()
    df = ch.query_df(SQL_PERFIL.replace("{ref}", ref))
    df["cidade"] = df["cidade"].map(_norm_cidade)
    df["sexo"] = df["sexo"].replace({"": "sem_info", "MASCULINO": "masculino", "FEMININO": "feminino"})
    y = df["inadimplente"].astype(int).values
    base = y.mean()
    # cidades pequenas viram "Outras" (menos de 15 clientes): evita ruido e reidentificacao
    cont = df["cidade"].value_counts()
    df["cidade"] = df["cidade"].where(df["cidade"].map(cont) >= 15, "Outras cidades")
    contu = df["uf"].value_counts()
    df["uf"] = df["uf"].where(df["uf"].map(contu) >= 15, "Outros estados")
    conts = df["segmento"].value_counts()
    df["segmento"] = df["segmento"].where(df["segmento"].map(conts) >= 15, "outros")
    attrs = ["sexo", "faixa_etaria", "uf", "cidade", "segmento", "tipo_pessoa"]
    # design: intercepto, controles padronizados, dummies (referencia = nivel mais frequente de cada atributo)
    cols = [np.ones(len(df))]
    nomes = ["intercepto"]
    for nome, vals in [("log_valor_emprestado", np.log1p(df["total_emprestado"].values)), ("log_dias_desde_primeiro_contrato", np.log1p(df["dias_desde_primeiro"].clip(lower=0).values)),
                       ("qtd_contratos", df["qtd_contratos"].values.astype(float)), ("tem_contrato_asaas", df["tem_asaas"].fillna(0).values.astype(float))]:
        v = vals.astype(float)
        sd = v.std() or 1.0
        cols.append((v - v.mean()) / sd)
        nomes.append(nome)
    # os ~200 clientes sem cadastro de perfil sao os mesmos em sexo, UF, segmento e idade (cohort antigo): um unico controle,
    # senao as colunas "sem_info" ficam colineares e os odds ratios explodem
    SEM = ("sem_info", "sem_data")
    sem_perfil = ((df["sexo"] == "sem_info") | (df["segmento"] == "sem_info")).astype(float).values
    cols.append(sem_perfil)
    nomes.append("cadastro_sem_perfil")
    refs, idx = {}, {}
    for a in attrs:
        cand = df.loc[~df[a].isin(SEM), a]
        ref_nivel = cand.value_counts().index[0]
        refs[a] = ref_nivel
        for nivel in sorted(df[a].unique()):
            if nivel == ref_nivel or nivel in SEM:
                continue
            cols.append((df[a] == nivel).astype(float).values)
            idx[(a, nivel)] = len(nomes)
            nomes.append(f"{a}={nivel}")
    X = np.column_stack(cols)
    beta, cov = logistica(X, y.astype(float), l2=1e-2)
    se = np.sqrt(np.diag(cov))
    linhas = []
    for a in attrs:
        for nivel, g in df.groupby(a):
            n, k = len(g), int(g["inadimplente"].sum())
            lo, hi = wilson(k, n)
            taxa = k / n
            suav = (k + K_SUAVIZACAO * base) / (n + K_SUAVIZACAO)
            ref = nivel == refs[a]
            if nivel in SEM:
                orv, orl, orh, sig = np.nan, np.nan, np.nan, False
            elif ref:
                orv, orl, orh, sig = 1.0, 1.0, 1.0, False
            else:
                j = idx[(a, nivel)]
                orv, orl, orh = math.exp(beta[j]), math.exp(beta[j] - 1.96 * se[j]), math.exp(beta[j] + 1.96 * se[j])
                sig = bool(orl > 1 or orh < 1)
            linhas.append(dict(atributo=a, nivel=str(nivel), clientes=n, inadimplentes=k, taxa=taxa, taxa_ic_baixo=lo, taxa_ic_alto=hi,
                               indice_bruto=taxa / base, taxa_suavizada=suav, indice_suavizado=suav / base, odds_ratio_ajustado=orv,
                               or_ic_baixo=orl, or_ic_alto=orh, significativo=sig, e_referencia=ref, grupo_pequeno=n < 30))
    out = pd.DataFrame(linhas)
    out["taxa_geral"] = base
    ctrl = pd.DataFrame({"controle": nomes[1:6], "odds_ratio_por_desvio_padrao": [math.exp(b) for b in beta[1:6]],
                         "or_ic_baixo": [math.exp(beta[i] - 1.96 * se[i]) for i in range(1, 6)], "or_ic_alto": [math.exp(beta[i] + 1.96 * se[i]) for i in range(1, 6)]})
    return out, ctrl, dict(clientes=len(df), inadimplentes=int(y.sum()), taxa_geral=float(base))


# ----------------------------------------------------------------------------- parte B
STOP = set("""de da do das dos a o as os e em um uma uns umas para por com sem que se na no nas nos ao aos ou mas ja nao sim eu voce vc
meu minha meus minhas seu sua seus suas esta esse essa isso este isto estou esta sao foi ser ter tem tenho vai vou pra pro la aqui bom boa dia
tarde noite ola oi obrigado obrigada ok certo tudo como mais muito mim te me lhe ele ela eles elas quando onde qual quais so tambem entao
at ate mesmo pode posso quero queria gostaria favor por""".split())

SQL_MSGS = """
SELECT cl.pessoa_id AS pessoa_id, m.content AS content, m.created_at AS criado_em
FROM analytics.cliente_360 cl
JOIN trusted_chatwoot.conversations c FINAL ON c.contact_id = cl.contato_chatwoot_principal_id
JOIN trusted_chatwoot.messages m FINAL ON m.conversation_id = c.id
WHERE m.message_type = 0 AND ifNull(m.content, '') != '' AND NOT ifNull(m.private, false)
  AND m.created_at < cl.primeiro_contrato_em + INTERVAL 1 DAY
  AND cl.qtd_contratos > 0 AND cl.tem_vinculo_chatwoot AND cl.empresa = 'Capital Certo'
"""


def _tokens(txt, nomes):
    t = unicodedata.normalize("NFD", txt.lower())
    t = re.sub(r"[̀-ͯ]", "", t)
    t = re.sub(r"https?://\S+", " ", t)
    palavras = [w for w in re.findall(r"[a-z]{3,}", t) if w not in STOP and w not in nomes]
    return palavras + [f"{a}_{b}" for a, b in zip(palavras, palavras[1:])]


def _tfidf(docs, min_df=15, max_feat=800):
    df_c = Counter()
    for d in docs:
        df_c.update(set(d))
    vocab = [w for w, c in df_c.most_common() if c >= min_df][:max_feat]
    ix = {w: i for i, w in enumerate(vocab)}
    n = len(docs)
    idf = np.array([math.log((1 + n) / (1 + df_c[w])) + 1 for w in vocab])
    M = np.zeros((n, len(vocab)))
    for i, d in enumerate(docs):
        for w, c in Counter(d).items():
            if w in ix:
                M[i, ix[w]] = (1 + math.log(c)) * idf[ix[w]]
    nr = np.linalg.norm(M, axis=1, keepdims=True)
    nr[nr == 0] = 1
    return M / nr, vocab, df_c


def _cv_auc(X, y, folds=5, rep=3, l2=3.0, seed=SEED):
    rng = np.random.default_rng(seed)
    oof = np.zeros(len(y))
    aucs = []
    for r in range(rep):
        perm = rng.permutation(len(y))
        ys = y[perm]
        pos, neg = perm[ys == 1], perm[ys == 0]
        fold_id = np.zeros(len(y), dtype=int)
        for grupo in (pos, neg):
            for i, ind in enumerate(grupo):
                fold_id[ind] = i % folds
        pred = np.zeros(len(y))
        for f in range(folds):
            tr, te = fold_id != f, fold_id == f
            beta, _ = logistica(np.column_stack([np.ones(tr.sum()), X[tr]]), y[tr], l2=l2, iters=25)
            pred[te] = np.column_stack([np.ones(te.sum()), X[te]]) @ beta
        aucs.append(auc(y, pred))
        oof += pred / rep
    return float(np.mean(aucs)), float(np.std(aucs)), oof


def parte_conversas(ref):
    ch = _ch()
    # primeiros nomes dos clientes: saem do vocabulario para o modelo nao aprender "nome proprio"
    nomes = set()
    for (n,) in ch.query("SELECT DISTINCT ifNull(name, '') FROM trusted_credhub.customer").result_rows:
        for w in re.findall(r"[a-z]{3,}", unicodedata.normalize("NFD", n.lower()).encode("ascii", "ignore").decode()):
            nomes.add(w)
    rows = ch.query(SQL_MSGS).result_rows
    por = {}
    for pid, content, ts in rows:
        por.setdefault(pid, []).append((ts, content))
    base_df = ch.query_df(SQL_PERFIL.replace("{ref}", ref))[["pessoa_id", "inadimplente", "tem_asaas"]].set_index("pessoa_id")
    alvo = base_df["inadimplente"].astype(int).to_dict()
    asaas = base_df["tem_asaas"].fillna(0).astype(int).to_dict()
    ids = [p for p in por if p in alvo and len(por[p]) >= 3]
    docs = [_tokens(" ".join(c for _, c in sorted(por[p])), nomes) for p in ids]
    y = np.array([alvo[p] for p in ids], dtype=float)
    sem_asaas = np.array([asaas[p] == 0 for p in ids])
    Xt, vocab, dfc = _tfidf(docs)
    num = np.array([[math.log1p(len(por[p])), math.log1p(np.mean([len(c) for _, c in por[p]])),
                     np.mean([(ts.hour - 3) % 24 < 6 for ts, _ in por[p]])] for p in ids])
    num = (num - num.mean(0)) / (num.std(0) + 1e-9)
    n = len(y)

    def avaliar(mask, nome_spec):
        ym, Xm, nm = y[mask], np.column_stack([Xt[mask], num[mask]]), num[mask]
        a_vol, _, _ = _cv_auc(nm, ym)
        a_txt, _, _ = _cv_auc(Xt[mask], ym)
        a_all, sd, oof_m = _cv_auc(Xm, ym)
        rng = np.random.default_rng(SEED + 1)
        nulo = np.array([_cv_auc(Xm, rng.permutation(ym), rep=1, seed=int(rng.integers(1e6)))[0] for _ in range(25)])
        return dict(especificacao=nome_spec, clientes=int(mask.sum()), mensagens=int(sum(len(por[p]) for p, k in zip(ids, mask) if k)),
                    prevalencia=float(ym.mean()), termos_no_vocabulario=len(vocab), auc_so_volume=a_vol, auc_so_texto=a_txt, auc_texto_e_volume=a_all,
                    auc_desvio=sd, auc_nulo_media=float(nulo.mean()), auc_nulo_p95=float(np.quantile(nulo, 0.95)),
                    p_valor_permutacao=float((np.sum(nulo >= a_all) + 1) / (len(nulo) + 1))), oof_m

    spec_todos, oof = avaliar(np.ones(n, dtype=bool), "todos os clientes")
    spec_sem, _ = avaliar(sem_asaas, "sem clientes do gateway antigo (Asaas)")
    metr = [spec_todos, spec_sem]
    # termos: ajuste final e taxa observada com e sem o termo
    beta, _ = logistica(np.column_stack([np.ones(n), Xt, num]), y, l2=3.0, iters=25)
    coefs = beta[1:1 + len(vocab)]
    termos = []
    for j, w in enumerate(vocab):
        tem = Xt[:, j] > 0
        if tem.sum() < 15 or (~tem).sum() < 15:
            continue
        termos.append(dict(termo=w.replace("_", " "), coeficiente=float(coefs[j]), clientes_com_termo=int(tem.sum()),
                           taxa_inad_com_termo=float(y[tem].mean()), taxa_inad_sem_termo=float(y[~tem].mean())))
    termos = pd.DataFrame(termos)
    termos["direcao"] = np.where(termos["coeficiente"] > 0, "mais inadimplencia", "menos inadimplencia")
    termos = pd.concat([termos.nlargest(20, "coeficiente"), termos.nsmallest(20, "coeficiente")])
    # score fora da amostra por cliente e decis
    dec = pd.qcut(pd.Series(oof).rank(method="first"), 10, labels=False) + 1
    score = pd.DataFrame({"pessoa_id": ids, "score_oof": oof, "decil": dec.values, "inadimplente": y.astype(int)})
    return metr, termos, score


# ----------------------------------------------------------------------------- gravacao
def _salvar(ch, nome, df, comentario, colunas):
    ch.command(f"CREATE DATABASE IF NOT EXISTS {DB_ML}")
    ch.command(f"DROP TABLE IF EXISTS {DB_ML}.{nome}")
    defs = []
    for c in df.columns:
        t = "String"
        if pd.api.types.is_bool_dtype(df[c]):
            t = "Bool"
        elif pd.api.types.is_integer_dtype(df[c]):
            t = "Int64"
        elif pd.api.types.is_float_dtype(df[c]):
            t = "Nullable(Float64)"
        defs.append(f"`{c}` {t} COMMENT '{colunas.get(c, '').replace(chr(39), ' ')}'")
    ch.command(f"CREATE TABLE {DB_ML}.{nome} ({', '.join(defs)}) ENGINE = MergeTree ORDER BY tuple() COMMENT '{comentario.replace(chr(39), ' ')}'")
    dados = df.astype(object).where(pd.notnull(df), None)
    ch.insert(f"{DB_ML}.{nome}", dados.values.tolist(), column_names=list(df.columns))


def rodar(ref=None):
    ch = _ch()
    if not ref:
        ref = ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    perfil, ctrl, info = parte_perfil(ref)
    perfil["data_referencia"] = ref
    _salvar(ch, "perfil_inadimplencia", perfil,
            "Experimento. Perfil do cliente x inadimplencia (cliente com algum contrato em atraso), por nivel de cada atributo, com fator normalizador: indice sobre a taxa geral, taxa suavizada e odds ratio ajustado por valor, tempo de casa, quantidade de contratos e gateway antigo. Descritivo, nao serve para decidir credito.",
            dict(atributo="atributo analisado", nivel="valor do atributo", clientes="clientes com contrato no grupo (CPF/CNPJ unico)", inadimplentes="clientes do grupo com contrato em atraso",
                 taxa="inadimplentes / clientes", taxa_ic_baixo="limite inferior do intervalo de Wilson 95%", taxa_ic_alto="limite superior do intervalo de Wilson 95%",
                 indice_bruto="taxa do grupo / taxa geral", taxa_suavizada="taxa puxada para a geral em grupos pequenos: (k + 20 x taxa geral) / (n + 20)",
                 indice_suavizado="taxa suavizada / taxa geral", odds_ratio_ajustado="odds de inadimplir frente ao nivel de referencia do atributo, controlando valor, tempo de casa, qtd de contratos e Asaas e os demais atributos",
                 or_ic_baixo="limite inferior 95% do odds ratio", or_ic_alto="limite superior 95% do odds ratio", significativo="o intervalo do odds ratio nao inclui 1",
                 e_referencia="nivel usado como base de comparacao (o mais frequente)", grupo_pequeno="menos de 30 clientes: ler com cautela", taxa_geral="taxa geral de inadimplencia dos clientes com contrato",
                 data_referencia="data de corte do CRM"))
    ctrl["data_referencia"] = ref
    _salvar(ch, "perfil_controles", ctrl, "Experimento. Efeito das variaveis de controle (por desvio padrao) na regressao do perfil.",
            dict(controle="variavel de controle", odds_ratio_por_desvio_padrao="odds ratio por aumento de 1 desvio padrao", or_ic_baixo="limite inferior 95%", or_ic_alto="limite superior 95%", data_referencia="data de corte do CRM"))
    metr, termos, score = parte_conversas(ref)
    m = pd.DataFrame(metr)
    m["data_referencia"] = ref
    _salvar(ch, "conversas_modelo", m, "Experimento. Resultado do modelo de texto das conversas anteriores ao contrato (validacao cruzada e teste de permutacao).",
            dict(especificacao="amostra usada: todos os clientes ou sem o gateway antigo", clientes="clientes no modelo", mensagens="mensagens do cliente usadas (so anteriores ao primeiro contrato)", prevalencia="taxa de inadimplentes na amostra",
                 termos_no_vocabulario="palavras e pares de palavras considerados", auc_so_volume="AUC com so volume e horario das mensagens", auc_so_texto="AUC com so o texto",
                 auc_texto_e_volume="AUC do modelo final (texto + volume + horario), media de 5 folds x 3 repeticoes", auc_desvio="desvio padrao do AUC entre repeticoes",
                 auc_nulo_media="AUC medio com rotulos embaralhados (deveria ser ~0,5)", auc_nulo_p95="percentil 95 do AUC com rotulos embaralhados", p_valor_permutacao="chance de um AUC tao alto sair por sorte", data_referencia="data de corte do CRM"))
    termos["data_referencia"] = ref
    _salvar(ch, "conversas_termos", termos, "Experimento. Palavras e pares de palavras mais associados a inadimplencia (coeficiente do modelo). Nomes de pessoas e numeros sao removidos; so termos que aparecem em 15 clientes ou mais.",
            dict(termo="palavra ou par de palavras", coeficiente="peso no modelo (positivo = mais inadimplencia)", clientes_com_termo="clientes que usaram o termo", taxa_inad_com_termo="taxa de inadimplencia de quem usou",
                 taxa_inad_sem_termo="taxa de inadimplencia de quem nao usou", direcao="sentido da associacao", data_referencia="data de corte do CRM"))
    score["data_referencia"] = ref
    _salvar(ch, "conversas_score_decil", score[["pessoa_id", "decil", "inadimplente", "data_referencia"]], "Experimento. Decil do score fora da amostra do modelo de conversas por cliente (1 = menor risco, 10 = maior).",
            dict(pessoa_id="hash do CPF/CNPJ", decil="decil do score (10 = maior risco previsto)", inadimplente="1 se o cliente tem contrato em atraso", data_referencia="data de corte do CRM"))
    print("perfil:", info)
    for mm in metr:
        print("conversas:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in mm.items()})
    return metr


if __name__ == "__main__":
    rodar()
