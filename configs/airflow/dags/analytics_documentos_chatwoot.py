# Extrai CPF/CNPJ citados no Chatwoot (texto de mensagens + atributos da conversa) e valida o digito verificador.
# Alimenta o cruzamento lead (Chatwoot) x cliente (CRM CredHub): documento e chave forte, telefone e chave media.
# Saida: analytics_stg.chatwoot_documentos_citados (1 linha por contato x documento x mensagem/atributo).
import os
import re

import clickhouse_connect

RE_CNPJ = re.compile(r"(?<!\d)(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})(?!\d)")
RE_CPF = re.compile(r"(?<!\d)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)")


def _dv_cnpj(d):
    if len(d) != 14 or len(set(d)) == 1:
        return False
    def calc(base, pesos):
        r = sum(int(a) * b for a, b in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)
    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    p2 = [6] + p1
    return d[12] == calc(d[:12], p1) and d[13] == calc(d[:13], p2)


def _dv_cpf(d):
    if len(d) != 11 or len(set(d)) == 1:
        return False
    def calc(base):
        r = (sum(int(a) * b for a, b in zip(base, range(len(base) + 1, 1, -1))) * 10) % 11
        return "0" if r == 10 else str(r)
    return d[9] == calc(d[:9]) and d[10] == calc(d[:10])


def extrair(texto):
    achados = []
    for m in RE_CNPJ.finditer(texto or ""):
        d = re.sub(r"\D", "", m.group(1))
        if _dv_cnpj(d):
            achados.append((d, "CNPJ"))
    for m in RE_CPF.finditer(texto or ""):
        d = re.sub(r"\D", "", m.group(1))
        if _dv_cpf(d):
            achados.append((d, "CPF"))
    return achados


def run():
    ch = clickhouse_connect.get_client(host=os.environ["CLICKHOUSE_HOST"], password=os.environ["CLICKHOUSE_PASSWORD"])
    ch.command("CREATE DATABASE IF NOT EXISTS analytics_stg")
    ch.command("DROP TABLE IF EXISTS analytics_stg.chatwoot_documentos_citados")
    ch.command(
        """CREATE TABLE analytics_stg.chatwoot_documentos_citados (
            contato_id Int64 COMMENT 'contacts.id no Chatwoot',
            conversa_id Int64 COMMENT 'conversations.id no Chatwoot',
            documento String COMMENT 'CPF (11) ou CNPJ (14), so digitos, com digito verificador valido',
            tipo_documento LowCardinality(String) COMMENT 'CPF ou CNPJ',
            origem_evidencia LowCardinality(String) COMMENT 'mensagem_cliente, mensagem_empresa ou atributo_conversa',
            referencia String COMMENT 'id da mensagem ou nome do atributo da conversa onde o documento apareceu',
            citado_em Nullable(DateTime64(3, 'UTC')) COMMENT 'quando foi citado (UTC)'
        ) ENGINE = MergeTree ORDER BY (documento, contato_id)
        COMMENT 'CPF/CNPJ citados nas conversas do Chatwoot, validados por digito verificador. Chave forte para vincular lead a cliente do CRM.'"""
    )
    linhas = []
    # 1. texto de mensagens (so as que tem sequencia numerica candidata, o filtro grosso fica no ClickHouse)
    q = """
        SELECT m.id, m.conversation_id, c.contact_id, m.message_type, m.created_at, m.content
        FROM trusted_chatwoot.messages AS m FINAL
        JOIN trusted_chatwoot.conversations AS c FINAL ON c.id = m.conversation_id
        WHERE c.contact_id IS NOT NULL AND match(ifNull(m.content, ''), '[0-9]{3}\\\\.?[0-9]{3}\\\\.?[0-9]{3}-?[0-9]{2}')
    """
    for mid, conv, contato, mtype, ts, content in ch.query(q).result_rows:
        origem = "mensagem_cliente" if mtype == 0 else "mensagem_empresa"
        for doc, tipo in set(extrair(content)):
            linhas.append([contato, conv, doc, tipo, origem, str(mid), ts])
    # 2. atributos da conversa que a automacao preenche com o CNPJ informado
    q2 = """
        SELECT c.contact_id, c.id, c.updated_at, JSONExtractString(ifNull(c.custom_attributes, '{}'), k) AS v, k
        FROM trusted_chatwoot.conversations AS c FINAL
        ARRAY JOIN ['cnpj_pending_number', 'cnpj_ownership_unconfirmed_number'] AS k
        WHERE c.contact_id IS NOT NULL AND v != ''
    """
    for contato, conv, ts, v, k in ch.query(q2).result_rows:
        digitos = re.sub(r"\D", "", v)
        achados = [(digitos, "CNPJ")] if _dv_cnpj(digitos) else extrair(v)
        for doc, tipo in set(achados):
            linhas.append([contato, conv, doc, tipo, "atributo_conversa", k, ts])
    if linhas:
        ch.insert("analytics_stg.chatwoot_documentos_citados", linhas,
                  column_names=["contato_id", "conversa_id", "documento", "tipo_documento", "origem_evidencia", "referencia", "citado_em"])
    n = ch.query("SELECT count(), uniqExact(documento), uniqExact(contato_id) FROM analytics_stg.chatwoot_documentos_citados").result_rows[0]
    print("documentos citados:", n)
    return n


if __name__ == "__main__":
    run()
