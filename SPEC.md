# Spec — capitalcerto_lake

Spec de arquitetura e infraestrutura do projeto Capital Certo, cliente da Rangel Tech. **Projeto
totalmente apartado do ecossistema interno Rangel Tech** — infra, dado e runtime isolados, zero
compartilhamento com outros projetos/clientes da Rangel Tech. Cobre o repositório, o deploy na VPS
do cliente e a orquestração dos serviços. Não cobre modelo de dados de risco, lista final de
variáveis nem algoritmo (fica fora do repositório de infra, ver skill `capitalcerto` seção 5,
item de confidencialidade).

## 1. Contexto

- Cliente: Capital Certo, factory de crédito CNPJ. Contrato fechado, projeto em execução.
- VPS já contratada e provisionada pelo próprio cliente: Contabo Cloud VPS 12 (12 vCPU AMD EPYC,
  48 GB RAM, 400 GB NVMe), IP `169.58.240.203`, hostname `vmi3535408`, Ubuntu Server (kernel 6.8).
- Acesso atual: SSH por chave (`capital_certo_ed25519`) como `root`, login por senha SSH ainda
  habilitado (decisão deliberada de não fazer hardening ainda, revisitar antes do bootstrap
  Terraform criar o usuário de deploy).
- Sem domínio por enquanto. Tudo por IP direto. Domínio entra depois como variável, sem exigir
  redesenho.
- **Isolamento total do ecossistema Rangel Tech.** Este é projeto de cliente, não infra interna da
  Rangel Tech. Repo próprio, VPS própria do cliente, secrets próprios, sem nenhum recurso
  compartilhado com `RangelTech/vps_rt_infra` ou qualquer outra infra da Rangel Tech: sem reusar
  conta/instância de 9Router, sem rede/VPN entre as duas VPS, sem secret ou credencial cruzada,
  sem backup no mesmo bucket/conta. `RangelTech/vps_rt_infra` serve só como **referência de
  padrão de repo** (Terraform tratando VPS Contabo existente como alvo, layout de pastas,
  convenção de CI/CD) — cada peça (Traefik, Postgres, MinIO, 9Router etc.) é instância nova,
  isolada, só para este cliente. O repo mora na org GitHub `RangelTech` por conveniência de conta
  (é onde a Rangel Tech versiona o trabalho que presta), mas o runtime na VPS do cliente não
  depende de nada da Rangel Tech além do próprio código deste repositório.

## 2. Repositório

- Nome: `capitalcerto_lake`, org `RangelTech` (GitHub).
- Visibilidade: **público no início** (sem quota de minutos CI/CD grátis em repo privado de org),
  **privatiza depois** assim que o plano de Actions comportar. Isso não muda nada da spec de
  segredos abaixo (seção 6) — repo público só é seguro aqui porque nenhum segredo real jamais é
  commitado, só vive em GitHub Actions Secrets. Reforçar `.gitignore` completo desde o primeiro
  commit (não deixar pra reforçar só quando privatizar).
- Estrutura (espelha `vps_rt_infra`):

```
capitalcerto_lake/
├── terraform/              # trata a VPS existente como alvo, não provisiona VM
│   ├── main.tf             # bootstrap remoto via null_resource + provisioners ssh
│   ├── variables.tf
│   ├── outputs.tf
│   ├── versions.tf
│   ├── dns.tf              # vazio/no-op até existir domínio
│   └── terraform.tfvars.example
├── cloud-init/
│   └── bootstrap.sh        # docker, firewall, fail2ban, usuário deploy, hardening
├── compose/
│   ├── docker-compose.yml
│   ├── .env.tftpl          # renderizado pelo Terraform a partir das variáveis
│   └── .env.example
├── apps/
│   ├── risk-api/           # FastAPI, model dummy por enquanto (ver 3.3), README próprio
│   └── ninerouter/         # placeholder minimo ate o pacote real do 9Router entrar, README próprio
├── configs/
│   ├── traefik/            # README próprio
│   ├── airflow/            # README próprio
│   ├── clickhouse/         # README próprio
│   └── metabase/           # README próprio
├── scripts/
│   ├── deploy.sh
│   ├── bootstrap-metabase.sh
│   ├── healthcheck.sh
│   └── README.md
│   # backup-*.sh entra depois, quando o cliente definir destino (seção 9, item 4)
├── .github/workflows/
│   ├── terraform-plan.yml
│   ├── terraform-apply.yml
│   └── deploy-vps.yml      # atalho rápido: rsync + restart sem Terraform completo
├── services.json           # liga/desliga serviços opcionais sem tocar no compose
├── memoria.md              # decisões e histórico do projeto, mesmo padrão dos outros repos RT
└── README.md               # raiz, indexa todos os READMEs de serviço (ver seção 8)
```

