# scripts

## deploy.sh

Roda localmente o mesmo passo que o workflow "Deploy Stack Only" roda em CI: sincroniza
`compose/`, `configs/`, `apps/` e `scripts/` pra VPS e sobe a stack de novo. Uso pontual, o
caminho normal e sempre via GitHub Actions.

## bootstrap-metabase.sh

Cadastra a conexao do Metabase com o ClickHouse via API, depois do primeiro boot. Precisa disso
porque o Metabase nao aceita essa conexao via variavel de ambiente. Roda automaticamente dentro
do workflow "Terraform Apply", so precisa ser rodado manualmente se essa etapa falhar ou se a
fonte de dado for apagada sem querer dentro do Metabase.

## healthcheck.sh

Roda na propria VPS, confere se cada servico esta respondendo na porta esperada. Util depois de
um deploy pra confirmar que subiu tudo certo.
