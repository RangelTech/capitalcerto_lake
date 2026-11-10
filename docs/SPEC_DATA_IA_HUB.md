# Data IA Hub: especificação técnica (10/11/2026)

Plataforma web dentro da infraestrutura da Capital Certo. Um usuário (o administrador), um chat, um agente (Hermes) que é especialista no Data Lake, motor de IA 9Router embutido com as contas do próprio cliente (Claude Code, GPT). **Sem a IA Qwen da Rangel Tech.** Interface reaproveitada do Segundo Cérebro, com a paleta azul da Capital Certo.

## 1. O que reaproveitar do Segundo Cérebro (repo `RangelTech/second-brain`, local `Desktop/new_ragente/repos/second-brain`)
| Peça | Uso no Hub |
|---|---|
| `renderer/` (chat, markdown, tabelas, estilos) | base visual; troca de paleta e remoção de telas (skills, mídia, conexões, remoto) |
| `agent.js` (sessão do Hermes via ACP, `prepararHome` que grava `config.yaml` e `SOUL.md`, restarts, gateway órfão) | portado para um servidor Node sem Electron; `app.getPath` vira variável de ambiente `HUB_DATA` |
| `embed-router.js` + `router/` (9Router) | o 9Router standalone sobe escondido em 127.0.0.1 com os dados na pasta do Hub; o Hub expõe `/api/router/*` autenticado |
| `hermes-instalar.js` | instalação do Hermes na construção da imagem (não na primeira conversa) |
| `ponte-llm.js` / `ponte-restaurar.js` | normalização do nome do servidor MCP para manter o cache de prefixo (só se o provedor for local; com Claude/GPT pode ser dispensado) |
| `skills.js` / `skills-repo.js` | sincronização das skills do Hub com manifesto |
O que **não** entra: Electron, atualizador, login remoto/ponte, navegador integrado, geração de imagem e vídeo, planos e cobrança, extensão do Chrome.

## 2. Arquitetura
```
navegador (HTTPS ou rede interna)
   │
hub-web (Node 20, Express + WebSocket)
   ├─ /login  (um usuário; senha em HUB_ADMIN_PASSWORD; cookie de sessão httpOnly, SameSite=Strict; bloqueio por tentativas)
   ├─ /chat   (SPA: mensagens, anexos, ditado, escolha do provider)
   ├─ /api/chats (histórico em SQLite com busca FTS5: "procurar nas conversas antigas")
   ├─ /api/upload  ->  MinIO bucket `hub` (anexos), devolve o caminho que o Hermes lê
   ├─ /api/dictado ->  transcrição (faster-whisper em CPU dentro do container; sem provedor externo)
   ├─ /api/router/*  ->  9Router embutido (providers, combos, round-robin)  [só autenticado]
   └─ agente: spawn de `hermes-acp` por sessão (ACP/JSON-RPC por stdio), HERMES_HOME próprio
        config.yaml: provider custom = http://127.0.0.1:<porta do 9Router>/v1  (chave local)
        skills: /hub/skills/capitalcerto/*  (somente do Data Lake)
        MCP/terminal: o Hermes roda comandos no container (python, curl), com as credenciais em variáveis de ambiente
```
Um único container (`data-ia-hub`) com Node + Python + Hermes + 9Router; volumes: `hub-data` (histórico, config do 9Router, HERMES_HOME). Rede docker da stack Capital Certo: enxerga `clickhouse`, `minio`, `metabase`, `risk-api` e `airflow` pelos nomes de serviço.

