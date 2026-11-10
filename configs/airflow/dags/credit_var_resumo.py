# Explicacao em linguagem natural de cada variavel do motor e resumo do acerto, escritos pelo modelo de linguagem do Data IA Hub (9Router, conta do cliente).
# O Hub tem os pesos e as correlacoes do dia e monta os textos; esta tarefa so dispara a geracao e acompanha ate terminar (LLM OPCIONAL: sem conta ou com o Hub fora, avisa e segue).
# Os textos ficam em hub_sandbox.motor_resumo_llm (chave 'var:<variavel>' e 'acerto') e aparecem no modal de cada variavel e na aba Acerto do motor.
# Uso: python credit_var_resumo.py
import time

import requests

HUB = "http://hub-data-ia-hub-1:8080/api/interno/resumos"
TETO_SEGUNDOS = 25 * 60


def token():
    from airflow.models import Variable

    return Variable.get("hub_interno_token", default_var="")


def main():
    tok = token()
    if not tok:
        print("sem token do Hub (Variable hub_interno_token); pulando os resumos")
        return
    h = {"Authorization": "Bearer " + tok}
    try:
        r = requests.post(HUB, headers=h, timeout=60)
        r.raise_for_status()
    except Exception as e:  # noqa
        print("Hub indisponivel, pulando os resumos:", str(e)[:150])
        return
    t0 = time.time()
    while time.time() - t0 < TETO_SEGUNDOS:
        time.sleep(20)
        try:
            e = requests.get(HUB, headers=h, timeout=30).json()
        except Exception as ex:  # noqa
            print("falha ao acompanhar:", str(ex)[:100])
            continue
        if not e.get("rodando"):
            if e.get("erro"):
                print("resumos interrompidos:", e["erro"])
            print(f"resumos gerados: {e.get('feitos')} de {e.get('total')}; falhas: {e.get('falhas')}")
            return
    print("teto de tempo; o Hub continua gerando em segundo plano")


if __name__ == "__main__":
    main()