## 3. Serviços (Docker Compose, 1 container por serviço)

| Serviço      | Papel                                                        | Observação                                                                 |
|--------------|---------------------------------------------------------------|-----------------------------------------------------------------------------|
| Traefik      | Reverse proxy / entrada única                                 | Sem TLS por enquanto (sem domínio); HTTP direto por IP. Let's Encrypt entra depois. |
| Postgres     | Metadados (Airflow, Metabase app db)                          | 1 instância, bancos separados por serviço (`airflow`, `metabase`)          |
| MinIO        | Data lake (raw / trusted / analytics)                         | Mesmo padrão de camadas do lake MindLab, em object storage local           |
| ClickHouse   | Warehouse analítico / Feature Mart                             | Fonte de dado pro Risk API e pro Metabase                                  |
| Airflow      | Orquestração (LocalExecutor)                                   | 1 pipeline por fonte (ERP, Chatwoot, bureaus, fotos)                       |
| Metabase     | BI                                                              | Aponta pro ClickHouse (e Postgres se precisar)                             |
| 9Router      | Roteamento LLM/VLM (extração de sinal, não decisão)             | Instância própria e isolada deste projeto, sem relação com nenhuma outra infra |
| Risk API     | FastAPI, motor de score/decisão                                 | Único serviço com código proprietário deste projeto; lê do ClickHouse. Nasce agora com código dummy no lugar do modelo (ver 3.3), modelo real entra depois |
| Uptime Kuma  | Observabilidade básica (uptime/healthcheck dos outros serviços)  | Tem UI própria (dashboard + status page), exposta via Traefik igual aos outros. VPS hoje não tem nenhuma observabilidade (checado ao vivo, seção 9), entra já na V1 junto no bootstrap |

Redes Docker: `internal` (só entre serviços, sem exposição) e `public` (atrás do Traefik),
mesma separação usada em `vps_rt_infra`.

### 3.1 Grafo de dependência real (não confundir com rede)

```
ERP / Chatwoot / bureaus / fotos  →  Airflow  →  MinIO (raw → trusted)
                                        │
                                        └──────→  ClickHouse (Feature Mart)
                                                        │
                                        ┌───────────────┼───────────────┐
                                        ▼                                ▼
                                    Metabase                        Risk API
                                  (dashboards)                (score/decisão)

Postgres: só metadado de Airflow e config própria do Metabase — não recebe nem alimenta
          nenhum dado do lake/warehouse, é infra de suporte de cada ferramenta.
```

ClickHouse não lê o MinIO diretamente por padrão — quem move o dado é o Airflow (extract →
grava MinIO → transforma → carrega ClickHouse). Usar o S3 table engine do ClickHouse pra ler
MinIO direto é possível, mas fica como decisão em aberto pra V2 se fizer sentido, não é V1.

### 3.2 O que nasce conectado automaticamente vs. o que precisa de bootstrap

Rede: automática (Compose já bota tudo na mesma rede `internal`, resolução por nome de serviço).
Conexão a nível de aplicação (usuário/senha/endpoint dentro de cada ferramenta) depende da
ferramenta — nem tudo aceita ficar pronto só com env var:

| Conexão                              | Como fica pronta                                                        |
|---------------------------------------|--------------------------------------------------------------------------|
| Airflow → MinIO                       | Env var `AIRFLOW_CONN_MINIO_S3` no `.env`, nativo do Airflow             |
| Airflow → ClickHouse                  | Env var `AIRFLOW_CONN_CLICKHOUSE_DEFAULT` no `.env`, nativo do Airflow   |
| Metabase → Postgres (config própria)  | Env vars nativas do Metabase (`MB_DB_TYPE`, `MB_DB_CONNECTION_URI`)      |
| Metabase → ClickHouse (fonte dos dash)| **Sem env var própria** — precisa script batendo na API do Metabase após o primeiro boot (mesma receita já usada nos scripts do case MindLab, seção 8 da skill) |
| Postgres com 2 bancos (`airflow`, `metabase`) na mesma instância | Init script em `docker-entrypoint-initdb.d/`, criado no primeiro boot do container |
| Risk API → ClickHouse                 | Connection string simples via env var, lida pelo próprio código da API  |

