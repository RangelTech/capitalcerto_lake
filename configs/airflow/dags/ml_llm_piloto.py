# Piloto de hipoteses: um LLM (Qwen, endpoint particular do Lucas, beta, NAO produtizado) le as mensagens que o cliente
# escreveu ANTES do primeiro contrato e devolve features estruturadas (finalidade, urgencia, tom, consistencia...).
# Depois testamos se essas features separam quem ficou inadimplente, descontando o efeito do cohort (valor, tempo de casa,
# gateway antigo, cadastro sem perfil). O LLM e extrator de sinal, nunca decisor de credito.
#
# Privacidade: o texto e mascarado AQUI (CPF/CNPJ, telefones, e-mails, links, numeros e nomes de cliente do CRM) antes de
# sair para o Qwen. Nenhuma conversa e gravada: so as features estruturadas, ligadas ao hash do CPF/CNPJ.
import json
import math
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

import ml_inadimplencia as ml

QWEN_URL = os.environ.get("QWEN_URL", "https://qwen.rangeltech.net/v1/chat/completions")
MODELO = "qwen-abliterated"
MAX_CHARS = 6000

PROMPT = """Voce le mensagens que um cliente escreveu em um atendimento de WhatsApp ANTES de contratar um microcredito para o comercio dele.
Descreva SO o que esta escrito. Nao adivinhe, nao julgue se ele vai pagar. Se o texto nao mostra algo, use o valor neutro.
Responda APENAS com um JSON, sem explicacao, com exatamente estes campos:
{
 "finalidade": um de ["capital_de_giro","pagar_divida","estoque_mercadoria","reforma_ou_equipamento","emergencia_pessoal","nao_informa","outro"],
 "urgencia": 0 a 3 (0 sem pressa, 3 precisa do dinheiro hoje),
 "menciona_divida_ou_negativacao": true/false (fala de dividas, nome sujo, restricao),
 "dificuldade_de_caixa": true/false (relata vendas fracas, aperto, atraso com fornecedores),
 "tom": um de ["cooperativo","objetivo","ansioso","insistente","evasivo","desconfiado"],
 "consistencia_das_informacoes": 0 a 3 (3 = informacoes coerentes e completas, 0 = contraditorias ou mudam durante a conversa),
 "pede_valor_maior_que_o_oferecido": true/false,
 "negocia_taxa_ou_prazo": true/false,
 "demora_ou_resiste_a_enviar_documentos": true/false,
 "informacao_voluntaria": 0 a 3 (quanto conta sobre o negocio sem ser perguntado)
}"""

SQL_NOMES = "SELECT DISTINCT ifNull(name, '') FROM trusted_credhub.customer"


def _mascarar(txt, nomes):
    t = re.sub(r"https?://\S+", "<link>", txt)
    t = re.sub(r"\S+@\S+", "<email>", t)
    t = re.sub(r"\d[\d\.\-/\s\(\)]{4,}\d", "<num>", t)
    t = re.sub(r"\d+", "<num>", t)
    def troca(m):
        w = m.group(0)
        base = unicodedata.normalize("NFD", w.lower()).encode("ascii", "ignore").decode()
        return "<nome>" if base in nomes else w
    return re.sub(r"[A-Za-zÀ-ÿ]{3,}", troca, t)


def _chamar(texto, chave):
    corpo = {"model": MODELO, "temperature": 0, "max_tokens": 400, "chat_template_kwargs": {"enable_thinking": False},
             "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": "Mensagens do cliente (em ordem):\n" + texto}]}
    for tentativa in range(4):
        try:
            r = requests.post(QWEN_URL, json=corpo, headers={"Authorization": f"Bearer {chave}"}, timeout=180)
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", msg, re.S)
                return json.loads(m.group(0)) if m else None
        except Exception:
            pass
        time.sleep(3 * (tentativa + 1))
    return None


CAMPOS_CAT = ["finalidade", "tom"]
CAMPOS_BOOL = ["menciona_divida_ou_negativacao", "dificuldade_de_caixa", "pede_valor_maior_que_o_oferecido", "negocia_taxa_ou_prazo", "demora_ou_resiste_a_enviar_documentos"]
CAMPOS_ORD = ["urgencia", "consistencia_das_informacoes", "informacao_voluntaria"]


