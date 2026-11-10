# Carrega no ClickHouse (analytics_ml) os resultados da bateria local de testes de 10/11/2026 (docs/resultados_bateria/*.csv). Uso: python carregar_bateria.py <pasta_dos_csv>
import os
import sys

import pandas as pd

import credit_features as cf

TABELAS = {
    "motor_matriz_associacao": ("Matriz de correlacao de Spearman entre as variaveis candidatas do motor (formato longo, uma linha por par). Bateria local de 10/11/2026.",
                                "CREATE TABLE analytics_ml.motor_matriz_associacao (variavel_a String COMMENT 'variavel A', variavel_b String COMMENT 'variavel B', spearman Float64 COMMENT 'correlacao de Spearman entre A e B') ENGINE = MergeTree ORDER BY (variavel_a, variavel_b)"),
    "motor_informacao_mutua": ("Informacao mutua de cada variavel com cada alvo (captura relacao nao linear). Bateria local de 10/11/2026.",
                               "CREATE TABLE analytics_ml.motor_informacao_mutua (alvo String COMMENT 'alvo testado (calote, inadimplencia, cluster)', variavel String COMMENT 'variavel candidata', informacao_mutua Float64 COMMENT 'informacao mutua em nats; 0 = sem relacao') ENGINE = MergeTree ORDER BY (alvo, variavel)"),
    "motor_pares_redundantes": ("Pares de variaveis quase iguais (|rho| alto): uma delas pode sair do motor sem perder informacao.",
                                "CREATE TABLE analytics_ml.motor_pares_redundantes (var_a String COMMENT 'variavel A', var_b String COMMENT 'variavel B', rho Float64 COMMENT 'correlacao de Spearman') ENGINE = MergeTree ORDER BY (var_a, var_b)"),
    "motor_combinacoes_condicionadas": ("Combinacoes de 1 a 3 variaveis testadas dentro de cada modo de cobranca (Pix agendado, sem Pix, sem Pix desde jun/26), com taxa do alvo no perfil, teste exato e correcao FDR (Benjamini-Hochberg).",
                                        "CREATE TABLE analytics_ml.motor_combinacoes_condicionadas (estrato String COMMENT 'modo de cobranca onde o perfil foi testado', alvo String COMMENT 'alvo', n_var UInt8 COMMENT 'quantas variaveis no perfil (1 a 3)', variaveis String COMMENT 'variaveis combinadas', perfil String COMMENT 'valores do perfil', n UInt32 COMMENT 'contratos no perfil', taxa Float64 COMMENT 'taxa do alvo no perfil', base_estrato Float64 COMMENT 'taxa do alvo no estrato inteiro', dif Float64 COMMENT 'taxa menos base', p Float64 COMMENT 'p-valor (teste exato de Fisher)', q_fdr Float64 COMMENT 'p corrigido por FDR (q<0,10 = sinal)') ENGINE = MergeTree ORDER BY (estrato, alvo, q_fdr)"),
    "motor_comite_classes": ("AUC fora do tempo de L1, floresta, gradient boosting e comite (media dos ranks), por conjunto de variaveis e por classe de cliente (CPF x CNPJ), no D+0.",
                             "CREATE TABLE analytics_ml.motor_comite_classes (alvo String COMMENT 'alvo', classe String COMMENT 'todas, CPF ou CNPJ', conjunto String COMMENT 'conjunto de variaveis', l1 Nullable(Float64) COMMENT 'AUC logistica L1', rf Nullable(Float64) COMMENT 'AUC floresta', gb Nullable(Float64) COMMENT 'AUC gradient boosting', comite Nullable(Float64) COMMENT 'AUC da media dos ranks', n UInt32 COMMENT 'contratos de teste') ENGINE = MergeTree ORDER BY (alvo, classe, conjunto)"),
}


def main():
    pasta = sys.argv[1]
    ch = cf.conecta()
    ch.command("CREATE DATABASE IF NOT EXISTS analytics_ml")
    dados = {}
    m = pd.read_csv(os.path.join(pasta, "matriz_spearman.csv"), index_col=0)
    dados["motor_matriz_associacao"] = m.stack().reset_index().set_axis(["variavel_a", "variavel_b", "spearman"], axis=1).dropna()
    dados["motor_informacao_mutua"] = pd.read_csv(os.path.join(pasta, "informacao_mutua.csv"))
    dados["motor_pares_redundantes"] = pd.read_csv(os.path.join(pasta, "pares_redundantes.csv"))
    c = pd.read_csv(os.path.join(pasta, "combinacoes_condicionadas.csv"))
    c["perfil"] = c["perfil"].astype(str)
    c["variaveis"] = c["variaveis"].astype(str)
    dados["motor_combinacoes_condicionadas"] = c
    k = pd.read_csv(os.path.join(pasta, "comite_e_classes.csv")).rename(columns={"comite (media dos ranks)": "comite"})
    k["classe"] = k["classe"].astype(str)
    dados["motor_comite_classes"] = k
    for nome, (comentario, ddl) in TABELAS.items():
        ch.command(f"DROP TABLE IF EXISTS analytics_ml.{nome}")
        ch.command(ddl + f" COMMENT '{comentario}'")
        df = dados[nome]
        ch.insert_df(f"analytics_ml.{nome}", df)
        print(nome, len(df), flush=True)


if __name__ == "__main__":
    main()