O script de bootstrap do Metabase (`scripts/bootstrap-metabase.sh` ou `.py`) e o init SQL do
Postgres (`configs/postgres/init-multi-db.sql` ou similar) entram no repo como parte do primeiro
deploy. Sem isso, cada stack nova exige clicar manualmente na UI do Metabase pra criar a conexão
com o ClickHouse, o que não escala e quebra a promessa de "sobe do zero automatizado".

### 3.3 Risk API nasce com modelo dummy

O modelo de crédito ainda não existe (fase de estudo/rotulagem em andamento, ver skill
`capitalcerto` seção 5 e 6). A Risk API entra no repo desde já, com toda a arquitetura pronta
(rotas, schema de request/response, conexão com o ClickHouse, logging, healthcheck), mas com um
modelo dummy no lugar do modelo real: uma função Python simples, determinística ou aleatória
controlada, que devolve um score no mesmo formato que o modelo real vai devolver. O objetivo é só
marcar onde o código do modelo entra depois, sem travar o resto da infra esperando o modelo ficar
pronto.

```
apps/risk-api/
├── Dockerfile
├── app/
│   ├── main.py            # FastAPI app, rotas
│   ├── schemas.py          # request/response (contrato já definitivo, mesmo com model dummy)
│   ├── clickhouse_client.py
│   └── model/
│       ├── __init__.py
│       ├── interface.py    # contrato que o modelo real precisa respeitar (predict(features) -> score)
│       └── dummy_model.py  # implementação dummy, é o que roda hoje
└── tests/
```

Quando o modelo real ficar pronto, ele entra em `app/model/` implementando a mesma interface de
`interface.py`, sem precisar mexer em rota, schema ou no resto da stack. `dummy_model.py` fica
marcado com um comentário curto dizendo que é placeholder, pra não passar despercebido se alguém
esquecer de trocar.

## 4. Terraform — VPS já existe, não provisiona VM

Segue o mesmo modelo de `vps_rt_infra/terraform/main.tf`:

1. `local_file.compose_env` — renderiza `compose/.env` a partir de `compose/.env.tftpl` com as
   variáveis do Terraform (versões de imagem, credenciais, portas). Todo `$` literal em senha
   precisa de `replace(var.x, "$", "$$")` porque o parser do `.env` do Compose trata `$` como
   início de variável.
2. `null_resource.bootstrap` — via `ssh`/`remote-exec` ou `local-exec` com `ssh`, dispara
   `cloud-init/bootstrap.sh` na VPS: instala Docker, firewall (ufw), fail2ban, cria usuário de
   deploy dedicado (não usar `root` no dia a dia), instala a chave pública do usuário de deploy.
   Depois disso, `root` deixa de ser o canal de deploy.
3. `null_resource.deploy` (ou etapa dentro do bootstrap) — `rsync` de `compose/`, `configs/`,
   `apps/`, `scripts/` pra `/opt/capitalcerto/` na VPS, depois `docker compose up -d`.
4. `dns.tf` fica vazio/comentado até existir domínio — quando entrar, replica o padrão de
   `vps_rt_infra/terraform/dns.tf` (hoje aponta pro provedor de DNS que o cliente escolher).

`terraform.tfvars` real nunca vai pro Git (fica só o `.example`); credenciais reais moram em
GitHub Actions Secrets (mesmo padrão do `vps_rt_infra`).

## 5. CI/CD (GitHub Actions)

- `terraform-plan.yml`: roda em PR, mostra o diff de infra.
- `terraform-apply.yml`: roda em merge na branch principal (ou manual), aplica de fato —
  bootstrap + render do `.env` + sobe/atualiza a stack inteira.
- `deploy-vps.yml`: atalho manual (`workflow_dispatch`) pra quando só mudou código do Risk API,
  do 9Router ou um config — `rsync` + `docker compose up -d --remove-orphans` sem passar pelo
  Terraform completo, igual ao `vps_rt_infra`.
- Secrets necessários no repo: `VPS_SSH_PRIVATE_KEY` (a chave `capital_certo_ed25519`, gerada em
  2026-08-26), `VPS_HOST` (`169.58.240.203`), `VPS_DEPLOY_USER` (criado no bootstrap, não `root`).

