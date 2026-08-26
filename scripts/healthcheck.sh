#!/usr/bin/env bash
# Checa se cada servico esta respondendo, roda na propria VPS depois do deploy.
set -uo pipefail

check() {
  local name="$1"
  local url="$2"
  if curl -sf -o /dev/null "$url"; then
    echo "OK   $name"
  else
    echo "FAIL $name ($url)"
  fi
}

check "traefik"     "http://localhost:8080/api/rawdata"
check "minio"        "http://localhost:9000/minio/health/live"
check "clickhouse"   "http://localhost:8123/ping"
check "airflow"      "http://localhost:8081/health"
check "metabase"     "http://localhost:3000/api/health"
check "risk-api"     "http://localhost:8000/health"
check "uptime-kuma"  "http://localhost:3001"
check "ninerouter"   "http://localhost:4000/health"
