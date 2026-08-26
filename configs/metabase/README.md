# metabase

BI, dashboards sobre o Feature Mart do ClickHouse.

## Como a conexao com o ClickHouse e criada

O Metabase nao aceita cadastrar uma fonte de dado externa via variavel de ambiente, so a propria
configuracao dele (banco de metadado). A conexao com o ClickHouse e criada por
`scripts/bootstrap-metabase.sh`, que bate na API do Metabase depois do primeiro boot e cadastra a
fonte de dado automaticamente. Sem esse script, a conexao teria que ser criada manualmente na
interface toda vez que a stack subisse do zero.

Esse script roda como parte do workflow "Terraform Apply" (ver `.github/workflows/terraform-apply.yml`).

## Variaveis de ambiente

`METABASE_VERSION`, versao da imagem. `METABASE_DB_PASSWORD`, credencial tecnica da conexao do
Metabase com o proprio banco de configuracao no Postgres, distinta do login administrativo do
Metabase, que usa o usuario unico (ver `SPEC.md`, secao 6.1).

## Login administrativo

O Metabase exige formato de e-mail no login administrativo, diferente dos outros paineis que
aceitam usuario livre. O endereco usado como `SHARED_ADMIN_USER` neste projeto precisa ter
formato de e-mail por causa dessa exigencia especifica do Metabase, ver `SPEC.md`, secao 6.1.
