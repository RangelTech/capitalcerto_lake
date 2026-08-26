# capitalcerto_lake

Infraestrutura do projeto Capital Certo: data lake, orquestracao, BI e motor de score de credito,
rodando em uma VPS unica via Docker Compose, com Terraform automatizando o bootstrap e o deploy.

Este repositorio cobre infraestrutura e arquitetura. Nao cobre o modelo de credito em si, a lista
de variaveis usadas nem o algoritmo, esse conhecimento fica fora do repositorio por decisao de
confidencialidade com o cliente.

Projeto totalmente separado do restante do ecossistema da Rangel Tech. VPS propria, secrets
proprios, sem nenhum servico ou credencial compartilhados com qualquer outro projeto.

## Servicos

| Servico     | Papel                                              | Documentacao                              |
|-------------|-----------------------------------------------------|---------------------------------------------|
| Traefik     | Reverse proxy, entrada unica dos servicos web        | [configs/traefik](configs/traefik/README.md) |
| Postgres    | Metadados do Airflow e do Metabase                    | [configs/postgres](configs/postgres/README.md) |
| MinIO       | Data lake, camadas raw e trusted                      | ver secao de arquitetura abaixo |
| ClickHouse  | Feature Mart, warehouse analitico                     | [configs/clickhouse](configs/clickhouse/README.md) |
| Airflow     | Orquestracao das pipelines de ingestao e transformacao | [configs/airflow](configs/airflow/README.md) |
| Metabase    | Dashboards de BI sobre o Feature Mart                  | [configs/metabase](configs/metabase/README.md) |
| 9Router     | Roteamento de LLM/VLM pra extracao de sinal            | apps/ninerouter |
| Risk API    | Motor de score e decisao de credito                    | [apps/risk-api](apps/risk-api/README.md) |
| Uptime Kuma | Observabilidade basica, uptime e healthcheck           | painel proprio, sem documentacao adicional |

## Arquitetura, em resumo

As fontes (ERP, Chatwoot, bureaus de credito, fotos de estabelecimento) sao extraidas pelo Airflow,
gravadas no MinIO em camadas (raw, depois trusted), transformadas e carregadas no ClickHouse como
Feature Mart. O Metabase le o Feature Mart pra montar dashboards. A Risk API le o Feature Mart pra
calcular o score de um contrato e devolver uma decisao. O Postgres serve so de metadado interno do
Airflow e de configuracao propria do Metabase, nao guarda dado do lake.

Detalhe completo da arquitetura, do fluxo de credenciais e das decisoes tomadas ate aqui esta em
`SPEC.md`, na raiz deste repositorio.

## Subindo a stack pela primeira vez

A VPS ja existe (contratada pelo cliente) e o Terraform trata ela como alvo, sem provisionar
maquina nova. O fluxo completo:

1. Preencher os secrets do repositorio no GitHub Actions (lista completa em `SPEC.md`, secao 6).
2. Rodar o workflow "Terraform Apply" (automatico em push na branch principal, ou manual).
3. O workflow instala Docker, firewall e fail2ban na VPS, cria o usuario de deploy, sobe a stack
   inteira via Docker Compose e cadastra a conexao do Metabase com o ClickHouse.

Para rodar localmente em vez de via GitHub Actions, ver `terraform/README.md`.

## Fazendo deploy de uma mudanca pontual

Se so mudou codigo de um servico (por exemplo, a Risk API) ou um arquivo de configuracao, sem
mudar versao de imagem nem credencial, roda o workflow "Deploy Stack Only" em vez do Terraform
Apply completo. Ele so sincroniza os arquivos mudados e reinicia os containers que precisam
reiniciar, o Docker Compose decide sozinho quais, o resto da stack continua no ar.

## Onde ficam os segredos e como cadastrar um novo

Nenhuma credencial real fica neste repositorio. A fonte de verdade sao os GitHub Actions Secrets
do proprio repositorio (aba Settings, Secrets and variables, Actions). Para cadastrar um segredo
novo:

1. Adicionar o secret no GitHub Actions com o nome que vai ser referenciado.
2. Adicionar a variavel correspondente em `terraform/variables.tf`.
3. Referenciar o secret nos workflows que precisam dela (`.github/workflows/`), como
   `TF_VAR_<nome>`.
4. Se o valor precisa estar disponivel dentro dos containers, adicionar em `compose/.env.tftpl` e
   usar a variavel dentro do `docker-compose.yml`.

Fluxo completo, com diagrama, esta em `SPEC.md`, secao 6.

Este projeto segue uma regra permanente: nenhuma credencial e trocada ou rotacionada sem
autorizacao explicita do responsavel pelo projeto, mesmo diante de suspeita de exposicao.
