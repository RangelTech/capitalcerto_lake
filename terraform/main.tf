# A VPS ja existe (Contabo, contratada pelo cliente), entao este modulo nao provisiona maquina.
# Ele trata var.server_ip como alvo fixo e faz tres coisas:
#   1. renderiza compose/.env a partir das variaveis do Terraform;
#   2. via SSH, roda cloud-init/bootstrap.sh (docker, firewall, fail2ban, usuario de deploy);
#   3. envia compose/ configs/ apps/ scripts/ para a VPS e sobe a stack com docker compose.
#
# Sem backend remoto de state (ver SPEC.md secao 6): cada apply reprocessa tudo, e isso e seguro
# porque bootstrap.sh e o passo de deploy sao idempotentes por design.

locals {
  remote_base_dir = "/opt/capitalcerto"
}

resource "local_file" "compose_env" {
  filename = "${path.module}/../compose/.env"
  file_permission = "0600"

  content = templatefile("${path.module}/../compose/.env.tftpl", {
    timezone = var.timezone

    root_domain       = var.root_domain
    letsencrypt_email = var.letsencrypt_email

    shared_admin_user     = var.shared_admin_user
    shared_admin_password = replace(var.shared_admin_password, "$", "$$")

    postgres_version        = var.postgres_version
    postgres_admin_password = replace(var.postgres_admin_password, "$", "$$")

    minio_version        = var.minio_version
    minio_root_password  = replace(var.minio_root_password, "$", "$$")
    minio_buckets        = join(",", var.minio_buckets)

    clickhouse_version  = var.clickhouse_version
    clickhouse_password = replace(var.clickhouse_password, "$", "$$")

    airflow_version    = var.airflow_version
    airflow_fernet_key = replace(var.airflow_fernet_key, "$", "$$")

    metabase_version      = var.metabase_version
    metabase_db_password  = replace(var.metabase_db_password, "$", "$$")

    ninerouter_version  = var.ninerouter_version
    ninerouter_api_keys = replace(var.ninerouter_api_keys, "$", "$$")

    risk_api_secret_key = replace(var.risk_api_secret_key, "$", "$$")

    uptime_kuma_version = var.uptime_kuma_version
    traefik_version     = var.traefik_version
  })
}

resource "null_resource" "bootstrap" {
  triggers = {
    bootstrap_script_sha = filesha256("${path.module}/../cloud-init/bootstrap.sh")
    deploy_user          = var.deploy_user
    public_key           = var.public_ssh_key
    server_ip            = var.server_ip
  }

  connection {
    type        = "ssh"
    host        = var.server_ip
    user        = var.initial_ssh_user
    password    = var.initial_ssh_password
    private_key = null
  }

  provisioner "file" {
    source      = "${path.module}/../cloud-init/bootstrap.sh"
    destination = "/tmp/bootstrap.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "chmod +x /tmp/bootstrap.sh",
      "DEPLOY_USER=${var.deploy_user} PUBLIC_KEY='${var.public_ssh_key}' /tmp/bootstrap.sh",
    ]
  }
}

resource "null_resource" "deploy" {
  depends_on = [null_resource.bootstrap, local_file.compose_env]

  triggers = {
    env_sha     = local_file.compose_env.content_md5
    compose_sha = filesha256("${path.module}/../compose/docker-compose.yml")
  }

  connection {
    type        = "ssh"
    host        = var.server_ip
    user        = var.deploy_user
    private_key = file(var.ssh_private_key_path)
  }

  provisioner "local-exec" {
    # Sem barra final nas pastas de origem: rsync cria a subpasta (compose/, configs/, ...)
    # no destino em vez de espalhar o conteudo direto em remote_base_dir.
    command = <<-EOT
      rsync -az --delete \
        -e "ssh -i ${var.ssh_private_key_path} -o StrictHostKeyChecking=accept-new" \
        ${path.module}/../compose ${path.module}/../configs ${path.module}/../apps ${path.module}/../scripts \
        ${var.deploy_user}@${var.server_ip}:${local.remote_base_dir}/ \
        --exclude ".env"
      rsync -az \
        -e "ssh -i ${var.ssh_private_key_path} -o StrictHostKeyChecking=accept-new" \
        ${path.module}/../compose/.env \
        ${var.deploy_user}@${var.server_ip}:${local.remote_base_dir}/compose/.env
    EOT
  }

  provisioner "remote-exec" {
    inline = [
      "chmod +x ${local.remote_base_dir}/scripts/*.sh",
      "cd ${local.remote_base_dir}/compose && docker compose up -d --remove-orphans",
    ]
  }
}
