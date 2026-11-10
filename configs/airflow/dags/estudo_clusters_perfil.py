# Perfil dos clusters de conversa (k=8 sobre o embedding Qwen): quem esta em cada cluster, o que dizem, e como pagam.
# Serve para transformar "o cluster se associa ao desfecho" em hipotese que o time de negocio consiga ler.
import math
import re
import unicodedata
from collections import Counter

import numpy as np
import pandas as pd

import estudo_massivo as e
import ml_inadimplencia as ml

STOP = ml.STOP | set("pra para essa esse aqui ola oi bom dia boa tarde noite tudo bem obrigado obrigada por favor gostaria queria quero vou pode sim nao ok sera quanto".split())


def tokens(t):
    t = unicodedata.normalize("NFD", t.lower()).encode("ascii", "ignore").decode()
    return [w for w in re.findall(r"[a-z]{4,}", t) if w not in STOP]


def rodar(ref=None, k=8):
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    df = e.montar_dados(ref)
    d = df[df["tem_conversa"]].reset_index(drop=True)
    P = d[[f"emb_pc{i+1}" for i in range(20)]].values
    a, C = e.kmeans(P[:, :10], k)
    d["cluster"] = a
    por = {}
    for pid, content, ts in ch.query(ml.SQL_MSGS).result_rows:
        por.setdefault(pid, []).append(content)
    docs = {p: Counter(tokens(" ".join(c))) for p, c in por.items()}
    tot = Counter()
    for p in d["pessoa_id"]:
        tot.update(set(docs.get(p, {})))
    linhas = []
    for c in range(k):
        g = d[d["cluster"] == c]
        n = len(g)
        df_c = Counter()
        for p in g["pessoa_id"]:
            df_c.update(set(docs.get(p, {})))
        # termos distintivos: mais frequentes no cluster que fora (log-odds suavizado), com 8+ clientes
        sc = []
        for w, k_in in df_c.items():
            if k_in < max(8, int(0.15 * n)):
                continue
            k_out = tot[w] - k_in
            lo = math.log((k_in + 0.5) / (n - k_in + 0.5)) - math.log((k_out + 0.5) / (len(d) - n - k_out + 0.5))
            sc.append((lo, w))
        top = ", ".join(w for _, w in sorted(sc, reverse=True)[:8])
        def pct(col, val):
            s = g[col].astype(str)
            return float((s == val).mean())
        linhas.append(dict(cluster=c, clientes=n, pct_paga_em_dia=float(g["q_paga_em_dia"].mean()), pct_pagou_e_parou=float(g["q_pagou_e_parou"].mean()), pct_atrasa_mas_paga=float(g["q_atrasa_mas_paga"].mean()),
                           pct_inadimplente_hoje=float(g["yatraso"].mean()), pct_cadastro_sem_perfil=float(g["sem_perfil"].mean()), pct_gateway_asaas=pct("gateway_1o_contrato", "ASAAS"),
                           pct_gateway_pluggy=pct("gateway_1o_contrato", "PLUGGY"), pct_gateway_efi=float(g["gateway_1o_contrato"].astype(str).str.startswith("EFI").mean()),
                           mes_medio_de_entrada=float(g["meses_desde_out25"].mean()), valor_medio_1o_contrato=float(g["valor_1o_contrato"].mean()), msgs_medias=float(g["msgs_pre"].mean()),
                           tamanho_medio_msg=float(g["tam_medio"].mean()), pct_cnpj=float((g["tipo_pessoa"] == "CNPJ").mean()), pct_alimentacao_llm=pct("v2_tipo_de_negocio", "alimentacao"),
                           faturamento_mencionado_mediano=float(pd.to_numeric(g["v2_faturamento_mensal_mencionado_reais"], errors="coerce").median()), prob_atraso_llm_media=float(pd.to_numeric(g["v2_prob_atraso"], errors="coerce").mean()),
                           termos_distintivos=top))
    P_ = pd.DataFrame(linhas)
    P_["data_referencia"] = ref
    ml._salvar(ch, "estudo_clusters_perfil", P_, "Estudo massivo. Perfil dos 8 clusters de conversa (embedding Qwen): tamanho, desfecho de pagamento, composicao de cohort, volume de mensagens, termos mais distintivos e leitura do LLM.",
               dict(cluster="numero do cluster (k-means k=8 sobre os 10 primeiros componentes do embedding)", clientes="clientes no cluster", pct_paga_em_dia="% no quadro paga em dia", pct_pagou_e_parou="% no quadro pagou e parou",
                    pct_atrasa_mas_paga="% no quadro atrasa mas paga", pct_inadimplente_hoje="% com parcela vencida hoje", pct_cadastro_sem_perfil="% sem sexo/segmento/idade preenchidos (cohort antigo)",
                    pct_gateway_asaas="% com 1o contrato no Asaas", pct_gateway_pluggy="% com 1o contrato na Pluggy", pct_gateway_efi="% com 1o contrato na Efi", mes_medio_de_entrada="meses desde out/2025 do 1o contrato",
                    valor_medio_1o_contrato="R$ medio do 1o contrato", msgs_medias="mensagens medias do cliente antes do contrato", tamanho_medio_msg="caracteres medios por mensagem", pct_cnpj="% cadastro CNPJ",
                    pct_alimentacao_llm="% que o LLM classificou como alimentacao", faturamento_mencionado_mediano="mediana do faturamento mensal que o cliente citou (R$)", prob_atraso_llm_media="media da estimativa do LLM",
                    termos_distintivos="palavras mais caracteristicas do cluster (log-odds)", data_referencia="data de corte do CRM"))
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 90)
    print(P_.drop(columns=["data_referencia"]).round(2).to_string())
    return P_


if __name__ == "__main__":
    rodar()