def rodar(ref=None, limite=None, chave=None, workers=6):
    chave = chave or os.environ["QWEN_KEY"]
    ch = ml._ch()
    ref = ref or ch.query("SELECT toString(toDate(max(created_at))) FROM trusted_credhub.audit_event").result_rows[0][0]
    perfil = ch.query_df(ml.SQL_PERFIL.replace("{ref}", ref)).set_index("pessoa_id")
    nomes = set()
    for (n,) in ch.query(SQL_NOMES).result_rows:
        for w in re.findall(r"[a-z]{3,}", unicodedata.normalize("NFD", n.lower()).encode("ascii", "ignore").decode()):
            nomes.add(w)
    por = {}
    for pid, content, ts in ch.query(ml.SQL_MSGS).result_rows:
        por.setdefault(pid, []).append((ts, content))
    # nome proprio tem baixa frequencia; palavra comum que aparece em nomes de empresa ("favor", "por", "mercado") nao pode ser mascarada
    df_tok = {}
    for p, msgs in por.items():
        for w in set(re.findall(r"[a-z]{3,}", unicodedata.normalize("NFD", " ".join(c for _, c in msgs).lower()).encode("ascii", "ignore").decode())):
            df_tok[w] = df_tok.get(w, 0) + 1
    nomes = {w for w in nomes if len(w) >= 4 and df_tok.get(w, 0) < 0.04 * max(len(por), 1) and w not in ml.STOP}
    ids = [p for p in por if p in perfil.index and len(por[p]) >= 3]
    if limite:
        ids = ids[:limite]
    textos = {p: _mascarar("\n".join(c for _, c in sorted(por[p])), nomes)[:MAX_CHARS] for p in ids}
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        resp = list(ex.map(lambda p: _chamar(textos[p], chave), ids))
    ok = [(p, r) for p, r in zip(ids, resp) if isinstance(r, dict)]
    print(f"respostas validas: {len(ok)} de {len(ids)} em {time.time()-t0:.0f}s")
    linhas = []
    for p, r in ok:
        lin = {"pessoa_id": p}
        for c in CAMPOS_CAT:
            lin[c] = str(r.get(c, "nao_informa"))
        for c in CAMPOS_BOOL:
            lin[c] = bool(r.get(c, False))
        for c in CAMPOS_ORD:
            try:
                lin[c] = int(r.get(c, 0))
            except Exception:
                lin[c] = 0
        lin["inadimplente"] = int(perfil.loc[p, "inadimplente"])
        linhas.append(lin)
    F = pd.DataFrame(linhas)
    return F, perfil, ref, len(ids)


def analisar(F, perfil, ref, n_ids):
    base = F["inadimplente"].mean()
    y = F["inadimplente"].values.astype(float)
    P = perfil.loc[F["pessoa_id"]]
    # controles de cohort (os mesmos do experimento de perfil)
    sem_perfil = ((P["sexo"].replace("", "sem_info") == "sem_info") | (P["segmento"] == "sem_info")).astype(float).values
    ctr = [np.log1p(P["total_emprestado"].values.astype(float)), np.log1p(P["dias_desde_primeiro"].clip(lower=0).values.astype(float)),
           P["qtd_contratos"].values.astype(float), P["tem_asaas"].fillna(0).values.astype(float)]
    ctr = [(v - v.mean()) / (v.std() or 1.0) for v in ctr] + [sem_perfil]
    C = np.column_stack(ctr)
    linhas = []

    def ajustado(coluna):
        X = np.column_stack([np.ones(len(y)), C, coluna])
        b, cov = ml.logistica(X, y, l2=1e-2)
        se = math.sqrt(cov[-1, -1])
        return math.exp(b[-1]), math.exp(b[-1] - 1.96 * se), math.exp(b[-1] + 1.96 * se)

    def linha(campo, nivel, mask):
        n, k = int(mask.sum()), int(y[mask].sum())
        if n < 10 or (len(y) - n) < 10:
            return
        lo, hi = ml.wilson(k, n)
        suav = (k + ml.K_SUAVIZACAO * base) / (n + ml.K_SUAVIZACAO)
        o, ol, oh = ajustado(mask.astype(float))
        linhas.append(dict(feature=campo, nivel=str(nivel), clientes=n, inadimplentes=k, taxa=k / n, taxa_ic_baixo=lo, taxa_ic_alto=hi,
                           indice_bruto=(k / n) / base, taxa_suavizada=suav, indice_suavizado=suav / base, odds_ratio_ajustado=o,
                           or_ic_baixo=ol, or_ic_alto=oh, significativo=bool(ol > 1 or oh < 1), grupo_pequeno=n < 30, taxa_geral=base))

    for c in CAMPOS_CAT:
        for nivel in F[c].unique():
            linha(c, nivel, (F[c] == nivel).values)
    for c in CAMPOS_BOOL:
        linha(c, "sim", F[c].values)
    for c in CAMPOS_ORD:
        linha(c, "alto (2 ou 3)", (F[c] >= 2).values)
        linha(c, "baixo (0 ou 1)", (F[c] <= 1).values)
    assoc = pd.DataFrame(linhas)
    # modelo: o LLM acrescenta algo alem do cohort?
    dummies = [(F[c] == v).astype(float).values for c in CAMPOS_CAT for v in sorted(F[c].unique())[1:]]
    L = np.column_stack(dummies + [F[c].astype(float).values for c in CAMPOS_BOOL + CAMPOS_ORD])
    L = (L - L.mean(0)) / (L.std(0) + 1e-9)
    res = {}
    for nome, X in [("so_cohort", C), ("so_llm", L), ("cohort_e_llm", np.column_stack([C, L]))]:
        res[nome] = ml._cv_auc(X, y, l2=3.0)[:2]
    rng = np.random.default_rng(11)
    nulo = np.array([ml._cv_auc(np.column_stack([C, L]), rng.permutation(y), rep=1, seed=int(rng.integers(1e6)))[0] for _ in range(25)])
    modelo = pd.DataFrame([dict(clientes=len(y), clientes_com_mensagens=n_ids, prevalencia=float(base), auc_so_cohort=res["so_cohort"][0], auc_so_llm=res["so_llm"][0],
                                auc_cohort_e_llm=res["cohort_e_llm"][0], auc_desvio=res["cohort_e_llm"][1], auc_nulo_p95=float(np.quantile(nulo, 0.95)),
                                ganho_do_llm=res["cohort_e_llm"][0] - res["so_cohort"][0], p_valor_permutacao=float((np.sum(nulo >= res["cohort_e_llm"][0]) + 1) / (len(nulo) + 1)),
                                data_referencia=ref)])
    assoc["data_referencia"] = ref
    return assoc, modelo


