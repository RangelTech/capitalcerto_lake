# Explicacao em linguagem natural de cada variavel do motor e resumo do acerto geral, escritos por LLM (9Router do Data IA Hub, conta do cliente).
# Roda toda noite depois do catalogo de variaveis (credit_variaveis.py), para o texto acompanhar os numeros do dia. LLM OPCIONAL: sem conta ou com o Hub fora, avisa e termina sem erro.
# Grava em hub_sandbox.motor_resumo_llm (chave 'var:<variavel>' e 'acerto'); o Hub mostra no modal de cada variavel e na aba Acerto do motor.
# Uso: python credit_var_resumo.py
import time

import requests

import credit_features as cf

HUB = "http://hub-data-ia-hub-1:8080/api/interno/llm"
SISTEMA = ("Voce explica resultados de um motor de risco de credito para uma equipe de negocio de uma factory de credito. Escreva em portugues claro, sem jargao, sem travessao "
           "e sem listas longas. Use somente os numeros recebidos; nao invente dado nem causa. Seja honesto sobre incerteza e sobre base pequena.")
TETO_SEGUNDOS = 25 * 60
NOMES_ALVO = {"calote": "Calote", "inadimplencia": "Inadimplência", "cluster_ruim": "Cluster ruim"}


def token():
    from airflow.models import Variable

    return Variable.get("hub_interno_token", default_var="")


def pedir(tok, prompt, maximo):
    r = requests.post(HUB, json={"mensagens": [{"role": "system", "content": SISTEMA}, {"role": "user", "content": prompt}], "maximo": maximo}, headers={"Authorization": "Bearer " + tok}, timeout=150)
    if r.status_code == 503:
        raise RuntimeError("indisponivel: " + r.text[:160])
    r.raise_for_status()
    return r.json()["texto"].strip()


def gravar(ch, chave, texto):
    ch.insert("hub_sandbox.motor_resumo_llm", [[chave, texto, "pipeline"]], column_names=["chave", "texto", "origem"])


def prompt_variavel(x, ganho=''):
    cor = "; ".join(f"{k[5:]}: {x[k]:.2f}" for k in x.index if k.startswith("corr_") and x[k] == x[k] and x[k] is not None)
    return (f"Variavel: {x['nome_claro']} ({x['variavel']}). Grupo: {x['grupo']}. O que e: {x['descricao'] or 'sem descricao'}. "
            f"Estado: {'usada no motor' if x['usada_no_motor'] else 'fora do motor'}. Resultado do teste: {x['status_teste'] or 'sem teste'}. "
            f"Correlacoes (-1 a +1): {cor or 'sem dados'}. Leitura automatica: {x['conclusao'] or ''}\n\n"
            "Escreva 3 a 4 frases: o que a variavel mede, como ela afeta calote, inadimplencia e cluster ruim segundo os numeros, e o que falta para ela ajudar o motor (ou por que ela ja ajuda).")


def prompt_acerto(ch):
    a = ch.query_df("SELECT alvo, etapa, modelo, features, round(auc,3) auc, round(auc_ic_inf,3) lo, round(auc_ic_sup,3) hi, n, confianca, toString(data_referencia) ref "
                    "FROM analytics.risco_acuracia_modelo WHERE run_id = (SELECT max(run_id) FROM analytics.risco_acuracia_modelo) ORDER BY alvo, etapa")
    if not len(a):
        return None
    tab = "\n".join(f"{NOMES_ALVO.get(r.alvo, r.alvo)} D+{r.etapa}: AUC {r.auc} (IC95 {r.lo} a {r.hi}), modelo {r.modelo}, conjunto {r.features}, {r.n} contratos de teste, confianca {r.confianca}" for r in a.itertuples())
    des = ch.query_df("SELECT variavel FROM hub_sandbox.motor_parametros FINAL WHERE ativa = 0 AND estado = 'desligado'")["variavel"].tolist()
    return (f"Resultados do ultimo treino do motor ({a['ref'].iloc[0]}). AUC 0,5 = chute, 1 = perfeito.\n{tab}\n\nVariaveis desligadas pelo administrador: {', '.join(des) or 'nenhuma'}.\n\n"
            "Escreva um resumo de 2 a 3 paragrafos curtos: em que idades e para qual resultado o motor e confiavel, onde ele ainda e fraco, e o que as variaveis desligadas significam para esse acerto.")


def main():
    tok = token()
    if not tok:
        print("sem token do Hub (Variable hub_interno_token); pulando as explicacoes por LLM")
        return
    ch = cf.conecta()
    ch.command("""CREATE TABLE IF NOT EXISTS hub_sandbox.motor_resumo_llm (chave String, texto String, atualizado_em DateTime DEFAULT now(), origem LowCardinality(String))
                  ENGINE = ReplacingMergeTree(atualizado_em) ORDER BY chave COMMENT 'Resumos em linguagem natural escritos por LLM para a aba Motor de credito do Data IA Hub.'""")
    v = ch.query_df("SELECT * FROM analytics_ml.motor_variaveis ORDER BY grupo, variavel")
    try:
        g = ch.query_df("SELECT variavel, etapa, auc_sem, auc_com, ic_inf, ic_sup, veredito FROM analytics_ml.motor_ganho_variavel FINAL")
        ganhos = {}
        for r in g.itertuples():
            ganhos.setdefault(r.variavel, []).append(f"dia {r.etapa}: acerto {r.auc_sem:.3f} sem e {r.auc_com:.3f} com (IC95 do ganho {r.ic_inf:+.3f} a {r.ic_sup:+.3f}, {r.veredito})")
    except Exception:  # noqa
        ganhos = {}
    t0, ok, falhas = time.time(), 0, 0
    try:
        p = prompt_acerto(ch)
        if p:
            gravar(ch, "acerto", pedir(tok, p, 900))
            ok += 1
        for _, x in v.iterrows():
            if time.time() - t0 > TETO_SEGUNDOS:
                print("teto de tempo; o resto fica para a proxima noite")
                break
            try:
                gravar(ch, "var:" + x["variavel"], pedir(tok, prompt_variavel(x, "; ".join(ganhos.get(x["variavel"], []))), 500))
                ok += 1
            except RuntimeError:
                raise
            except Exception as e:  # noqa
                falhas += 1
                print("falhou", x["variavel"], str(e)[:100])
    except RuntimeError as e:
        print("LLM indisponivel, pulando:", e)
    print(f"explicacoes gravadas: {ok}; falhas: {falhas}; {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
