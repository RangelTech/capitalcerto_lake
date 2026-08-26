#!/usr/bin/env bash
# Roda uma vez como root na VPS (via Terraform, ver terraform/main.tf).
# Deixa a maquina pronta pra rodar a stack: Docker, firewall, fail2ban, usuario de deploy.
# Idempotente: pode rodar de novo sem quebrar nada que ja exista.

set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
PUBLIC_KEY="${PUBLIC_KEY:-}"

if [ -z "$PUBLIC_KEY" ]; then
  echo "PUBLIC_KEY nao foi definida, abortando" >&2
  exit 1
fi

echo "==> Atualizando pacotes"
apt-get update -y
apt-get upgrade -y

echo "==> Instalando Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable docker
systemctl start docker

echo "==> Instalando firewall (ufw) e fail2ban"
apt-get install -y ufw fail2ban
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
systemctl enable fail2ban
systemctl start fail2ban

echo "==> Criando usuario de deploy ($DEPLOY_USER)"
if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G docker,sudo "$DEPLOY_USER"
fi
mkdir -p "/home/$DEPLOY_USER/.ssh"
echo "$PUBLIC_KEY" >> "/home/$DEPLOY_USER/.ssh/authorized_keys"
sort -u -o "/home/$DEPLOY_USER/.ssh/authorized_keys" "/home/$DEPLOY_USER/.ssh/authorized_keys"
chmod 700 "/home/$DEPLOY_USER/.ssh"
chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"

echo "==> Preparando diretorio da aplicacao"
mkdir -p /opt/capitalcerto
chown -R "$DEPLOY_USER:$DEPLOY_USER" /opt/capitalcerto

echo "==> Bootstrap concluido. Deploy a partir daqui usa o usuario $DEPLOY_USER, nao root."
echo "==> Desabilitar login de root por senha fica a criterio do cliente (ver SPEC.md secao 9, item 1: nunca rotacionar/alterar credencial sem autorizacao explicita)."
