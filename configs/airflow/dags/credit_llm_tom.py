# LLM pequeno (Qwen3 1,7B, CPU) como EXTRATOR de tom e comportamento da conversa ANTES da assinatura e ANTES da renovacao: 12 perguntas sim/nao, probabilidade de "Sim"
# lida do logprob do 1o token (sem gerar texto). Janela: ultimos ~700 caracteres das mensagens do cliente ate 1 dia depois do inicio do contrato (para renovacoes isso e a
# conversa mais recente antes do novo contrato). Amostra: todas as renovacoes com texto + primeiros contratos, com rotulo. Uso: python credit_llm_tom.py [n_total] [threads]
import math
import re
import sys
import time

import numpy as np
import pandas as pd
from llama_cpp import Llama, LlamaRAMCache

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
    "direto": "O tom do cliente é direto e objetivo, sem rodeios?",
    "agressivo": "O cliente é grosseiro, impaciente ou agressivo no tom?",
    "recusa": "O cliente se recusa a informar algum dado, enviar documento ou aceitar alguma condição?",
    "desconfia": "O cliente demonstra desconfiança sobre o golpe, as taxas ou a empresa?",
    "pergunta_taxa": "O cliente pergunta sobre juros, taxa, valor da parcela ou quanto vai pagar no total?",
    "cordial": "O cliente é educado e cordial (agradece, cumprimenta)?",
}


def p_sim(llm, texto, pergunta):
    msgs = [{"role": "system", "content": "Você analisa conversas de WhatsApp de pedidos de crédito para pequenos comércios. Responda apenas Sim ou Não."},
            {"role": "user", "content": f"/no_think\nMENSAGENS DO CLIENTE (mais recentes):\n{texto}\n\nPERGUNTA: {pergunta}\nResponda apenas Sim ou Não."}]
    r = llm.create_chat_completion(messages=msgs, temperature=0.0, max_tokens=1, logprobs=True, top_logprobs=12)
    tops = r["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    sim = [t["logprob"] for t in tops if t["token"].strip().lower() in ("sim", "s")]
    nao = [t["logprob"] for t in tops if t["token"].strip().lower() in ("não", "nao", "n")]
    if not sim or not nao:
        return np.nan
    return 1 / (1 + math.exp(max(nao) - max(sim)))


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    thr = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan6.pkl")
    tm, _ = ce.textos(ch, df)  # junta mensagens ate 1 dia depois do inicio; aqui usamos a CAUDA (mais recentes)
    tm = tm[tm.str.len() > 40]
    d = df[df["contrato_id"].isin(tm.index) & (df["ye_0"].notna() | df["y_calote_30"].notna())].copy()
    ren = d[d["ordem_contrato_cliente"] > 1]
    pri = d[d["ordem_contrato_cliente"] <= 1].sample(frac=1, random_state=21)
    d = pd.concat([ren, pri]).head(n)
    print(f"contratos: {len(d)} (renovacoes {int((d['ordem_contrato_cliente'] > 1).sum())})", flush=True)
    llm = Llama(model_path=MODELO, n_ctx=1536, n_threads=thr, n_threads_batch=thr, n_batch=256, logits_all=True, verbose=False)
    llm.set_cache(LlamaRAMCache(capacity_bytes=1 << 30))
    linhas, t0 = [], time.time()
    for i, r in enumerate(d.itertuples(), 1):
        txt = tm.loc[r.contrato_id][-700:]
        linha = {"contrato_id": r.contrato_id}
        for k, q in PERGUNTAS.items():
            try:
                linha["tom_" + k] = p_sim(llm, txt, q)
            except Exception as e:  # noqa
                linha["tom_" + k] = np.nan
        linhas.append(linha)
        if i % 10 == 0:
            pd.DataFrame(linhas).to_pickle("/tmp/llm_tom.pkl")
            print(f"{i}/{len(d)} | {time.time() - t0:.0f}s ({(time.time() - t0) / i:.0f}s por contrato)", flush=True)
    pd.DataFrame(linhas).to_pickle("/tmp/llm_tom.pkl")
    print("fim", flush=True)


if __name__ == "__main__":
    main()
