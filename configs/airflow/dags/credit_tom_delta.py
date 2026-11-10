# Tom da conversa por LLM pequeno EM CPU, dentro da VPS (sem API externa), so no DELTA: contratos cuja conversa mudou ou que ainda nao tem tom com a versao atual.
# 12 perguntas sim/nao; probabilidade de "Sim" lida do logprob do 1o token (sem gerar texto). Grava analytics.tom_conversa_contrato.
# O modelo (gguf) fica no MinIO (models/llm/) e e baixado para /tmp/llm quando falta, para sobreviver a recriacao do container.
# Uso:
#   python credit_tom_delta.py delta [max_contratos] [minutos]      rotina diaria (DAG tom_llm_diario)
#   python credit_tom_delta.py validar [n]                          compara o 1,7B em CPU com o Qwen grande (/tmp/llm_tom_api.pkl) em n contratos
import hashlib
import math
import os
import sys
import time

import numpy as np
import pandas as pd

import credit_embed as ce
import credit_features as cf

MODELO_ARQ = "/tmp/llm/Qwen3-1.7B-Q8_0.gguf"
MODELO_NOME = "qwen3-1.7b-q8-cpu"
VERSAO = "tom-v1"
BUCKET, CHAVE = "models", "llm/Qwen3-1.7B-Q8_0.gguf"
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
DDL = ("CREATE TABLE IF NOT EXISTS analytics.tom_conversa_contrato (contrato_id String COMMENT 'contrato (analytics.fato_contrato)', hash_texto String COMMENT 'identifica o trecho de conversa lido; se a conversa muda, o tom e recalculado', "
       "modelo LowCardinality(String) COMMENT 'modelo que leu a conversa (CPU na VPS)', versao LowCardinality(String) COMMENT 'versao das perguntas', "
       + ", ".join(f"tom_{k} Nullable(Float32) COMMENT 'probabilidade de Sim: {v.replace(chr(39), ' ')}'" for k, v in PERGUNTAS.items())
       + ", processado_em DateTime DEFAULT now() COMMENT 'quando foi calculado') ENGINE = ReplacingMergeTree(processado_em) ORDER BY contrato_id "
       "COMMENT 'Tom da conversa do cliente antes do contrato, lido por LLM pequeno em CPU na propria VPS (12 perguntas sim/nao, probabilidade de Sim). Atualizado so no delta. Medida experimental: o sinal contra calote e pequeno e foi validado com o modelo grande, ver motor_tom_testes.'")


def garantir_modelo():
    if os.path.exists(MODELO_ARQ) and os.path.getsize(MODELO_ARQ) > 1_000_000_000:
        return
    os.makedirs(os.path.dirname(MODELO_ARQ), exist_ok=True)
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    S3Hook(aws_conn_id="minio_s3").get_conn().download_file(BUCKET, CHAVE, MODELO_ARQ)


def carrega_llm(threads):
    from llama_cpp import Llama, LlamaRAMCache

    garantir_modelo()
    llm = Llama(model_path=MODELO_ARQ, n_ctx=1536, n_threads=threads, n_threads_batch=threads, n_batch=256, logits_all=True, verbose=False)
    llm.set_cache(LlamaRAMCache(capacity_bytes=1 << 30))
    return llm


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


def trechos(ch):
    """contrato_id -> ultimos 700 caracteres da conversa ate 1 dia depois do inicio (mesma regra dos testes)."""
    base = cf._contratos(ch)
    base["data_inicio"] = pd.to_datetime(base["data_inicio"])
    tm, _ = ce.textos(ch, base)
    tm = tm[tm.str.len() > 40]
    return base, tm.map(lambda s: s[-700:])


def tom_de(llm, texto):
    return {f"tom_{k}": p_sim(llm, texto, q) for k, q in PERGUNTAS.items()}