**Deploy granular, mas não por mérito do Terraform.** Editar só a config do Airflow e rodar o
deploy não derruba os outros serviços — mas quem garante isso é o **Docker Compose**, não o
Terraform: `docker compose up -d` compara a definição de cada serviço (imagem, env, volumes)
contra o container rodando e só recria o que mudou, deixando o resto no ar sem downtime. Como não
existe backend remoto de state (seção 6), o Terraform não tem memória entre execuções — todo
`apply` reprocessa bootstrap e deploy inteiros de novo, sempre; ele não decide "só toca no
Airflow" via diff de recurso como um `terraform plan` tradicional faria. Consequências práticas:
volumes precisam ser nomeados (named volumes) pra dado sobreviver a uma recriação de container, e
`depends_on` não causa recriação em cascata (Airflow reiniciar não derruba o Postgres).

## 6. Credenciais e segredos

Fonte da verdade: **GitHub Actions Secrets** (repo secrets, environment `production`). Nunca vai
segredo real pro Git — nem em `.tfvars`, nem em `.env`, nem em config committado.

Fluxo, ponta a ponta, todo terraformado (mesmo padrão do `vps_rt_infra`):

```
GitHub Actions Secret (ex.: POSTGRES_ADMIN_PASSWORD)
        │  workflow injeta como env var TF_VAR_postgres_admin_password
        ▼
terraform apply  (variables.tf marca a var como sensitive)
        │  local_file.compose_env renderiza compose/.env.tftpl
        ▼
compose/.env  (gerado, nunca commitado, chmod 600)
        │  Terraform copia/rsync pra VPS
        ▼
/opt/capitalcerto/compose/.env  (na VPS, lido pelo docker compose em runtime)
```

Lista de secrets necessários no repo (nomes definitivos a fechar na implementação):
`VPS_HOST`, `VPS_SSH_PRIVATE_KEY`, `VPS_DEPLOY_USER`, `VPS_INITIAL_ROOT_PASSWORD` (usada só na
primeira execução do bootstrap, pra criar o usuário de deploy — depois disso não é mais usada),
`SHARED_ADMIN_USER` + `SHARED_ADMIN_PASSWORD` (login único dos paineis administrativos, ver 6.1),
`POSTGRES_ADMIN_PASSWORD`, `MINIO_ROOT_PASSWORD` (reaproveita `SHARED_ADMIN_PASSWORD`, ver 6.1),
`CLICKHOUSE_PASSWORD` (idem), `METABASE_DB_PASSWORD` (essa é técnica, não é login, fica distinta),
`AIRFLOW_FERNET_KEY` (técnica, distinta) + `AIRFLOW_ADMIN_PASSWORD` (reaproveita, ver 6.1),
`RISK_API_SECRET_KEY` (técnica, distinta), `NINEROUTER_API_KEYS` (técnica, distinta),
`TRAEFIK_BASIC_AUTH` (reaproveita, ver 6.1).

### 6.1 Usuário administrativo único (decisão do cliente)

Por pedido explícito, todos os paineis administrativos das ferramentas (console do MinIO,
ClickHouse, Airflow, Metabase, dashboard do Traefik, 9Router) usam o mesmo login e a mesma senha,
pra facilitar operação do dia a dia. Implementado como 1 par de secrets únicos no GitHub Actions
(`SHARED_ADMIN_USER` e `SHARED_ADMIN_PASSWORD`), referenciado por várias `TF_VAR_*` ao mesmo
tempo em vez de gerar 1 segredo distinto por painel.

Limites dessa decisão, pra não virar mal-entendido depois:

- Vale só pro login administrativo humano de cada painel. Não vale pra credencial técnica de
  conexão entre serviços (Airflow para ClickHouse, Airflow para MinIO, Risk API para ClickHouse
  continuam com senha própria e distinta cada uma, é comunicação máquina a máquina, não tem
  "operador" fazendo login ali).
- Não vale pro acesso SSH da VPS. Acesso à VPS continua por chave (`capital_certo_ed25519`),
  segregado, nunca a mesma senha do painel administrativo. Misturar os dois multiplica o estrago
  se qualquer um dos dois vazar.

Risco que preciso registrar mesmo comprando a decisão: com senha única, vazar 1 painel (por
exemplo, dashboard do Traefik exposto por engano, ou um log que capturou a senha sem querer) vaza
todos os outros paineis ao mesmo tempo. É o tradeoff clássico de conveniência contra raio de
estrago de um vazamento. Decisão do cliente, aqui só documentando pra constar.

