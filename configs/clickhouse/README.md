# clickhouse

Feature Mart, warehouse analitico que alimenta a Risk API e o Metabase.

## O que tem aqui hoje

`init/001_feature_mart.sql` roda no primeiro boot e so cria o banco `feature_mart`, ainda sem
tabela. O schema definitivo entra conforme o estudo do modelo avanca e as features candidatas
ficam definidas (ver skill `capitalcerto`, secao 5, e `SPEC.md`, secao 3.1).

## Como o dado chega aqui

O ClickHouse nao le o MinIO direto por padrao, quem move o dado e o Airflow: extrai da fonte,
grava no MinIO, transforma e carrega no ClickHouse. Usar o motor de tabela S3 do ClickHouse pra
ler o MinIO direto e possivel, mas fica em aberto pra uma versao futura, nao esta na V1.

## Variaveis de ambiente

`CLICKHOUSE_VERSION`, versao da imagem. `CLICKHOUSE_PASSWORD`, credencial tecnica de acesso ao
banco, usada por Airflow, Metabase e Risk API pra se conectar, distinta do login administrativo.

## Com quem conversa

Recebe carga do Airflow. E lido pela Risk API (rota de score) e pelo Metabase (fonte de dado dos
dashboards, cadastrada via `scripts/bootstrap-metabase.sh`, nao manualmente).
