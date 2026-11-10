# Capital Certo: próximas entregas (pedidas pelo Lucas em 10/11/2026)

Documento vivo. Ordem de execução = ordem da lista. Marcar `[x]` só depois de testar de verdade.

## Bloco 1. Fechar o que está aberto (matriz de testes, dashboard, pipeline)
- [x] Bateria 2 da matriz: combinações de 1 a 4 variáveis (391 mil perfis), 11 algoritmos × 10 conjuntos × 4 alvos × 2 estratos, efeito ajustado por Pix/provedor/mês, sazonalidade controlada, estabilidade por metade do tempo.
- [x] Resultados da bateria 2 carregados no ClickHouse (`analytics_ml.motor_combinacoes_1a4`, `motor_matriz_modelos`, `motor_efeito_ajustado`, `motor_sazonalidade_controlada`) e catálogo de variáveis atualizado.
- [ ] Decisão das variáveis do motor com a matriz completa (regra: só entra o que passa FDR **e** melhora fora do tempo). Resultado hoje: o motor do dia 0 não muda; tom da conversa fica como observação.
- [ ] Dashboard: reconstruir a aba "Motor de crédito" com nomes claros e texto de ajuda ao lado de cada gráfico; criar a aba "Mapa de testes"; trocar os textos estáticos pelos resultados finais; validar por captura de tela (CDP).
- [ ] Orquestradora diária verde ponta a ponta (run `teste_final_10nov`) e as 3 tarefas do `credit_motor_diario`.
- [ ] Reavaliar a latência da API de consulta por telefone (alvo cerca de 1 s) com a VPS ociosa.

## Bloco 1b. Estudo dedicado do dia 0 (o que mais interessa ao cliente)
Situação: acerto ~0,63 (confiança "baixa"); o sinal forte é Pix agendado (calote 74% com Pix x 20% sem), que é uma **decisão operacional**, não só um dado do cliente.
- [ ] **Por que o Pix agendado falha:** cruzar autorização do agendamento, falha de cobrança (Efi e Pluggy), saldo no dia do débito, horário do débito e provedor; separar falha técnica de inadimplência do cliente. Pode ser a alavanca principal (corrigir o processo vale mais que melhorar o score).
- [ ] **Dados bancários na assinatura (Open Finance, Pluggy):** a Pluggy já é usada para cobrar; verificar se extrato, saldo e fluxo de caixa podem ser coletados com consentimento no dia 0. É o preditor clássico para crédito de pequeno negócio.
- [ ] **Tom da conversa em CPU no delta diário** (Bloco 3), depois de validar a concordância com o modelo grande.
- [ ] **Dia 0 só dentro de "sem Pix"** (onde o score pode mudar a decisão) e dentro de "Pix" (onde quase todo mundo falha): modelos separados com os contratos de junho em diante.
- [ ] Acumular meses: refazer a matriz a cada mês; o motor já revalida todo dia.
- [ ] Reportar ao cliente, em linguagem simples, o que o dia 0 consegue e o que não consegue.

## Bloco 2. ClickHouse limpo, documentado e legível por IA
- [ ] **Faxina:** inventariar todos os bancos e tabelas; listar lixo (tabelas temporárias `__novo`, `_old`, `_bak`, testes, vazias, duplicadas, sem uso); mostrar a lista ao Lucas antes de apagar o que for dado de origem; apagar o lixo claramente temporário com quarentena (snapshot) quando houver dúvida.
- [ ] **Documentação total:** `COMMENT` em todas as tabelas e em todas as colunas (nome, o que é, unidade, de onde vem, chave).
- [ ] **Catálogo legível por IA:** view `analytics.catalogo_tabelas` (banco, tabela, descrição, linhas, colunas, camada, fonte, atualização) e `analytics.catalogo_colunas`.
- [ ] **Matriz de testes única:** tabela `analytics_ml.motor_testes_realizados` que consolida TODOS os testes e cruzamentos já feitos (correlações univariadas, combinações 1 a 4, interações, matriz de modelos, comitê, efeito ajustado, sazonalidade, mídia, bureau, tom, OCR), com: família, variáveis, alvo, estrato, método, n, resultado, p, q, estável, conclusão e tabela de origem. Para o Claude Code ou o Hermes saber o que já foi testado antes de testar de novo.

