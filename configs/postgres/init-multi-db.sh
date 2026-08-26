#!/usr/bin/env bash
# Roda no primeiro boot do container Postgres (docker-entrypoint-initdb.d). Cria os bancos
# e usuarios tecnicos separados que Airflow e Metabase usam dentro da mesma instancia
# (ver SPEC.md secao 3, "Postgres: metadados").
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE USER airflow WITH PASSWORD '$POSTGRES_ADMIN_PASSWORD';
    CREATE DATABASE airflow OWNER airflow;

    CREATE USER metabase WITH PASSWORD '$METABASE_DB_PASSWORD';
    CREATE DATABASE metabase OWNER metabase;
EOSQL
