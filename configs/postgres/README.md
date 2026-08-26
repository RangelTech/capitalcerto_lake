# postgres

Metadados do Airflow e configuracao propria do Metabase. Nao guarda nenhum dado do lake ou do
warehouse, so o estado interno de cada ferramenta.

## O que tem aqui hoje

`init-multi-db.sh` roda no primeiro boot do container e cria dois bancos com usuarios proprios
dentro da mesma instancia, `airflow` e `metabase`, cada um com sua propria senha tecnica.

## Variaveis de ambiente

`POSTGRES_ADMIN_PASSWORD`, senha do usuario administrador da instancia, usada tambem como senha
do banco `airflow`. `METABASE_DB_PASSWORD`, senha do banco `metabase`, distinta.

## Com quem conversa

So com Airflow e Metabase, cada um lendo e escrevendo so no proprio banco.
