#!/usr/bin/env bash
# Cria a conexao do Metabase com o ClickHouse via API, depois do primeiro boot (ver SPEC.md
# secao 3.2, Metabase nao aceita essa conexao via env var). Mesma receita ja usada nos scripts
# do case MindLab (build_metabase_score_municipios.py e afins).
set -euo pipefail

METABASE_URL="${METABASE_URL:-http://localhost:3000}"
METABASE_USER="${SHARED_ADMIN_USER:?}"
METABASE_PASSWORD="${SHARED_ADMIN_PASSWORD:?}"
CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-clickhouse}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:?}"

echo "==> Autenticando no Metabase"
SESSION_ID=$(curl -sf -X POST "$METABASE_URL/api/session" \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$METABASE_USER\", \"password\": \"$METABASE_PASSWORD\"}" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

echo "==> Verificando se a fonte de dado ClickHouse ja existe"
EXISTS=$(curl -sf "$METABASE_URL/api/database" -H "X-Metabase-Session: $SESSION_ID" \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print('yes' if any(db['engine'] == 'clickhouse' for db in d.get('data', [])) else 'no')")

if [ "$EXISTS" = "yes" ]; then
  echo "==> Fonte de dado ClickHouse ja cadastrada, nada a fazer"
  exit 0
fi

echo "==> Cadastrando fonte de dado ClickHouse (Feature Mart)"
curl -sf -X POST "$METABASE_URL/api/database" \
  -H "Content-Type: application/json" \
  -H "X-Metabase-Session: $SESSION_ID" \
  -d "{
    \"name\": \"Feature Mart (ClickHouse)\",
    \"engine\": \"clickhouse\",
    \"details\": {
      \"host\": \"$CLICKHOUSE_HOST\",
      \"port\": 8123,
      \"user\": \"default\",
      \"password\": \"$CLICKHOUSE_PASSWORD\",
      \"dbname\": \"feature_mart\"
    }
  }" > /dev/null

echo "==> Concluido"