## Bloco 3. LLM em CPU no pipeline diário (delta)
- [ ] O Qwen da Rangel Tech foi só para acelerar os testes. No pipeline diário o tom da conversa e o resumo precisam rodar **na própria VPS**, em CPU (llama.cpp, Qwen3 1,7B), só no **delta**: contratos e conversas novos ou alterados desde a última execução.
- [ ] Tabela `analytics.tom_conversa_contrato` (12 perguntas, versão do modelo, data) e tarefa na DAG diária (janela de madrugada, teto de tempo, retomada).
- [ ] **Validar a troca de modelo:** o tom foi medido com o Qwen 27B; o 1,7B em CPU dá valores diferentes. Medir a concordância (correlação e ordem) em amostra e só usar o tom em CPU se o sinal se mantiver.
- [ ] Resumo por cliente no mesmo delta (reescrita do resumo estruturado, com checagem de números).
- [ ] Nenhuma chave da API Qwen fica na VPS do cliente. (A chave OWNER foi passada por variável de ambiente só durante o teste.)

## Bloco 4. Data IA Hub (plataforma web dentro da infra deles)
**O que é:** um site só com login e senha de administrador, com um agente **Hermes** que é o especialista no Data Lake da Capital Certo. Interface reaproveitada do Segundo Cérebro, só o chat.

**Regras**
- **Sem a IA Qwen da Rangel Tech.** O motor de IA é o **9Router embutido** (igual ao Segundo Cérebro): o cliente coloca a conta Claude Code ou GPT dele, cria round-robin e fallback. Aba de configuração com providers.
- Um tenant, login e senha de admin. Sem login remoto, sem Electron, sem opções além do necessário: chat, anexar arquivo, gravar áudio (ditado), trocar o provider.
- Arquivos anexados vão para o **MinIO** deles (bucket próprio).
- Paleta e marca da Capital Certo (azul). Nome: **Data IA Hub**.
- Pode pesquisar na internet (vem do modelo do cliente, Claude ou GPT).
- Código no GitHub (repositório novo da Rangel Tech). Nada de credencial no repositório.

**O Hermes precisa saber (skills focadas no Data Lake):**
- Ler e consultar tudo no ClickHouse (raw, trusted, analytics, analytics_ml), usando o catálogo e a matriz de testes.
- Rodar análises e **rodar o motor de crédito com variáveis diferentes** ("testa com esta variável") e comparar fora do tempo.
- **Criar gráficos e dashboards no Metabase** (API) e entregar o link.
- Bater direto na **API do Chatwoot** e ver os dados do CredHub (banco do portal), PDFs, transcrições, imagens e vídeos no MinIO, dados brutos.
- Usar a **API de risco** por telefone, CPF ou CNPJ.
- Explicar para eles, em português simples, como as coisas funcionam.
- Sem geração de imagem ou vídeo por enquanto.

**Testes (a noite toda):** abrir pelo Chrome via CDP, entrar, clicar, perguntar, anexar, trocar provider; conferir cada skill com pergunta real; registrar prints.

**Acrescentado pelo Lucas (10/11):**
- **Um usuário só** (o administrador). Sem multiusuário.
- **Skill do ambiente inteiro deles**, incluindo URLs e links ("qual é o link daquele Metabase?"): Metabase, Airflow, API de risco, ClickHouse, MinIO, Chatwoot, CredHub, Data IA Hub, dashboards existentes com título e link, DAGs, tabelas. Gerada a partir do que existe de verdade e conferida (nada inventado).
- **Auto-aprendizado:** o Hermes grava na própria skill/memória tudo que cria ou descobre ("criei o dashboard X com estes gráficos, link Y, a pedido de Z"): dashboards novos, consultas úteis, decisões, análises, resultados de testes de variáveis. Antes de trabalhar, ele consulta essa memória e **procura nas conversas antigas** (busca no histórico de chats).

Checklist do Bloco 4 (atualizado em `ESTADO_MOTOR.md`):
- [ ] Skill `ambiente-capital-certo` (URLs, links, mapa do que existe) + skill `memoria-do-hub` (auto-aprendizado e busca em conversas antigas).
- [ ] Spec técnica do Data IA Hub (arquitetura, segurança, deploy na VPS, o que reaproveitar do Segundo Cérebro).
- [ ] Repositório e esqueleto.
- [ ] Autenticação de admin (sessão, senha no `.env` da VPS, sem rotacionar nada existente).
- [ ] 9Router embutido e aba de providers.
- [ ] Hermes com as skills do Data Lake (ClickHouse, Metabase, Chatwoot, CredHub, MinIO, API de risco, motor).
- [ ] Chat com anexos (MinIO) e ditado.
- [ ] Tema Capital Certo.
- [ ] Deploy na VPS (Docker, atrás do Traefik/porta definida), HTTPS se houver domínio.
- [ ] Testes por CDP com prints e relatório.

## Pendências já conhecidas fora dos blocos
- Segurança "repaginada" (credenciais em Airflow Connections e `.env`; API de risco por IP sem HTTPS).
- Calibração por safra (a safra é efeito real: p entre 0,0000 e 0,01 mesmo controlando Pix e provedor).
- Nada commitado no GitHub; o Lucas decide quando.
