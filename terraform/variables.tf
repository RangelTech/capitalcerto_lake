# A VPS já existe (contratada e provisionada pelo cliente Capital Certo, ver SPEC.md seção 1).
# Este módulo não cria a máquina, trata o IP abaixo como alvo de bootstrap e deploy.

variable "server_ip" {
  description = "IP público da VPS já provisionada"
  type        = string
}

variable "initial_ssh_user" {
  description = "Usuário usado só na primeira execução, antes do usuário de deploy existir"
  type        = string
  default     = "root"
}

variable "initial_ssh_password" {
  description = "Senha inicial da VPS, usada só na primeira execução do bootstrap. Nunca rotacionar sem autorização explícita, mesmo suspeitando de exposição (ver SPEC.md secao 9)."
  type        = string
  sensitive   = true
}

variable "deploy_user" {
  description = "Usuário dedicado criado pelo bootstrap para rodar o deploy no dia a dia, no lugar de root"
  type        = string
  default     = "deploy"
}

variable "public_ssh_key" {
  description = "Chave pública instalada no usuário de deploy"
  type        = string
}

variable "ssh_private_key_path" {
  description = "Caminho local da chave privada correspondente, usada pelo provisioner"
  type        = string
}

variable "timezone" {
  description = "Timezone dos containers"
  type        = string
  default     = "America/Sao_Paulo"
}

# --- Domínio (futuro, ver SPEC.md secao 7) ---

variable "root_domain" {
  description = "Domínio raiz, vazio ate o cliente definir um. Traefik roteia por IP ate la."
  type        = string
  default     = ""
}

variable "letsencrypt_email" {
  description = "E-mail para o Let's Encrypt, so usado quando root_domain estiver preenchido"
  type        = string
  default     = ""
}

# --- Usuário administrativo único dos paineis (SPEC.md secao 6.1) ---

variable "shared_admin_user" {
  description = "Login administrativo unico usado nos paineis (MinIO console, ClickHouse, Airflow, Metabase, Traefik dashboard). Metabase exige formato de e-mail, ver SPEC.md secao 6.1."
  type        = string
}

variable "shared_admin_password" {
  description = "Senha administrativa unica usada nos mesmos paineis do shared_admin_user"
  type        = string
  sensitive   = true
}

# --- Credenciais tecnicas, uma por servico, nunca compartilhadas entre si ---

variable "postgres_version" {
  type    = string
  default = "16"
}

variable "postgres_admin_password" {
  type      = string
  sensitive = true
}

variable "minio_version" {
  type    = string
  default = "latest"
}

variable "minio_root_password" {
  description = "Senha do console MinIO. Reaproveita shared_admin_password por decisao do cliente (SPEC.md secao 6.1)."
  type        = string
  sensitive   = true
}

variable "minio_buckets" {
  description = "Buckets criados no primeiro boot do MinIO"
  type        = list(string)
  default     = ["raw", "trusted", "analytics"]
}

variable "clickhouse_version" {
  type    = string
  default = "latest"
}

variable "clickhouse_password" {
  type      = string
  sensitive = true
}

variable "airflow_version" {
  type    = string
  default = "2.9.3"
}

variable "airflow_fernet_key" {
  description = "Chave tecnica de criptografia interna do Airflow, distinta do login administrativo"
  type        = string
  sensitive   = true
}

variable "metabase_version" {
  type    = string
  default = "latest"
}

variable "metabase_db_password" {
  description = "Senha tecnica da conexao Metabase -> Postgres, distinta do login administrativo do Metabase"
  type        = string
  sensitive   = true
}

variable "ninerouter_version" {
  type    = string
  default = "latest"
}

variable "ninerouter_api_keys" {
  description = "Chaves de API dos provedores de LLM/VLM roteados pelo 9Router"
  type        = string
  sensitive   = true
  default     = ""
}

variable "risk_api_secret_key" {
  description = "Chave tecnica interna da Risk API (assinatura de token, etc), nao e login humano"
  type        = string
  sensitive   = true
}

variable "uptime_kuma_version" {
  type    = string
  default = "latest"
}

variable "traefik_version" {
  type    = string
  default = "v3.1"
}