Ponto que precisa de resposta sua: Metabase não aceita usuário livre no login administrativo,
exige e-mail. Como o projeto ainda não tem domínio próprio (seção 7), qual e-mail usar? Um e-mail
real de algum operador, ou um endereço genérico do tipo `admin@capitalcerto.com.br` mesmo sem
esse domínio existir ainda (o Metabase só valida o formato, não entrega nada nesse endereço a
menos que configure SMTP depois). Se a meta é "mesmo login em todo lugar", o username usado nos
outros paineis deveria ser esse mesmo endereço por consistência, ou aceitamos que o Metabase é a
única exceção de formato dentro dessa decisão.

**Sobre o Terraform state**: seguindo o mesmo modelo do `vps_rt_infra`, não existe backend remoto
configurado — o `terraform.tfstate` é local ao runner do GitHub Actions, que é efêmero (criado e
destruído a cada execução do workflow), e está no `.gitignore` (nunca é commitado). Na prática o
Terraform aqui funciona como automação de push de config via SSH, não como gerência real de
recurso cloud (não existe API de provedor criando/destruindo VM) — por isso cada `apply` reprocessa
o bootstrap e o deploy do zero, o que é seguro porque `bootstrap.sh` e o passo de deploy são
idempotentes por design. Efeito colateral aceito: `terraform plan` sempre mostra "a criar" em vez
de mostrar diff real. Risco residual: se alguém rodar `terraform apply` na própria máquina (fora do
CI), o `.tfstate` local daquela máquina fica com os segredos renderizados em texto plano — o
`.gitignore` impede o commit acidental, mas a prática recomendada é sempre rodar via GitHub
Actions, nunca local, exceto para depuração pontual (e apagar o `.tfstate` local depois).

Isso é o mesmo tradeoff aceito no `vps_rt_infra` hoje — não é uma lacuna nova deste projeto, é
característica do padrão de repo que estamos seguindo.

## 7. Domínio (futuro)

Sem domínio agora: Traefik expõe os serviços via IP:porta direto, sem TLS. Quando o cliente
definir o domínio:

1. Adiciona `root_domain` e `letsencrypt_email` nas variáveis do Terraform (já previstos desde
   já no `variables.tf`, só ficam com default vazio/nulo por enquanto).
2. Preenche `dns.tf` com o provedor escolhido.
3. Traefik liga o resolver ACME e passa a rotear por subdomínio (`airflow.dominio`,
   `metabase.dominio`, `api.dominio` etc.) em vez de porta.

Não precisa refazer nada da stack pra isso, é só preencher variável e reaplicar.

## 8. Documentação do repositório

Cada pasta de serviço tem seu próprio README, e existe um README raiz que indexa todos. Sem
linguagem de IA, sem emojis, sem travessão (usar vírgula ou dois pontos no lugar), texto corrido e
direto, mesmo estilo já usado nos documentos de arquitetura entregues ao cliente (skill
`capitalcerto`, seção 9).

**Nenhum README expõe dado real.** README explica arquitetura, como cada serviço funciona, como
acessar cada painel e onde cadastrar um segredo novo, mas nunca leva IP, senha, token ou qualquer
valor real de credencial. Onde um exemplo for necessário, usa placeholder (`<VPS_HOST>`,
`<SHARED_ADMIN_PASSWORD>`) ou referencia o nome do secret no GitHub Actions, nunca o valor.

Estrutura de documentação:

```
README.md                        # raiz, indexa tudo, visão geral da stack e como subir do zero
compose/README.md                # o que é o compose, como rodar local, como as redes funcionam
apps/risk-api/README.md          # contrato da API, onde entra o modelo real, como rodar local
configs/traefik/README.md
configs/airflow/README.md        # convenção de DAG, connections esperadas, onde ficam os pipelines
configs/clickhouse/README.md     # schema do Feature Mart, convenção de tabela
configs/metabase/README.md       # como a conexão com o ClickHouse é criada (script, não manual)
terraform/README.md              # como rodar plan/apply, quais variáveis existem, o que cada uma faz
scripts/README.md                # o que cada script faz e quando rodar
```

README raiz cobre, no mínimo: o que é o projeto, lista de serviços com uma frase cada, como subir
a stack pela primeira vez, como fazer deploy de uma mudança pontual, onde ficam os segredos e como
cadastrar um novo, link pra cada README de serviço.

