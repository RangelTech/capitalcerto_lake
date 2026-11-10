# Dia 0 com POUCAS variaveis (as que passaram no varrimento univariado com FDR), validacao temporal. Compara com o modelo largo.
import sys
import warnings

import numpy as np
import pandas as pd

import credit_experimentos as ex

warnings.filterwarnings("ignore")


def prepara(df):
    d = df.copy()
    d["cep_vazio"] = d["uf_cep"].isna().astype(float)
    d["e_renovacao"] = (d["ordem_contrato_cliente"] > 1).astype(float)
    d["pix_agendado"] = d["pix_agendado"].astype(float)
    return d


CONJUNTOS = {
    "P1 pix_agendado": ([], ["pix_agendado"]),
    "P2 +idade": ([], ["pix_agendado", "idade_na_contratacao"]),
    "P3 +historico anterior": ([], ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao"]),
    "P4 +cep vazio+provedor": (["provedor_pagamento"], ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio"]),
    "P5 +valor/parcela": (["provedor_pagamento"], ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "prev_atraso_max", "e_renovacao", "cep_vazio",
                                                    "valor_principal", "razao_parcela_principal"]),
}


def main():
    df = prepara(pd.read_pickle("/tmp/base_scan2.pkl"))
    linhas = []
    for alvo in ("ye_0", "y_calote_30"):
        H = 45 if alvo == "ye_0" else 30
        for nome, (cats, nums) in CONJUNTOS.items():
            for modelo in ("logistica", "lightgbm"):
                o = ex.avalia_temporal(df, alvo, H, cats, nums, modelo)
                if not len(o):
                    continue
                m = ex.metricas(o)
                linhas.append({"alvo": alvo, "conjunto": nome, "modelo": modelo, **m})
                print(f"{alvo:12s} | {nome:26s} | {modelo:9s} | n={m['n']} taxa={m['taxa']} AUC={m['auc']} [{m['auc_ic_lo']}-{m['auc_ic_hi']}] PR={m['pr_auc']} KS={m['ks']} Brier={m['brier']} (base {m['brier_base']}) lift={m['lift_top_decil']}", flush=True)
    pd.DataFrame(linhas).to_csv("/tmp/motor_parcimonioso.csv", index=False)


if __name__ == "__main__":
    main()
