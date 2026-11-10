# Embedding neural (encoder multilingue pequeno, ONNX em CPU via fastembed, sem LLM gerador) do texto da conversa do cliente e dos relatorios
# de bureau escritos pela IA, ate 1 dia depois do inicio do contrato. Testa: PCA dos vetores x calote (univariado com FDR), clusters e modelo
# direto sobre o vetor (regressao logistica regularizada, validacao temporal). Uso: python credit_embed.py
import re
import warnings

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import credit_experimentos as ex
import credit_features as cf
import credit_scan as sc

warnings.filterwarnings("ignore")
MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def textos(ch, base):
    pon = ch.query_df("select cliente_id, contato_id from analytics.ponte_lead_cliente where vinculo_valido = 1")
    conv = ch.query_df("select id as conversa_id, contact_id as contato_id from trusted_chatwoot.conversations final")
    c = base[["contrato_id", "cliente_id", "data_inicio"]].merge(pon, on="cliente_id").merge(conv, on="contato_id")
    c["cutoff"] = pd.to_datetime(c["data_inicio"]) + pd.Timedelta(days=1)
    ids = ",".join(str(int(x)) for x in c["conversa_id"].unique())
    msgs = ch.query_df(f"select conversation_id as conversa_id, created_at as quando, content from trusted_chatwoot.messages final where conversation_id in ({ids}) and message_type = 0 and private = 0 and content is not null and length(content) > 2")
    notas = ch.query_df(f"select conversation_id as conversa_id, created_at as quando, content from trusted_chatwoot.kanban_notes final where conversation_id in ({ids})")

    def junta(fonte, lim):
        m = c[["contrato_id", "conversa_id", "cutoff"]].merge(fonte, on="conversa_id")
        m = m[m["quando"] <= m["cutoff"]].sort_values("quando")
        return m.groupby("contrato_id")["content"].apply(lambda s: re.sub(r"\s+", " ", " ".join(s.astype(str)))[:lim])

    # notas: so a parte descritiva (justificativa/analise), sem numeros de documento
    tn = junta(notas, 3000)
    return junta(msgs, 1500), tn


def embed(textos_lista, lote=32):
    from fastembed import TextEmbedding

    m = TextEmbedding(MODELO, threads=3)
    E = np.array(list(m.embed(textos_lista, batch_size=lote)))
    return E / np.linalg.norm(E, axis=1, keepdims=True)


def main():
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan4.pkl")
    tm, tn = textos(ch, df)
    print(f"contratos {len(df)} | com mensagens {len(tm)} | com notas {len(tn)}", flush=True)
    res = {}
    for nome, serie in (("msgs", tm), ("notas", tn)):
        serie = serie[serie.str.len() > 20]
        E = embed(serie.tolist())
        emb = pd.DataFrame(E, index=serie.index)
        res[nome] = emb
        print(f"[{nome}] vetores {E.shape}", flush=True)
        pcs = PCA(n_components=10, random_state=7).fit_transform(E)
        for i in range(10):
            df[f"emb_{nome}_pc{i + 1}"] = df["contrato_id"].map(pd.Series(pcs[:, i], index=serie.index))
        km = KMeans(n_clusters=8, n_init=10, random_state=7).fit_predict(E)
        df[f"emb_{nome}_cluster"] = df["contrato_id"].map(pd.Series(km, index=serie.index)).astype("object").map(lambda x: None if x != x else f"c{int(x)}")
    cols_num = [c for c in df.columns if c.startswith("emb_") and not c.endswith("cluster")]
    cols_cat = [c for c in df.columns if c.endswith("_cluster")]
    todos = []
    for alvo in ("ye_0", "y_calote_30", "y_calote_15"):
        r = sc.testes(df, alvo, cols_num, cols_cat, min_nivel=10)
        todos.append(r)
        print(f"\n== {alvo}: {len(r)} testes de embedding; q<0.10: {int((r['q_fdr'] < 0.10).sum())}; menores p:", [(x.variavel, x.nivel[:8], round(x.p, 3), x.n) for x in r.sort_values("p").head(6).itertuples()], flush=True)
    # modelo direto sobre o vetor (384 dim), validacao temporal
    print("\n== regressao logistica sobre o vetor inteiro (temporal, 5 meses) ==", flush=True)
    for nome, emb in res.items():
        d = df.set_index("contrato_id").join(emb.add_prefix("v"), how="inner")
        d = d.reset_index()
        vcols = [c for c in d.columns if re.fullmatch(r"v\d+", str(c))]
        for alvo in ("ye_0", "y_calote_30"):
            for C in (0.01, 0.1):
                saidas = []
                H = 45 if alvo == "ye_0" else 30
                dd = d[d[alvo].notna()].copy()
                dd["mes"] = dd["data_inicio"].dt.to_period("M")
                gap = pd.Timedelta(days=H + cf.CURA_DIAS)
                for mes in sorted(dd["mes"].unique())[-5:]:
                    ini = mes.start_time
                    te, tr = dd[dd["mes"] == mes], dd[dd["data_inicio"] <= ini - gap]
                    if len(te) < 20 or len(tr) < 80 or tr[alvo].nunique() < 2:
                        continue
                    sc_ = StandardScaler().fit(tr[vcols])
                    m = LogisticRegression(C=C, max_iter=2000).fit(sc_.transform(tr[vcols]), tr[alvo].astype(int))
                    saidas.append(pd.DataFrame({"y": te[alvo].astype(int).values, "p": m.predict_proba(sc_.transform(te[vcols]))[:, 1]}))
                if saidas:
                    mt = ex.metricas(pd.concat(saidas))
                    print(f"  {nome:5s} | {alvo:11s} | C={C:<5} | n={mt['n']} AUC={mt['auc']} [{mt['auc_ic_lo']}-{mt['auc_ic_hi']}] PR={mt['pr_auc']}", flush=True)
    pd.concat(todos).to_pickle("/tmp/motor_scan_embed.pkl")
    df.to_pickle("/tmp/base_scan5.pkl")


if __name__ == "__main__":
    main()
