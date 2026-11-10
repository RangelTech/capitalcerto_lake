# Ganho de acerto de CADA variavel do catalogo, medido fora do tempo (treina no passado, testa nos meses seguintes) com intervalo de bootstrap.
# Pergunta respondida: "se eu LIGAR esta variavel no motor, o acerto melhora, fica igual ou piora?". Alimenta o alerta do Data IA Hub ao ligar uma variavel que nao ajuda.
# Medida no dia 0 e no dia 1 (as idades em que o cadastro importa; depois disso o comportamento de pagamento domina) contra o candidato simples do motor.
#   analytics_ml.motor_ganho_variavel   1 linha por variavel e etapa: auc sem e com a variavel, ganho, IC95, contratos de teste e veredito (ganho, sem_ganho, piora)
# Uso: python credit_ganho_variaveis.py
import time

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import credit_experimentos as ex
import credit_features as cf

BASE0 = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "e_renovacao", "cep_vazio"]
CATS0 = ["provedor_pagamento"]
ETAPAS = ((0, "ye_0"), (1, "ye_1"))
TETO_SEGUNDOS = 45 * 60
DDL = """CREATE TABLE IF NOT EXISTS analytics_ml.motor_ganho_variavel (
  variavel String COMMENT 'nome da variavel (coluna de motor_features_contrato)', etapa UInt8 COMMENT 'idade do contrato avaliada (0 ou 1)', alvo LowCardinality(String) COMMENT 'resultado previsto (calote)',
  auc_sem Float32 COMMENT 'acerto do candidato simples sem a variavel (fora do tempo)', auc_com Float32 COMMENT 'acerto com a variavel acrescentada',
  ganho Float32 COMMENT 'auc_com menos auc_sem (negativo = a variavel piora o motor)', ic_inf Float32 COMMENT 'limite inferior do IC95 do ganho (bootstrap)', ic_sup Float32 COMMENT 'limite superior do IC95 do ganho',
  n UInt32 COMMENT 'contratos no teste fora do tempo', veredito LowCardinality(String) COMMENT 'ganho (IC acima de zero), sem_ganho (IC cruza zero) ou piora (IC abaixo de zero)',
  atualizado_em DateTime DEFAULT now() COMMENT 'quando foi medido') ENGINE = ReplacingMergeTree(atualizado_em) ORDER BY (variavel, etapa, alvo)
  COMMENT 'Ganho de acerto de cada variavel do catalogo se ela for ligada no motor, medido todo dia. Alimenta o alerta da aba Motor de credito do Data IA Hub.'"""


def medir(ob, on):
    if not len(ob) or not len(on) or len(ob) != len(on):
        return None
    yb, pb, pn = ob["y"].values, ob["p"].values, on["p"].values
    rng = np.random.RandomState(7)
    ds = [roc_auc_score(yb[i], pn[i]) - roc_auc_score(yb[i], pb[i]) for i in (rng.randint(0, len(yb), len(yb)) for _ in range(300)) if len(set(yb[i])) > 1]
    if not ds:
        return None
    return roc_auc_score(yb, pb), roc_auc_score(yb, pn), float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5)), len(yb)


def gravar(ch, linhas):
    if linhas:
        df = pd.DataFrame(linhas)
        ch.insert("analytics_ml.motor_ganho_variavel", df, column_names=list(df.columns))


def main():
    ch = cf.conecta()
    ch.command(DDL)
    df = cf.montar(ch)
    df["data_inicio"] = pd.to_datetime(df["data_inicio"])
    cat = ch.query_df("SELECT variavel FROM analytics_ml.motor_variaveis")["variavel"].tolist()
    candidatas = [v for v in cat if v in df.columns and v != "provedor_pagamento" and pd.to_numeric(df[v], errors="coerce").notna().sum() + df[v].notna().sum() > 0]
    print(f"{len(candidatas)} variaveis do catalogo para medir", flush=True)
    t0, linhas = time.time(), []
    for etapa, alvo in ETAPAS:
        if alvo not in df.columns:
            continue
        ob0 = ex.avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, CATS0, [c for c in BASE0 if c in df.columns], "rf", ultimos_meses=5)
        for v in candidatas:
            if time.time() - t0 > TETO_SEGUNDOS:
                print("teto de tempo; o resto fica para a proxima noite", flush=True)
                break
            try:
                x = pd.to_numeric(df[v], errors="coerce")
                numerica = pd.api.types.is_numeric_dtype(df[v]) or x.notna().sum() >= 0.5 * len(df)
                if numerica and x.nunique() < 2:
                    continue
                sem = [c for c in BASE0 if c in df.columns and c != v]
                ob = ob0 if v not in BASE0 else ex.avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, CATS0, sem, "rf", ultimos_meses=5)
                on = ex.avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, CATS0 + ([] if numerica else [v]), sem + ([v] if numerica else []), "rf", ultimos_meses=5)
                g = medir(ob, on)
                if not g:
                    continue
                ver = "ganho" if g[2] > 0 else "piora" if g[3] < 0 else "sem_ganho"
                linhas.append({"variavel": v, "etapa": etapa, "alvo": "calote", "auc_sem": g[0], "auc_com": g[1], "ganho": g[1] - g[0], "ic_inf": g[2], "ic_sup": g[3], "n": g[4], "veredito": ver})
                print(f"D+{etapa} {v}: ganho {g[1] - g[0]:+.3f} (IC {g[2]:+.3f} a {g[3]:+.3f}) {ver}", flush=True)
            except Exception as e:  # noqa
                print("falhou", v, str(e)[:100], flush=True)
        gravar(ch, linhas)
        linhas = []
    print(f"medicao concluida em {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