README de cada serviço cobre, no mínimo: o que o serviço faz dentro dessa stack especificamente
(não é documentação genérica da ferramenta), quais variáveis de ambiente ele espera, com quem ele
conversa (rede e conexão de aplicação, ver 3.2), e como rodar ou testar isoladamente se precisar.

## 9. Achados da rechecagem

Pontos que apareceram revisando a spec inteira de novo, com a decisão do Lucas em cada um:

1. **Senha de root da VPS ficou exposta em texto plano nesta conversa.** Decisão: não trocar,
   nunca, sem autorização explícita. Regra permanente do projeto: nenhuma credencial deste projeto
   (senha de root, chave SSH, secret do GitHub Actions) é rotacionada por iniciativa própria, nem
   por suspeita de vazamento ou hack, rotação malfeita já quebrou sistema no passado. Se aparecer
   credencial exposta em qualquer lugar, o procedimento é reportar e perguntar, nunca trocar direto.
   Registrado também na memória do projeto e na skill `capitalcerto`.
2. **README nunca expõe dado real** (IP, senha, token). README documenta arquitetura, como cada
   serviço funciona e como acessar, com placeholder ou nome do secret no lugar do valor. Resolvido,
   ver seção 8.
3. **Observabilidade hoje: nenhuma.** Checado ao vivo na VPS em 2026-08-26: sem Docker instalado,
   ufw inativo, fail2ban inativo, sem Prometheus/Grafana/Netdata/Zabbix/Uptime Kuma, sem cron. É
   Ubuntu recém-entregue pelo Contabo, nada além do sistema base. Uptime Kuma entra na V1 (mesmo
   padrão de baseline descrito na skill `capitalcerto`, seção 3), nasce junto no bootstrap
   (`cloud-init/bootstrap.sh`, seção 4) ao lado de Docker, ufw e fail2ban. Uptime Kuma tem UI
   própria (dashboard de monitores + status page), cobre "serviço no ar ou não" e tempo de
   resposta sozinho, sem precisar de Grafana. Grafana é outra categoria (métrica histórica de
   CPU/memória/latência via Prometheus), continua fora da V1, só entra se o volume justificar
   (mesma condição já descrita na skill `capitalcerto`, seção 3).
4. **Backup sem destino: pendência futura consciente, não bloqueia a V1.** Scripts
   `backup-postgres.sh`, `backup-minio.sh` e `backup-clickhouse.sh` saem da estrutura inicial do
   repo por enquanto (entram quando o cliente definir onde o backup vai, respeitando o isolamento
   da seção 1, nunca na mesma conta do `vps_rt_infra`). Até lá, a stack sobe sem rotina de backup.

## 10. Pendências

Scaffold completo escrito e commitado localmente em 2026-08-26 (`Desktop\Repos\capitalcerto_lake`,
commit inicial). Terraform validado (`terraform validate`), Docker Compose validado
(`docker compose config`). Ainda não existe no GitHub, ainda não rodou contra a VPS de verdade.

- [x] Escrever `.gitignore` completo, feito antes do primeiro commit
- [x] Escrever todos os READMEs (raiz e por serviço, seção 8), feito junto com o scaffold
- [x] Implementar a interface do modelo (`app/model/interface.py`) e o dummy (`dummy_model.py`)
      da Risk API (seção 3.3)
- [ ] Criar o repositório de verdade no GitHub (org `RangelTech`, público) e dar `git push`
- [ ] Cadastrar todos os secrets da seção 6 no GitHub Actions antes do primeiro `terraform apply`
- [ ] Definir username/e-mail e senha do `SHARED_ADMIN_USER`/`SHARED_ADMIN_PASSWORD` (seção 6.1),
      incluindo qual e-mail usar no Metabase sem domínio próprio ainda
- [ ] Confirmar se cria usuário de deploy já no primeiro bootstrap (a spec já implementa criando,
      falta só a confirmação final antes do primeiro `terraform apply` de verdade)
- [ ] Confirmar bancos do ERP e da plataforma de contratos do cliente (define os DAGs reais do
      Airflow, hoje só existe um DAG de exemplo/healthcheck)
- [ ] Definir nomes finais de bucket MinIO (hoje o default é `raw,trusted,analytics`)
- [ ] Definir data/gatilho pra privatizar o repo (ex.: assim que a org tiver plano pago de Actions,
      ou antes de subir o primeiro dado real do cliente pro código, o que vier primeiro)
