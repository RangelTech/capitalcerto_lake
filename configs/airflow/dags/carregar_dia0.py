# Carrega no ClickHouse o estudo focado do DIA 0 (docs/bateria_dia0.py, 10/11/2026) e o teste exploratorio da configuracao de agendamento (credit_dia0_estudo.py).
# Uso: python carregar_dia0.py <dia0_modelos.csv> [dia0_estudo.csv]
import sys

import pandas as pd

import credit_features as cf


def main():
    ch = cf.conecta()
    m = pd.read_csv(sys.argv[1])
    for c in m.columns:
        if m[c].dtype == object:
            m[c] = m[c].astype(str)
    ch.command("DROP TABLE IF EXISTS analytics_ml.motor_dia0_modelos")
    ch.command("CREATE TABLE analytics_ml.motor_dia0_modelos (estrato String COMMENT 'em quais contratos o modelo foi treinado e testado (todos, sem Pix, canal...)', alvo String COMMENT 'calote ate o dia 45 ou em 30 dias', "
               "conjunto String COMMENT 'informacoes usadas no dia 0', melhor_algoritmo String COMMENT 'algoritmo com maior acerto', auc Float64 COMMENT 'acerto fora do tempo do melhor algoritmo (0,5 = chute)', "
               "auc_comite Float64 COMMENT 'acerto do comite (media de todos os algoritmos)', auc_l1 Float64 COMMENT 'acerto da logistica com selecao', auc_rf Float64 COMMENT 'acerto da floresta aleatoria', "
               "n UInt32 COMMENT 'contratos de teste', taxa Float64 COMMENT 'taxa de calote nos contratos de teste') ENGINE = MergeTree ORDER BY (estrato, alvo, conjunto) "
               "COMMENT 'Estudo focado do dia 0 (assinatura): acerto fora do tempo por modo de cobranca e por conjunto de informacoes. Resultado: so o canal de cobranca (provedor) ja da o maximo, 0,65 a 0,67; acrescentar produto curto, tom, endereco dividido ou valor sobre faturamento nao melhora. 10/11/2026.'")
    ch.insert_df("analytics_ml.motor_dia0_modelos", m)
    print("motor_dia0_modelos", len(m), flush=True)
    if len(sys.argv) > 2:
        e = pd.read_csv(sys.argv[2])
        for c in e.columns:
            if e[c].dtype == object:
                e[c] = e[c].astype(str)
        ch.command("DROP TABLE IF EXISTS analytics_ml.motor_dia0_agendamento")
        ch.command("CREATE TABLE analytics_ml.motor_dia0_agendamento (estrato String COMMENT 'todos, Pix agendado ou sem Pix', alvo String COMMENT 'o que se tenta prever', variavel String COMMENT 'configuracao do agendamento ou momento da contratacao', "
                   "n UInt32 COMMENT 'contratos', auc Float64 COMMENT 'AUC da variavel contra o alvo', p Float64 COMMENT 'p-valor Mann-Whitney', media_calote Float64 COMMENT 'media da variavel nos contratos com calote', "
                   "media_nao Float64 COMMENT 'media da variavel nos demais', q_fdr Nullable(Float64) COMMENT 'p corrigido por FDR') ENGINE = MergeTree ORDER BY (estrato, alvo, p) "
                   "COMMENT 'Teste exploratorio do dia 0: configuracao do agendamento de cobranca (dia da semana, parcelas agendadas, pedidos, canal) e momento da contratacao contra calote. Cuidado: parcelas agendadas e numero de pedidos podem ja embutir o desfecho (vazamento); so provedor e numero de parcelas sao seguros. 10/11/2026.'")
        ch.insert_df("analytics_ml.motor_dia0_agendamento", e)
        print("motor_dia0_agendamento", len(e), flush=True)


def carregar_auditoria(caminho):
    ch = cf.conecta()
    a = pd.read_csv(caminho)
    for c in a.columns:
        if a[c].dtype == object:
            a[c] = a[c].astype(str)
    ch.command("DROP TABLE IF EXISTS analytics_ml.motor_dia0_auditoria")
    ch.command("CREATE TABLE analytics_ml.motor_dia0_auditoria (estrato String COMMENT 'todos, Pix agendado ou sem Pix', alvo String COMMENT 'o que se tenta prever', variavel String COMMENT 'evento da trilha de auditoria do CredHub nas primeiras 6 ou 24 horas (aud6h_ ou aud24h_) e quantas vezes ocorreu', "
               "n UInt32 COMMENT 'contratos', n_com_evento UInt32 COMMENT 'contratos com o evento', auc Float64 COMMENT 'AUC da contagem do evento contra o alvo', p Float64 COMMENT 'p-valor', calote_com_evento Nullable(Float64) COMMENT 'taxa de calote nos contratos com o evento', "
               "calote_sem_evento Nullable(Float64) COMMENT 'taxa de calote nos contratos sem o evento', q_fdr Nullable(Float64) COMMENT 'p corrigido por FDR') ENGINE = MergeTree ORDER BY (estrato, alvo, p) "
               "COMMENT 'Eventos de pre-autorizacao, QR e diagnostico do agendamento de cobranca (Efi e Pluggy) nas primeiras horas do contrato contra calote. Conclusao (10/11/2026): repetem a informacao do canal de cobranca (provedor), nao acrescentam. Dentro do canal Pluggy sem Pix: 13% de calote com pre-autorizacao, 0% sem (n=23).'")
    ch.insert_df("analytics_ml.motor_dia0_auditoria", a)
    print("motor_dia0_auditoria", len(a), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 3:
        carregar_auditoria(sys.argv[3])
    main()
