# Tom da conversa por LLM, OPCIONAL, usando o motor de IA do Data IA Hub (9Router com a conta do cliente). So o DELTA: contratos com conversa nova ou alterada.
# Se o Hub estiver fora do ar ou sem conta de IA ligada, a tarefa termina com sucesso e diz que pulou: o pipeline nunca depende do LLM.
# Grava analytics.tom_conversa_contrato (12 perguntas, nota de 0 a 1 por pergunta). Uso: python credit_tom_hub.py [max_contratos]
import hashlib
import json
import re
import sys
import time

import pandas as pd
import requests

import credit_features as cf
from credit_tom_delta import DDL, MODELO_NOME as _M, PERGUNTAS, trechos  # noqa: F401

HUB = "http://hub-data-ia-hub-1:8080/api/interno/llm"
MODELO_NOME = "hub-llm"
VERSAO = "tom-hub-v1"


def token():
    from airflow.models import Variable

    return Variable.get("hub_interno_token", default_var="")


def pergunta_json():
    itens = "\n".join(f'- "{k}": {q}' for k, q in PERGUNTAS.items())
    return ("Você analisa conversas de WhatsApp de pedidos de crédito para pequenos comércios. Leia as MENSAGENS DO CLIENTE e dê, para cada item, uma nota de 0 a 1 "
            "(0 = claramente não, 1 = claramente sim). Responda SOMENTE um JSON com as chaves exatas, sem texto extra.\nItens:\n" + itens)


def pontuar(tok, texto):
    msgs = [{"role": "system", "content": pergunta_json()}, {"role": "user", "content": "MENSAGENS DO CLIENTE (mais recentes):\n" + texto}]
    r = requests.post(HUB, json={"mensagens": msgs, "maximo": 600}, headers={"Authorization": "Bearer " + tok}, timeout=150)
    if r.status_code == 503:
        raise RuntimeError("indisponivel: " + r.text[:160])
    r.raise_for_status()
    bruto = r.json()["texto"]
    m = re.search(r"\{.*\}", bruto, re.S)
    j = json.loads(m.group(0))
    return {f"tom_{k}": max(0.0, min(1.0, float(j[k]))) if k in j else None for k in PERGUNTAS}


def main():
    maximo = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    tok = token()
    if not tok:
        print("sem token do Hub: LLM opcional pulado")
        return
    ch = cf.conecta()
    ch.command(DDL)
    base, tm = trechos(ch)
    feitos = ch.query_df("select contrato_id, hash_texto from analytics.tom_conversa_contrato final where versao = {v:String}", parameters={"v": VERSAO})
    feitos.columns = [c.split(".")[-1] for c in feitos.columns]
    ja = dict(zip(feitos["contrato_id"], feitos["hash_texto"])) if len(feitos) and "contrato_id" in feitos.columns else {}   # tabela vazia na 1a rodada
    h = tm.map(lambda s: hashlib.md5(s.encode("utf-8")).hexdigest())
    pend = [c for c in h.index if ja.get(c) != h[c]]
    ordem = base.set_index("contrato_id")["data_inicio"].reindex(pend).sort_values(ascending=False).index.tolist()[:maximo]
    print(f"conversas: {len(tm)}; pendentes: {len(pend)}; nesta rodada: {len(ordem)}", flush=True)
    linhas, t0, falhas = [], time.time(), 0
    for i, cid in enumerate(ordem, 1):
        try:
            nota = pontuar(tok, tm[cid])
        except RuntimeError as e:
            print("LLM do Hub indisponivel, pulando o tom desta noite:", str(e)[:160])
            break
        except Exception as e:  # noqa
            falhas += 1
            print("falha em", cid, str(e)[:100])
            if falhas >= 5:
                print("muitas falhas, encerrando")
                break
            continue
        linhas.append({"contrato_id": cid, "hash_texto": h[cid], "modelo": MODELO_NOME, "versao": VERSAO, **nota})
        if len(linhas) >= 5:
            ch.insert_df("analytics.tom_conversa_contrato", pd.DataFrame(linhas))
            linhas = []
            print(f"{i}/{len(ordem)} | {time.time() - t0:.0f}s", flush=True)
    if linhas:
        ch.insert_df("analytics.tom_conversa_contrato", pd.DataFrame(linhas))
    print("tom: fim (opcional)", flush=True)


if __name__ == "__main__":
    main()
