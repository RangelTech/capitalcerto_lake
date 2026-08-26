# traefik

Reverse proxy e entrada unica da stack.

## O que faz hoje

Sem dominio configurado, o dashboard do Traefik e cada servico web ficam acessiveis direto por
IP e porta, sem TLS. `dynamic.yml` esta vazio de proposito, os roteadores por subdominio e o
resolver ACME do Let's Encrypt entram ali quando o cliente definir um dominio.

## Variaveis de ambiente

`TRAEFIK_VERSION`, versao da imagem. `TRAEFIK_BASIC_AUTH_USER` e `TRAEFIK_BASIC_AUTH_PASSWORD`,
login do dashboard, reaproveitam o usuario administrativo unico (ver `SPEC.md`, secao 6.1).

## Quando o dominio entrar

Preencher `root_domain` e `letsencrypt_email` nas variaveis do Terraform, declarar os registros
DNS em `terraform/dns.tf` e adicionar os roteadores por subdominio em `dynamic.yml`. Detalhe
completo em `SPEC.md`, secao 7.
