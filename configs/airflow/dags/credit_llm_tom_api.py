# Tom da conversa via API Qwen do proprio Lucas (qwen.rangeltech.net, chave OWNER passada por variavel de ambiente QWEN_KEY, nunca gravada). Mesmas 12 perguntas sim/nao do
# credit_llm_tom.py, probabilidade de "Sim" lida do logprob do 1o token, em paralelo. Amostra: TODOS os contratos com texto e rotulo. Uso: python credit_llm_tom_api.py [workers]
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

import credit_embed as ce
import credit_features as cf
from credit_llm_tom import PERGUNTAS

URL = "https://qwen.rangeltech.net/v1/chat/completions"


def p_sim(texto, pergunta, tent=3):
    corpo = {"model": "qwen-abliterated", "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 12, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "system", "content": "Voce analisa conversas de WhatsApp de pedidos de credito para pequenos comercios. Responda apenas Sim ou Nao."},
                          {"role": "user", "content": f"MENSAGENS DO CLIENTE (mais recentes):\n{texto}\n\nPERGUNTA: {pergunta}\nResponda apenas Sim ou Nao."}]}
    for i in range(tent):
        try:
            r = requests.post(URL, json=corpo, headers={"Authorization": "Bearer " + os.environ["QWEN_KEY"]}, timeout=180)
            tops = r.json()["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
            sim = [t["logprob"] for t in tops if t["token"].strip().lower() in ("sim", "s", "yes")]
            nao = [t["logprob"] for t in tops if t["token"].strip().lower() in ("não", "nao", "n", "no")]
            if not sim or not nao:
                return np.nan
            return 1 / (1 + math.exp(max(nao) - max(sim)))
        except Exception:  # noqa
            time.sleep(3 * (i + 1))
    return np.nan


def um_contrato(args):
    cid, txt = args
    return {"contrato_id": cid, **{"tom_" + k: p_sim(txt, q) for k, q in PERGUNTAS.items()}}


def main():
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    ch = cf.conecta()
    df = cf.montar(ch)
    tm, _ = ce.textos(ch, df)
    tm = tm[tm.str.len() > 40]
    d = df[df["contrato_id"].isin(tm.index) & (df["ye_0"].notna() | df["y_calote_30"].notna() | df["yi_0"].notna())]
    itens = [(c, tm.loc[c][-700:]) for c in d["contrato_id"]]
    print(f"contratos: {len(itens)}", flush=True)
    t0, linhas = time.time(), []
    with ThreadPoolExecutor(w) as ex:
        for i, r in enumerate(ex.map(um_contrato, itens), 1):
            linhas.append(r)
            if i % 20 == 0:
                pd.DataFrame(linhas).to_pickle("/tmp/llm_tom_api.pkl")
                print(f"{i}/{len(itens)} | {time.time() - t0:.0f}s", flush=True)
    pd.DataFrame(linhas).to_pickle("/tmp/llm_tom_api.pkl")
    print("fim", flush=True)


if __name__ == "__main__":
    main()
