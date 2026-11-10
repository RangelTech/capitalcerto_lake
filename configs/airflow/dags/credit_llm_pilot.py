# Piloto: LLM pequeno (Qwen3 1.7B Q8, llama.cpp em CPU) como EXTRATOR de sinais do texto da conversa do cliente. Nao gera texto livre: para cada
# contrato faz 6 perguntas sim/nao e le a probabilidade de "Sim" no primeiro token (logprob). O prefixo (texto do cliente) e cacheado, entao
# as 6 perguntas custam pouco depois do prefill. Mede quanto tempo leva e se algum sinal separa calote (univariado e AUC). Lento de proposito:
# serve para decidir se vale rodar no historico inteiro de madrugada.
# Uso: python credit_llm_pilot.py [n_contratos] [threads]
import math
import re
import sys
import time

import numpy as np
import pandas as pd
from llama_cpp import Llama, LlamaRAMCache
from scipy import stats

import credit_embed as ce
import credit_features as cf

MODELO = "/tmp/llm/Qwen3-1.7B-Q8_0.gguf"
PERGUNTAS = {
    "urgencia": "O cliente demonstra urgência ou desespero para conseguir o dinheiro?",
    "negocio_ativo": "O cliente demonstra ter um negócio estabelecido e funcionando (fala de clientes, vendas, estoque, funcionários)?",
    "evasivo": "O cliente parece evasivo, contraditório ou desconfiado nas respostas?",
    "motivo_claro": "O cliente cita um motivo claro para o empréstimo (comprar mercadoria, reforma, capital de giro, equipamento)?",
    "divida": "O cliente menciona dívidas, nome sujo, protesto ou problemas financeiros?",
    "terceiro": "O cliente parece falar em nome de outra pessoa ou usar a conta de terceiro?",
}


def p_sim(llm, texto, pergunta):
    msgs = [{"role": "system", "content": "Você analisa conversas de WhatsApp de pedidos de crédito para pequenos comércios. Responda apenas Sim ou Não."},
            {"role": "user", "content": f"/no_think\nMENSAGENS DO CLIENTE:\n{texto}\n\nPERGUNTA: {pergunta}\nResponda apenas Sim ou Não."}]
    r = llm.create_chat_completion(messages=msgs, temperature=0.0, max_tokens=1, logprobs=True, top_logprobs=12)
    tops = r["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    sim = [t["logprob"] for t in tops if t["token"].strip().lower() in ("sim", "s")]
    nao = [t["logprob"] for t in tops if t["token"].strip().lower() in ("não", "nao", "n")]
    if not sim or not nao:
        return np.nan
    a, b = max(sim), max(nao)
    return 1 / (1 + math.exp(b - a))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    thr = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan4.pkl")
    tm, _ = ce.textos(ch, df)
    tm = tm[tm.str.len() > 40]
    d = df[df["contrato_id"].isin(tm.index) & df["ye_0"].notna()].sample(frac=1, random_state=11).head(n)
    print(f"contratos no piloto: {len(d)} (com texto e rotulo ye_0)", flush=True)
    llm = Llama(model_path=MODELO, n_ctx=1536, n_threads=thr, n_threads_batch=thr, n_batch=256, logits_all=True, verbose=False)
    llm.set_cache(LlamaRAMCache(capacity_bytes=1 << 30))
    linhas, t0 = [], time.time()
    for i, r in enumerate(d.itertuples(), 1):
        txt = tm.loc[r.contrato_id][:650]
        linha = {"contrato_id": r.contrato_id}
        for k, q in PERGUNTAS.items():
            try:
                linha["llm_" + k] = p_sim(llm, txt, q)
            except Exception as e:  # noqa
                print("erro:", str(e)[:120], flush=True)
                linha["llm_" + k] = np.nan
        linhas.append(linha)
        if i % 10 == 0:
            print(f"{i}/{len(d)} | {time.time() - t0:.0f}s ({(time.time() - t0) / i:.1f}s por contrato)", flush=True)
            pd.DataFrame(linhas).to_pickle("/tmp/llm_pilot.pkl")
    out = pd.DataFrame(linhas)
    out.to_pickle("/tmp/llm_pilot.pkl")
    x = out.merge(df[["contrato_id", "ye_0", "y_calote_30", "y_calote_15"]], on="contrato_id")
    print("\n== piloto: AUC de cada pergunta contra o calote (0,5 = nada; n pequeno => IC largo) ==", flush=True)
    for c in [c for c in x.columns if c.startswith("llm_")]:
        for alvo in ("ye_0", "y_calote_30"):
            z = x[x[alvo].notna() & x[c].notna()]
            if z[alvo].nunique() < 2 or len(z) < 30:
                continue
            auc = stats.mannwhitneyu(z.loc[z[alvo] == 1, c], z.loc[z[alvo] == 0, c]).statistic / ((z[alvo] == 1).sum() * (z[alvo] == 0).sum())
            p = stats.mannwhitneyu(z.loc[z[alvo] == 1, c], z.loc[z[alvo] == 0, c]).pvalue
            print(f"  {c:22s} x {alvo:11s} n={len(z)} AUC={auc:.3f} p={p:.3f}  (media P(sim)={z[c].mean():.2f})", flush=True)


if __name__ == "__main__":
    main()