## 3. Segurança
- **Login único**: senha longa gerada na criação e gravada no cofre (`secrets/capitalcerto-data-ia-hub.json`); nada de rotação das existentes.
- **Rede**: a VPS hoje publica serviços por IP e HTTP. O Hub deve entrar atrás do Traefik com HTTPS (Let's Encrypt) assim que houver um domínio ou subdomínio; até lá, escutar só em porta interna e acessar por túnel SSH ou por IP restrito. **Senha em HTTP puro não é aceitável**: decisão do Lucas (domínio).
- **Credenciais do agente**: contas de serviço **novas** e mínimas (não reutilizar as de administrador):
  - ClickHouse: usuário `hub_ia` com perfil de leitura em `analytics`, `analytics_ml`, `trusted_*`, e escrita só no banco `hub_sandbox` (experimentos).
  - Metabase: chave de API própria (criar/editar cartões e dashboards da coleção da Capital Certo).
  - Chatwoot: token de leitura (já existe um token de administrador no cofre; criar um de agente leitor se possível).
  - MinIO: usuário com acesso aos buckets `raw` e `landing` (leitura) e `hub` (escrita).
  - API de risco: a chave existente (somente leitura).
- Hermes **não** recebe a chave do cofre pessoal nem chaves de Airflow; para rodar o motor com variáveis diferentes usa o endpoint `POST /v1/experimento` da API de risco (a criar) ou um script `motor_experimento.py` que roda em sandbox e só lê.
- Auditoria: todo comando do agente e toda consulta SQL ficam registrados no histórico da conversa.

## 4. Skills do Hermes (somente Data Lake), em `/hub/skills/capitalcerto/`
1. `ambiente-capital-certo`: mapa do ambiente, **com URLs e links reais** (Metabase e cada dashboard, Airflow e cada DAG, API de risco e `/docs`, ClickHouse, MinIO, Chatwoot, CredHub, o próprio Hub). Gerada por script a partir do que existe (Metabase `/api/dashboard`, Airflow `dags list`, `analytics.catalogo_tabelas`) e conferida; regenerada todo dia pelo pipeline para não envelhecer.
2. `clickhouse-datalake`: como consultar (comece por `analytics.catalogo_tabelas`, `catalogo_colunas`, `analytics_ml.motor_testes_resumo`), padrões de consulta, o que cada banco contém, armadilhas (`FINAL`, NULL em `join_use_nulls`).
3. `metabase-api`: criar cartões SQL e dashboards pela API, modelo de abas e filtros de campo, nomes claros para negócio e texto de ajuda ao lado de cada gráfico; devolver o link.
4. `chatwoot-api`: consultar conversas, mensagens e anexos pela API; dados brutos no ClickHouse `trusted_chatwoot`.
5. `credhub-dados`: contratos, parcelas, pagamentos, cobranças, auditoria (`trusted_credhub`); o que é cada evento.
6. `arquivos-minio`: ler PDFs, imagens, áudios, transcrições e vídeos (`raw/chatwoot_anexos`, tabelas `anexo_*`); anexos enviados ao Hub.
7. `api-de-risco`: consultar por telefone, CPF ou CNPJ e interpretar a resposta (confiança, faixa, cluster).
8. `motor-de-credito`: como o motor funciona (etapas, alvos, pesos), a matriz de testes já feita (**consultar antes de testar**) e como rodar um experimento com variáveis diferentes (fora do tempo, regra de ganho, FDR) sem mexer na produção.
9. `memoria-do-hub` (**auto-aprendizado**): ao criar um dashboard, uma consulta útil, uma decisão ou um resultado de teste, o Hermes grava uma nota em `/hub/memoria/` (título, data, contexto, link/arquivo, resumo); antes de trabalhar, busca nessa memória e no histórico de conversas (`GET /api/chats/search?q=`). Pode atualizar a skill `ambiente-capital-certo` com o que aprendeu (ex.: novo dashboard e link).
10. `explicar-ao-negocio`: como explicar resultados em linguagem simples (sem sigla), com exemplos do próprio ambiente.

## 4b. SOUL do Hermes
Português, tom simples, sem jargão; consulta as skills antes de agir; confirma antes de apagar ou sobrescrever qualquer coisa; nunca rotaciona credenciais; nunca inventa dado (consulta); diz quando não sabe; mostra o link do que criou; grava na memória o que criou.

## 5. Interface (só o chat)
Tela de login; chat com histórico à esquerda (busca); caixa de mensagem com anexar arquivo e gravar áudio; seletor de provider/combo; aba **Provedores** (9Router: adicionar conta Claude Code ou GPT, round-robin e fallback). Sem opções extras. Tema: azul Capital Certo.

## 6. Entrega e testes
- Repositório novo `RangelTech/data-ia-hub` (privado), sem credenciais; imagem Docker; `docker-compose` do Hub dentro da stack Capital Certo.
- Testes pelo Chrome via CDP: login errado e certo, nova conversa, pergunta sobre o Data Lake (SQL), pergunta de link ("qual o link do dashboard X?"), criação de gráfico no Metabase, anexar PDF e perguntar sobre ele, ditado, trocar provider, busca em conversa antiga, memória (criar dashboard e perguntar depois), recuperação após reiniciar o container. Prints e relatório em `docs/testes_hub/`.
- Pendências que dependem do Lucas: domínio/HTTPS; a conta Claude Code ou GPT do cliente no 9Router; confirmar criação das contas de serviço mínimas.
