# Estudo massivo, etapa 1: o Qwen (endpoint particular do Lucas, beta, NAO produto) le a conversa pre-contrato de cada cliente SEM mascara
# (autorizado pelo Lucas para o estudo) e devolve (a) features estruturadas ricas, (b) uma estimativa de chance de atraso (0 a 100) e
# (c) o embedding semantico do texto (Qwen3-Embedding, 768 dimensoes). Grava em analytics_ml.llm_v2_features e analytics_ml.embeddings_conversa.
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

import ml_inadimplencia as ml

BASE_URL = os.environ.get("QWEN_BASE", "https://qwen.rangeltech.net/v1")
MAX = 6000

PROMPT = """Voce le as mensagens que um cliente escreveu em um atendimento de WhatsApp ANTES de contratar um microcredito para o comercio dele.
Descreva SO o que esta no texto; se algo nao aparece, use null ou o valor neutro. Responda APENAS com um JSON com exatamente estes campos:
{
 "finalidade": um de ["capital_de_giro","pagar_divida","estoque_mercadoria","reforma_ou_equipamento","emergencia_pessoal","expansao","nao_informa","outro"],
 "urgencia": 0 a 3, "tom": um de ["cooperativo","objetivo","ansioso","insistente","evasivo","desconfiado"],
 "consistencia_das_informacoes": 0 a 3, "informacao_voluntaria": 0 a 3,
 "menciona_divida_ou_negativacao": true/false, "dificuldade_de_caixa": true/false, "pede_valor_maior_que_o_oferecido": true/false,
 "negocia_taxa_ou_prazo": true/false, "demora_ou_resiste_a_enviar_documentos": true/false,
 "tipo_de_negocio": um de ["alimentacao","varejo","servicos","beleza","outro","nao_informa"],
 "anos_de_negocio_mencionados": numero ou null, "faturamento_mensal_mencionado_reais": numero ou null,
 "usa_maquininha": true/false, "recebe_por_pix": true/false, "cliente_indicado_por_terceiro": true/false,
 "linguagem_formal": 0 a 3, "erros_de_escrita": 0 a 3, "tamanho_da_conversa": 0 a 3,
 "prob_atraso": inteiro de 0 a 100 (sua melhor estimativa da chance de este cliente atrasar parcelas, apenas pelo que leu)
}"""

SQL_TEXTO = ml.SQL_MSGS


def _json(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def _chat(texto, chave):
    corpo = {"model": "qwen-abliterated", "temperature": 0, "max_tokens": 500, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": "Mensagens do cliente (em ordem):\n" + texto}]}
    for t in range(4):
        try:
            r = requests.post(BASE_URL + "/chat/completions", json=corpo, headers={"Authorization": f"Bearer {chave}"}, timeout=240)
            if r.status_code == 200:
                return _json(r.json()["choices"][0]["message"]["content"])
        except Exception:
            pass
        time.sleep(3 * (t + 1))
    return None


def _embed(textos, chave):
    for t in range(4):
        try:
            r = requests.post(BASE_URL + "/embeddings", json={"model": "qwen-embedding", "input": textos, "dimensions": 768},
                              headers={"Authorization": f"Bearer {chave}"}, timeout=300)
            if r.status_code == 200:
                return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
        except Exception:
            pass
        time.sleep(3 * (t + 1))
    return None


def rodar(ref=None, chave=None, workers=6, limite=None):
    chave = chave or os.environ["QWEN_KEY"]
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    por = {}
    for pid, content, ts in ch.query(SQL_TEXTO).result_rows:
        por.setdefault(pid, []).append((ts, content))
    ids = [p for p in por if len(por[p]) >= 3]
    if limite:
        ids = ids[:limite]
    textos = {}
    for p in ids:
        t = "\n".join(c for _, c in sorted(por[p]))
        textos[p] = t if len(t) <= MAX else t[: MAX // 2] + "\n[...]\n" + t[-MAX // 2:]
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        resp = list(ex.map(lambda p: _chat(textos[p], chave), ids))
    print(f"LLM: {sum(isinstance(r, dict) for r in resp)} de {len(ids)} respostas validas em {time.time()-t0:.0f}s", flush=True)
    linhas = []
    for p, r in zip(ids, resp):
        if not isinstance(r, dict):
            continue
        lin = {"pessoa_id": p}
        for k, v in r.items():
            if isinstance(v, bool):
                lin[k] = int(v)
            elif isinstance(v, (int, float)):
                lin[k] = float(v)
            elif v is None:
                lin[k] = None
            else:
                lin[k] = str(v)
        linhas.append(lin)
    F = pd.DataFrame(linhas)
    F["data_referencia"] = ref
    # embeddings
    t0 = time.time()
    ems = {}
    for i in range(0, len(ids), 16):
        lote = ids[i:i + 16]
        e = _embed([textos[p][:8000] for p in lote], chave)
        if e:
            for p, v in zip(lote, e):
                ems[p] = v
    print(f"embeddings: {len(ems)} em {time.time()-t0:.0f}s", flush=True)
    ch.command(f"CREATE DATABASE IF NOT EXISTS {ml.DB_ML}")
    ml._salvar(ch, "llm_v2_features", F, "Experimento. Features que o Qwen (beta) extraiu da conversa pre-contrato do cliente, sem mascara, incluindo a estimativa propria de chance de atraso (prob_atraso, 0 a 100). Sem texto da conversa.",
               dict(pessoa_id="hash do CPF/CNPJ", prob_atraso="estimativa do LLM de 0 a 100", data_referencia="data de corte do CRM"))
    ch.command(f"DROP TABLE IF EXISTS {ml.DB_ML}.embeddings_conversa")
    ch.command(f"CREATE TABLE {ml.DB_ML}.embeddings_conversa (pessoa_id String COMMENT 'hash do CPF/CNPJ', embedding Array(Float32) COMMENT 'embedding Qwen3-Embedding de 768 dimensoes do texto das mensagens do cliente anteriores ao 1o contrato', "
               f"data_referencia String COMMENT 'data de corte do CRM') ENGINE = MergeTree ORDER BY pessoa_id COMMENT 'Experimento. Embedding semantico da conversa pre-contrato por cliente.'")
    ch.insert(f"{ml.DB_ML}.embeddings_conversa", [[p, [float(x) for x in v], ref] for p, v in ems.items()], column_names=["pessoa_id", "embedding", "data_referencia"])
    return F, ems


if __name__ == "__main__":
    rodar(limite=int(os.environ.get("LIMITE", "0")) or None)
