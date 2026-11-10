# Carrega no ClickHouse os resultados da bateria 3 (matriz de modelos POR IDADE do contrato) e do teste de tom da conversa por LLM.
# Uso: python carregar_bateria3.py <pasta_bateria3> <tom_resultado.csv>
import os
import sys

import pandas as pd

import credit_features as cf

T = {
    "motor_matriz_por_idade": (
        "Matriz de modelos por idade do contrato (dia 1 a 14): AUC fora do tempo de 8 algoritmos e do comite, em 6 conjuntos de informacoes (so comportamento, + cadastro, + endereco dividido, + contrato anterior, + tom, tudo), para calote e inadimplencia. Bateria 3 de 10/11/2026.",
        "CREATE TABLE analytics_ml.motor_matriz_por_idade (idade UInt8 COMMENT 'dia do contrato em que o modelo avalia', alvo String COMMENT 'calote ou inadimplencia', conjunto String COMMENT 'conjunto de informacoes', "
        "algoritmo String COMMENT 'algoritmo ou comite', auc Float64 COMMENT 'acerto fora do tempo (0,5 = chute, 1 = perfeito)', n UInt32 COMMENT 'contratos de teste') ENGINE = MergeTree ORDER BY (idade, alvo, conjunto, algoritmo)",
        "matriz_por_idade.csv"),
    "motor_ganho_por_idade": (
        "Ganho de acerto de cada conjunto de informacoes sobre so comportamento de pagamento, por idade do contrato, com intervalo de bootstrap. significativo = 1 quando o IC de 95% fica acima de zero. Regra do motor: so entra informacao nova com ganho significativo. Bateria 3 de 10/11/2026.",
        "CREATE TABLE analytics_ml.motor_ganho_por_idade (idade UInt8 COMMENT 'dia do contrato', alvo String COMMENT 'calote ou inadimplencia', conjunto String COMMENT 'conjunto de informacoes comparado', "
        "algoritmo String COMMENT 'algoritmo', ganho_auc Float64 COMMENT 'AUC do conjunto menos AUC de so comportamento', ic95_inf Float64 COMMENT 'limite inferior do IC 95% do ganho', ic95_sup Float64 COMMENT 'limite superior do IC 95% do ganho', "
        "significativo UInt8 COMMENT '1 = o ganho e real (IC 95% acima de zero)') ENGINE = MergeTree ORDER BY (idade, alvo, conjunto, algoritmo)",
        "ganho_sobre_comportamento.csv"),
    "motor_tom_testes": (
        "Tom da conversa lido por LLM (12 perguntas sim/nao, 654 contratos) contra calote, inadimplencia e cluster ruim: AUC por pergunta, estrato, p-valor, FDR e estabilidade nas duas metades do tempo. auc abaixo de 0,5 = quem tem mais disso da MENOS calote.",
        "CREATE TABLE analytics_ml.motor_tom_testes (alvo String COMMENT 'o que se tenta prever', estrato String COMMENT 'todos, sem Pix ou renovacao', variavel String COMMENT 'pergunta de tom (tom_*)', n UInt32 COMMENT 'contratos', "
        "auc Nullable(Float64) COMMENT 'AUC da variavel contra o alvo', p Nullable(Float64) COMMENT 'p-valor Mann-Whitney', auc_1a_metade Nullable(Float64) COMMENT 'AUC na metade antiga do tempo', auc_2a_metade Nullable(Float64) COMMENT 'AUC na metade nova do tempo', "
        "q_fdr Nullable(Float64) COMMENT 'p corrigido por FDR', estavel UInt8 COMMENT '1 = mesma direcao nas duas metades') ENGINE = MergeTree ORDER BY (alvo, estrato, variavel)",
        "tom"),
}


def main():
    pasta, tomcsv = sys.argv[1], sys.argv[2]
    ch = cf.conecta()
    for nome, (com, ddl, arq) in T.items():
        df = pd.read_csv(tomcsv if arq == "tom" else os.path.join(pasta, arq))
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].astype(str)
        if arq == "tom":
            df["estavel"] = df["estavel"].astype(int)
        ch.command(f"DROP TABLE IF EXISTS analytics_ml.{nome}")
        ch.command(ddl + f" COMMENT '{com}'")
        ch.insert_df(f"analytics_ml.{nome}", df)
        print(nome, len(df), flush=True)


if __name__ == "__main__":
    main()
