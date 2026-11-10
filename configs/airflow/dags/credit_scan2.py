# Varrimento v2: junta ao base_scan (cadastro, geografia, bureau, nome, chat) o CNPJ/Receita corrigido e o quadro societario com a rede de clientes ligados.
import sys
import warnings

import numpy as np
import pandas as pd

import credit_features as cf
import credit_rede as rd
import credit_scan as sc

warnings.filterwarnings("ignore")
ALVOS = ("ye_0", "y_calote_15", "y_calote_30", "y_calote_45")


def main():
    ch = cf.conecta()
    df = pd.read_pickle("/tmp/base_scan.pkl")
    df = df.drop(columns=[c for c in df.columns if c.startswith("rfb_")])  # a versao anterior usava nomes de coluna errados
    parc = cf._parcelas(ch)
    novas = rd.features_cnpj_rede(ch, df, parc)
    df = df.merge(novas, on="contrato_id", how="left")
    df.to_pickle("/tmp/base_scan2.pkl")
    print("novas variaveis:", [c for c in novas.columns if c != "contrato_id"], flush=True)
    print("cobertura rfb_tem:", round(float(df.rfb_tem.mean()), 3), "| com vinculados:", int((df.r_vinculados > 0).sum()), "| com historico de vinculado:", int((df.r_vinculados_com_historico > 0).sum()), flush=True)
    num, cat = sc.candidatas(df)
    print(f"{len(df)} contratos | {len(num)} numericas, {len(cat)} categoricas", flush=True)
    todos = []
    for a in ALVOS:
        r = sc.testes(df, a, num, cat)
        todos.append(r)
        sig = r[r["q_fdr"] < 0.10].sort_values("p")
        print(f"\n=== alvo {a}: {len(r)} testes, n={int(df[a].notna().sum())}, {len(sig)} com q<0.10", flush=True)
        for x in sig.head(20).itertuples():
            print(f"  {x.variavel} [{x.nivel}] n={x.n} efeito={x.efeito:+.3f} p={x.p:.4f} q={x.q_fdr:.3f} antigo={x.auc_antigo:+.3f} novo={x.auc_novo:+.3f} estavel={x.estavel}", flush=True)
        g = r[r["variavel"].str.startswith(("rfb_", "r_"))].sort_values("p")
        print(f"  -- CNPJ/Receita/socios/rede: {len(g)} testes; melhores p:", [(x.variavel, x.nivel[:14], round(x.p, 3), x.n) for x in g.head(6).itertuples()], flush=True)
    pd.concat(todos).to_pickle("/tmp/motor_scan2.pkl")


if __name__ == "__main__":
    main()
