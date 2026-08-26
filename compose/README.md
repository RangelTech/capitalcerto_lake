# compose

Definicao Docker Compose de todos os servicos da stack, um container por servico.

## Arquivos

`docker-compose.yml` declara os servicos, redes e volumes. `.env.tftpl` e o modelo que o
Terraform preenche com valores reais (versoes de imagem, credenciais, portas) e grava como
`.env` dentro desta mesma pasta, esse `.env` nunca e commitado. `.env.example` mostra o formato
esperado com valores fake, serve de referencia pra quem for editar `.env.tftpl` ou testar local.

## Redes

`internal` nao tem saida pra fora do Docker, so os servicos entre si se enxergam por nome.
`public` e a rede que o Traefik usa pra expor o que precisa ficar acessivel de fora.

## Rodando local

Pra testar a stack fora da VPS, copiar `.env.example` para `.env`, trocar os valores fake por
valores validos e rodar `docker compose up -d` dentro desta pasta. Sem dominio configurado, cada
servico fica acessivel direto por `localhost:<porta>`, a porta de cada um esta comentada ao lado
do mapeamento em `docker-compose.yml`.

## Como um servico muda sem derrubar os outros

`docker compose up -d` compara a definicao de cada servico contra o container que ja esta
rodando e so recria o que mudou. Editar a imagem ou variavel de um servico nao reinicia os
demais. Detalhe completo em `SPEC.md`, secao 5, na raiz do repositorio.