def delta(max_contratos=40, minutos=75, threads=4):
    ch = cf.conecta()
    ch.command(DDL)
    base, tm = trechos(ch)
    feitos = ch.query_df("select contrato_id, hash_texto from analytics.tom_conversa_contrato final where versao = {v:String} and modelo = {m:String}", parameters={"v": VERSAO, "m": MODELO_NOME})
    feitos.columns = [c.split(".")[-1] for c in feitos.columns]
    ja = dict(zip(feitos["contrato_id"], feitos["hash_texto"]))
    h = tm.map(lambda s: hashlib.md5(s.encode("utf-8")).hexdigest())
    pend = [c for c in h.index if ja.get(c) != h[c]]
    ordem = base.set_index("contrato_id")["data_inicio"].reindex(pend).sort_values(ascending=False).index.tolist()[:max_contratos]   # os mais novos primeiro
    print(f"contratos com conversa: {len(tm)}; com tom atual: {len(tm) - len(pend)}; pendentes: {len(pend)}; nesta rodada: {len(ordem)}", flush=True)
    if not ordem:
        return
    llm = carrega_llm(threads)
    t0, linhas = time.time(), []
    for i, cid in enumerate(ordem, 1):
        if time.time() - t0 > minutos * 60:
            print("tempo esgotado; o resto fica para a proxima noite", flush=True)
            break
        linhas.append({"contrato_id": cid, "hash_texto": h[cid], "modelo": MODELO_NOME, "versao": VERSAO, **tom_de(llm, tm[cid])})
        if len(linhas) >= 5 or i == len(ordem):
            ch.insert_df("analytics.tom_conversa_contrato", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(ordem)} | {time.time() - t0:.0f}s", flush=True)
    if linhas:
        ch.insert_df("analytics.tom_conversa_contrato", pd.DataFrame(linhas))


def validar(n=60, threads=4):
    """O tom em CPU (1,7B) concorda com o do modelo grande (Qwen 27B), que foi o que mostrou sinal? Correlacao de Spearman por pergunta em n contratos."""
    from scipy import stats

    ch = cf.conecta()
    base, tm = trechos(ch)
    grande = pd.read_pickle("/tmp/llm_tom_api.pkl").set_index("contrato_id")
    ids = [c for c in grande.index if c in tm.index][:n]
    llm = carrega_llm(threads)
    t0, linhas = time.time(), []
    for i, cid in enumerate(ids, 1):
        linhas.append({"contrato_id": cid, **tom_de(llm, tm[cid])})
        if i % 5 == 0:
            print(f"{i}/{len(ids)} | {time.time() - t0:.0f}s ({(time.time() - t0) / i:.0f}s por contrato)", flush=True)
    cpu = pd.DataFrame(linhas).set_index("contrato_id")
    cpu.to_pickle("/tmp/tom_cpu_validacao.pkl")
    res = []
    for c in cpu.columns:
        rho, p = stats.spearmanr(cpu[c], grande.loc[cpu.index, c], nan_policy="omit")
        res.append((c, round(float(rho), 3), round(float(p), 4)))
    print("Spearman entre o 1,7B em CPU e o Qwen grande, por pergunta:")
    for r in sorted(res, key=lambda x: -x[1]):
        print("  ", r)
    eng = ["tom_evasivo", "tom_desconfia", "tom_pergunta_taxa", "tom_urgencia", "tom_agressivo"]
    rho, p = stats.spearmanr(cpu[eng].mean(axis=1), grande.loc[cpu.index, eng].mean(axis=1))
    print(f"engajamento (media das 5 perguntas): rho={rho:.3f} p={p:.4f} n={len(cpu)}")


if __name__ == "__main__":
    modo = sys.argv[1] if len(sys.argv) > 1 else "delta"
    if modo == "validar":
        validar(int(sys.argv[2]) if len(sys.argv) > 2 else 60)
    else:
        delta(int(sys.argv[2]) if len(sys.argv) > 2 else 40, int(sys.argv[3]) if len(sys.argv) > 3 else 75)
