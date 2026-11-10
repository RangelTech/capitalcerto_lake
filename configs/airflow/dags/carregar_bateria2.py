# Carrega no ClickHouse (analytics_ml) os resultados da bateria local 2 de 10/11/2026 (docs/bateria2_local.py): combinacoes de 1 a 4 variaveis, matriz de modelos,
# efeito ajustado por Pix/provedor/mes e sazonalidade controlada. Uso: python carregar_bateria2.py <pasta_dos_csv>
import os
import sys

import pandas as pd

import credit_features as cf

TABELAS = {
    "motor_combinacoes_1a4": (
        "Combinacoes de 1 a 4 variaveis testadas dentro de cada modo de cobranca (Pix agendado, sem Pix, todos): taxa do alvo no perfil contra a taxa do estrato, teste exato e correcao FDR (q<0,10 = sinal). Inclui tom da conversa, endereco dividido, valor/faturamento e cluster do contrato anterior. Bateria 2 de 10/11/2026.",
        "CREATE TABLE analytics_ml.motor_combinacoes_1a4 (estrato String COMMENT 'modo de cobranca onde o perfil foi testado', alvo String COMMENT 'o que se tenta prever (calote, inadimplencia, cluster)', "
        "n_var UInt8 COMMENT 'quantas variaveis no perfil (1 a 4)', variaveis String COMMENT 'variaveis combinadas', perfil String COMMENT 'valores do perfil', n UInt32 COMMENT 'contratos no perfil', "
        "taxa Float64 COMMENT 'taxa do alvo no perfil', base_estrato Float64 COMMENT 'taxa do alvo no estrato inteiro', dif Float64 COMMENT 'taxa menos base', p Float64 COMMENT 'p-valor', "
        "q_fdr Float64 COMMENT 'p corrigido por FDR (q<0,10 = sinal)') ENGINE = MergeTree ORDER BY (estrato, alvo, q_fdr)"),
    "motor_matriz_modelos": (
        "Matriz de modelos: AUC fora do tempo de 11 algoritmos e do comite, por conjunto de variaveis, alvo e estrato (todos ou sem Pix). Validacao temporal, 5 meses. Bateria 2 de 10/11/2026.",
        "CREATE TABLE analytics_ml.motor_matriz_modelos (estrato String COMMENT 'todos ou sem Pix', alvo String COMMENT 'o que se tenta prever', conjunto String COMMENT 'conjunto de variaveis', "
        "algoritmo String COMMENT 'algoritmo ou comite', auc Float64 COMMENT 'acerto fora do tempo (0,5 = chute, 1 = perfeito)', n UInt32 COMMENT 'contratos de teste') "
        "ENGINE = MergeTree ORDER BY (estrato, alvo, conjunto, algoritmo)"),
    "motor_efeito_ajustado": (
        "Efeito de cada variavel no alvo controlando Pix agendado, provedor e mes de inicio (regressao logistica). odds_ratio_por_dp = quanto a chance multiplica a cada 1 desvio padrao da variavel. Bateria 2 de 10/11/2026.",
        "CREATE TABLE analytics_ml.motor_efeito_ajustado (alvo String COMMENT 'o que se tenta prever', variavel String COMMENT 'variavel testada', n UInt32 COMMENT 'contratos usados', "
        "odds_ratio_por_dp Float64 COMMENT 'maior que 1 = aumenta o risco, menor que 1 = protege', ic95_inf Float64 COMMENT 'limite inferior do IC 95%', ic95_sup Float64 COMMENT 'limite superior do IC 95%', "
        "p Float64 COMMENT 'p-valor', q_fdr Float64 COMMENT 'p corrigido por FDR') ENGINE = MergeTree ORDER BY (alvo, p)"),
    "motor_sazonalidade_controlada": (
        "Teste de sazonalidade (dia da semana, semana do mes, mes de inicio) controlando provedor e, quando aplicavel, Pix. p_lr = teste de razao de verossimilhanca. Bateria 2 de 10/11/2026.",
        "CREATE TABLE analytics_ml.motor_sazonalidade_controlada (estrato String COMMENT 'sem Pix ou todos (controla Pix)', alvo String COMMENT 'o que se tenta prever', efeito String COMMENT 'tipo de sazonalidade', "
        "n UInt32 COMMENT 'contratos usados', p_lr Float64 COMMENT 'p-valor do teste (menor que 0,05 = o efeito existe)') ENGINE = MergeTree ORDER BY (alvo, efeito)"),
}
ARQ = {"motor_combinacoes_1a4": "combinacoes_1a4.csv", "motor_matriz_modelos": "matriz_modelos.csv", "motor_efeito_ajustado": "efeito_ajustado.csv", "motor_sazonalidade_controlada": "sazonalidade_controlada.csv"}


def main():
    pasta = sys.argv[1]
    ch = cf.conecta()
    for nome, (comentario, ddl) in TABELAS.items():
        df = pd.read_csv(os.path.join(pasta, ARQ[nome]))
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].astype(str)
        ch.command(f"DROP TABLE IF EXISTS analytics_ml.{nome}")
        ch.command(ddl + f" COMMENT '{comentario}'")
        for i in range(0, len(df), 100000):
            ch.insert_df(f"analytics_ml.{nome}", df.iloc[i:i + 100000])
        print(nome, len(df), flush=True)


if __name__ == "__main__":
    main()
