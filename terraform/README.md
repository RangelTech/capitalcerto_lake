# terraform

A VPS ja existe, contratada e provisionada pelo cliente. Este modulo nao cria maquina nenhuma,
trata o IP dela como alvo fixo e automatiza tres coisas: bootstrap do sistema operacional, render
das credenciais em `compose/.env` e deploy da stack via Docker Compose.

## Arquivos

`main.tf` tem os tres recursos principais: `local_file.compose_env` (renderiza o `.env`),
`null_resource.bootstrap` (roda `cloud-init/bootstrap.sh` via SSH) e `null_resource.deploy`
(sincroniza os arquivos e sobe a stack). `variables.tf` declara todas as variaveis, com
comentario em cada uma explicando o que e. `dns.tf` fica vazio ate existir dominio.
`terraform.tfvars.example` mostra o formato esperado, nunca usar valor real ali.

## Rodando via GitHub Actions (caminho normal)

Cada variavel do `terraform.tfvars` vira um secret no GitHub Actions, injetado como
`TF_VAR_<nome>` nos workflows `terraform-plan.yml` e `terraform-apply.yml`. Lista completa de
secrets necessarios em `SPEC.md`, secao 6, na raiz do repositorio.

## Rodando local (so pra depuracao pontual)

```
cd terraform
cp terraform.tfvars.example terraform.tfvars
# preencher terraform.tfvars com valores reais
terraform init
terraform plan
terraform apply
```

Depois de rodar local, apagar o `terraform.tfstate` gerado, ele fica com as credenciais
renderizadas em texto plano (ver `SPEC.md`, secao 6, sobre o Terraform state).

## Sobre nao ter backend remoto de state

Cada `apply` reprocessa o bootstrap e o deploy inteiros, porque o Terraform nao guarda memoria
entre execucoes (sem backend remoto configurado, ver `SPEC.md`, secao 6). Isso e seguro porque
`cloud-init/bootstrap.sh` e o passo de deploy sao idempotentes, mas significa que `terraform plan`
sempre mostra tudo como "a criar", nunca um diff real.