def gravar(F, assoc, modelo):
    ch = ml._ch()
    F = F.copy()
    F["data_referencia"] = modelo["data_referencia"].iloc[0]
    ml._salvar(ch, "llm_piloto_features", F, "Experimento. Features estruturadas que o Qwen (beta, nao produtizado) extraiu das mensagens do cliente anteriores ao primeiro contrato, mascaradas. Sem texto das conversas.",
               dict(pessoa_id="hash do CPF/CNPJ", finalidade="finalidade do credito declarada", tom="tom da conversa", menciona_divida_ou_negativacao="fala de dividas ou nome sujo",
                    dificuldade_de_caixa="relata aperto de caixa", pede_valor_maior_que_o_oferecido="pediu mais do que o oferecido", negocia_taxa_ou_prazo="negocia taxa ou prazo",
                    demora_ou_resiste_a_enviar_documentos="resiste ou demora a mandar documentos", urgencia="0 sem pressa a 3 precisa hoje", consistencia_das_informacoes="0 contraditorio a 3 coerente",
                    informacao_voluntaria="0 a 3 quanto conta sem ser perguntado", inadimplente="1 se tem contrato em atraso", data_referencia="data de corte do CRM"))
    ml._salvar(ch, "llm_piloto_associacao", assoc, "Experimento. Cada feature extraida pelo LLM x inadimplencia, com fator normalizador (indice sobre a taxa geral, taxa suavizada e odds ratio ajustado por valor, tempo de casa, qtd de contratos, Asaas e cadastro sem perfil).",
               dict(feature="campo extraido pelo LLM", nivel="valor", clientes="clientes no grupo", inadimplentes="inadimplentes no grupo", taxa="inadimplentes / clientes", taxa_ic_baixo="Wilson 95% inferior", taxa_ic_alto="Wilson 95% superior",
                    indice_bruto="taxa do grupo / taxa geral", taxa_suavizada="taxa puxada para a geral em grupo pequeno", indice_suavizado="taxa suavizada / taxa geral", odds_ratio_ajustado="odds ratio controlando o cohort",
                    or_ic_baixo="OR 95% inferior", or_ic_alto="OR 95% superior", significativo="intervalo do OR nao inclui 1", grupo_pequeno="menos de 30 clientes", taxa_geral="taxa geral da amostra", data_referencia="data de corte do CRM"))
    ml._salvar(ch, "llm_piloto_modelo", modelo, "Experimento. O LLM acrescenta capacidade de separar inadimplentes alem do cohort? AUC por validacao cruzada com teste de permutacao.",
               dict(clientes="clientes com resposta valida", clientes_com_mensagens="clientes com 3+ mensagens antes do contrato", prevalencia="taxa de inadimplentes", auc_so_cohort="AUC so com valor, tempo de casa, qtd de contratos, Asaas e cadastro sem perfil",
                    auc_so_llm="AUC so com as features do LLM", auc_cohort_e_llm="AUC com cohort + LLM", auc_desvio="desvio entre repeticoes", auc_nulo_p95="percentil 95 do AUC com rotulos embaralhados",
                    ganho_do_llm="auc_cohort_e_llm menos auc_so_cohort", p_valor_permutacao="chance do AUC combinado sair por sorte", data_referencia="data de corte do CRM"))


if __name__ == "__main__":
    F, perfil, ref, n = rodar(limite=int(os.environ.get("LIMITE", "0")) or None)
    a, m = analisar(F, perfil, ref, n)
    gravar(F, a, m)
    print(m.round(3).to_string())
