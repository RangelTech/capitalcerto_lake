# Memória do repositório capitalcerto_lake

Decisões e histórico de infraestrutura deste projeto. Mesmo padrão de memória usado em outros
repositórios da Rangel Tech (ver `RangelTech/vps_rt_infra/memoria.md` como referência de formato,
sem nenhum outro vínculo entre os dois repositórios).

## 2026-08-26, provisionamento inicial

VPS contratada e provisionada diretamente pelo cliente Capital Certo: Contabo Cloud VPS 12
(12 vCPU AMD EPYC, 48GB RAM, 400GB NVMe), Ubuntu Server. Acesso SSH configurado por chave dedicada.
Checado ao vivo antes de qualquer bootstrap: servidor limpo, sem Docker, sem firewall ativo, sem
fail2ban, sem nenhuma ferramenta de observabilidade instalada.

Spec de arquitetura escrita e revisada em `SPEC.md` antes do primeiro código. Decisões que saíram
dessa revisão:

- Repositório público no início (sem quota de minutos de CI/CD grátis em repositório privado de
  org), privatiza depois. Nenhum segredo real é commitado em nenhum momento, por isso é seguro.
- Projeto totalmente isolado do ecossistema Rangel Tech: sem serviço, secret, rede ou conta de
  backup compartilhados com qualquer outro projeto da Rangel Tech.
- Nunca rotacionar nenhuma credencial deste projeto sem autorização explícita do responsável, nem
  por suspeita de vazamento. Regra permanente, motivada por rotação malfeita que já quebrou sistema
  no passado.
- Uptime Kuma entra desde a primeira versão da stack (VPS não tinha nenhuma observabilidade).
  Grafana/Prometheus ficam fora até o volume justificar.
- Backup sai do escopo inicial, entra quando o cliente definir destino de armazenamento próprio.
- Risk API nasce com um modelo dummy implementando a interface que o modelo real vai seguir depois
  (`apps/risk-api/app/model/interface.py`). O modelo de crédito em si não existe ainda.
