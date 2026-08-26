#!/usr/bin/env bash
# Atalho pra rodar localmente o mesmo passo que o workflow deploy-vps.yml roda em CI
# (rsync + docker compose up -d), sem passar pelo Terraform completo. Uso pontual/depuracao,
# o caminho normal e sempre via GitHub Actions (ver SPEC.md secao 6, sobre nao rodar
# terraform local sem necessidade).
set -euo pipefail

VPS_HOST="${VPS_HOST:?defina VPS_HOST}"
VPS_DEPLOY_USER="${VPS_DEPLOY_USER:?defina VPS_DEPLOY_USER}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/capital_certo_ed25519}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rsync -az --delete \
  -e "ssh -i $SSH_KEY" \
  "$REPO_ROOT/compose/" "$REPO_ROOT/configs/" "$REPO_ROOT/apps/" "$REPO_ROOT/scripts/" \
  "$VPS_DEPLOY_USER@$VPS_HOST:/opt/capitalcerto/" \
  --exclude ".env"

ssh -i "$SSH_KEY" "$VPS_DEPLOY_USER@$VPS_HOST" \
  "chmod +x /opt/capitalcerto/scripts/*.sh && cd /opt/capitalcerto/compose && docker compose up -d --remove-orphans"
