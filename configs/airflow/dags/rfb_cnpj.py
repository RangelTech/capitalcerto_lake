# Ingestao mensal da base de CNPJ da Receita Federal para o ClickHouse da Capital Certo (banco trusted_rfb).
# Fonte: compartilhamento publico da Receita (WebDAV, uma pasta AAAA-MM por mes, ZIPs com CSV ';' em ISO-8859-1, sem cabecalho).
# Estrategia: cada ZIP e baixado para disco temporario, descompactado em streaming, convertido para UTF-8 e inserido como texto
# em raw_rfb.<tabela>__novo; depois uma passada INSERT SELECT tipa tudo em trusted_rfb.<tabela>__novo e a troca e atomica
# (EXCHANGE TABLES). A base fica com so o snapshot mais recente; _controle guarda qual mes foi carregado.
# Layout dos arquivos: https://www.gov.br/receitafederal/pt-br/assuntos/orientacao-tributaria/cadastros/consultas/dados-publicos-cnpj
import os
import re
import tempfile
import time
import zipfile

import pendulum
import requests

WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav"
TOKEN = "YggdBLfdninEJX9"  # token do compartilhamento publico (nao e segredo: esta na URL publica da Receita)
RAW_DB, TRUSTED_DB = "raw_rfb", "trusted_rfb"

# tabela -> (prefixo do arquivo, quantidade de particoes, colunas na ordem do arquivo)
LAYOUT = {
    "empresas": ("Empresas", 10, ["cnpj_basico", "razao_social", "natureza_juridica", "qualificacao_responsavel", "capital_social", "porte_empresa", "ente_federativo_responsavel"]),
    "estabelecimentos": ("Estabelecimentos", 10, ["cnpj_basico", "cnpj_ordem", "cnpj_dv", "identificador_matriz_filial", "nome_fantasia", "situacao_cadastral", "data_situacao_cadastral",
                                                  "motivo_situacao_cadastral", "nome_cidade_exterior", "pais", "data_inicio_atividade", "cnae_fiscal_principal", "cnae_fiscal_secundaria",
                                                  "tipo_logradouro", "logradouro", "numero", "complemento", "bairro", "cep", "uf", "municipio", "ddd_1", "telefone_1", "ddd_2", "telefone_2",
                                                  "ddd_fax", "fax", "correio_eletronico", "situacao_especial", "data_situacao_especial"]),
    "simples": ("Simples", 1, ["cnpj_basico", "opcao_simples", "data_opcao_simples", "data_exclusao_simples", "opcao_mei", "data_opcao_mei", "data_exclusao_mei"]),
    "socios": ("Socios", 10, ["cnpj_basico", "identificador_socio", "nome_socio", "cnpj_cpf_socio", "qualificacao_socio", "data_entrada_sociedade", "pais", "representante_legal",
                              "nome_representante", "qualificacao_representante", "faixa_etaria"]),
    "cnaes": ("Cnaes", 1, ["codigo", "descricao"]),
    "motivos": ("Motivos", 1, ["codigo", "descricao"]),
    "municipios": ("Municipios", 1, ["codigo", "descricao"]),
    "naturezas": ("Naturezas", 1, ["codigo", "descricao"]),
    "paises": ("Paises", 1, ["codigo", "descricao"]),
    "qualificacoes": ("Qualificacoes", 1, ["codigo", "descricao"]),
}
DIMS = ["cnaes", "motivos", "municipios", "naturezas", "paises", "qualificacoes"]

DATA = lambda c: f"if({c} IN ('', '0', '00000000'), NULL, toDate(parseDateTimeBestEffortOrNull({c})))"
# tipagem: tabela -> (expressoes SELECT na ordem das colunas finais, ORDER BY)
TIPAGEM = {
    "empresas": ("cnpj_basico, razao_social, natureza_juridica, qualificacao_responsavel, toFloat64OrNull(replaceOne(capital_social, ',', '.')) AS capital_social, "
                 "multiIf(porte_empresa = '01', 'micro_empresa', porte_empresa = '03', 'empresa_pequeno_porte', porte_empresa = '05', 'demais', 'nao_informado') AS porte_empresa, ente_federativo_responsavel", "cnpj_basico"),
    "estabelecimentos": ("cnpj_basico, cnpj_ordem, cnpj_dv, concat(cnpj_basico, cnpj_ordem, cnpj_dv) AS cnpj, if(identificador_matriz_filial = '1', 'matriz', 'filial') AS matriz_ou_filial, nome_fantasia, "
                         "multiIf(situacao_cadastral = '01', 'nula', situacao_cadastral = '02', 'ativa', situacao_cadastral = '03', 'suspensa', situacao_cadastral = '04', 'inapta', situacao_cadastral = '08', 'baixada', 'outra') AS situacao_cadastral, "
                         + DATA("data_situacao_cadastral") + " AS data_situacao_cadastral, motivo_situacao_cadastral, " + DATA("data_inicio_atividade") + " AS data_inicio_atividade, "
                         "cnae_fiscal_principal, cnae_fiscal_secundaria, tipo_logradouro, logradouro, numero, complemento, bairro, cep, uf, municipio AS municipio_codigo, "
                         "ddd_1, telefone_1, correio_eletronico", "cnpj_basico, cnpj_ordem"),
    "simples": ("cnpj_basico, opcao_simples = 'S' AS optante_simples, " + DATA("data_opcao_simples") + " AS data_opcao_simples, " + DATA("data_exclusao_simples") + " AS data_exclusao_simples, "
                "opcao_mei = 'S' AS optante_mei, " + DATA("data_opcao_mei") + " AS data_opcao_mei, " + DATA("data_exclusao_mei") + " AS data_exclusao_mei", "cnpj_basico"),
    "socios": ("cnpj_basico, multiIf(identificador_socio = '1', 'pessoa_juridica', identificador_socio = '2', 'pessoa_fisica', 'estrangeiro') AS tipo_socio, nome_socio, cnpj_cpf_socio AS documento_mascarado_socio, "
               "qualificacao_socio, " + DATA("data_entrada_sociedade") + " AS data_entrada_sociedade, faixa_etaria", "cnpj_basico"),
}
for d in DIMS:
    TIPAGEM[d] = ("codigo, descricao", "codigo")

DESC_TABELA = {
    "empresas": "Base de CNPJ da Receita Federal: uma linha por empresa (CNPJ basico, 8 digitos) com razao social, natureza juridica, capital social e porte.",
    "estabelecimentos": "Base de CNPJ da Receita Federal: uma linha por estabelecimento (matriz ou filial) com situacao cadastral, data de abertura, CNAE, endereco e contatos.",
    "simples": "Base de CNPJ da Receita Federal: opcao pelo Simples Nacional e pelo MEI por empresa, com datas de entrada e saida.",
    "socios": "Base de CNPJ da Receita Federal: quadro societario. O CPF do socio vem mascarado pela Receita (so os 6 digitos do meio).",
    "cnaes": "Tabela de apoio da Receita: codigo e descricao do CNAE.", "motivos": "Tabela de apoio da Receita: motivos de situacao cadastral.",
    "municipios": "Tabela de apoio da Receita: codigo (da Receita, nao do IBGE) e nome dos municipios.", "naturezas": "Tabela de apoio da Receita: naturezas juridicas.",
    "paises": "Tabela de apoio da Receita: paises.", "qualificacoes": "Tabela de apoio da Receita: qualificacoes de socios e responsaveis.",
}
DESC_COL = {
    "cnpj_basico": "primeiros 8 digitos do CNPJ (identifica a empresa; une empresas, estabelecimentos, simples e socios)",
    "cnpj_ordem": "4 digitos de ordem do estabelecimento (0001 = matriz)", "cnpj_dv": "2 digitos verificadores", "cnpj": "CNPJ completo de 14 digitos",
    "razao_social": "nome empresarial", "natureza_juridica": "codigo da natureza juridica (liga a naturezas.codigo)", "qualificacao_responsavel": "qualificacao do responsavel (liga a qualificacoes.codigo)",
    "capital_social": "capital social em R$", "porte_empresa": "micro_empresa, empresa_pequeno_porte, demais ou nao_informado", "matriz_ou_filial": "matriz ou filial",
    "nome_fantasia": "nome fantasia", "situacao_cadastral": "ativa, suspensa, inapta, baixada, nula ou outra", "data_situacao_cadastral": "data da situacao cadastral atual",
    "motivo_situacao_cadastral": "codigo do motivo (liga a motivos.codigo)", "data_inicio_atividade": "data de abertura do estabelecimento", "cnae_fiscal_principal": "CNAE principal (liga a cnaes.codigo)",
    "cnae_fiscal_secundaria": "CNAEs secundarios separados por virgula", "cep": "CEP do estabelecimento", "uf": "UF do estabelecimento", "municipio_codigo": "codigo do municipio na Receita (liga a municipios.codigo)",
    "correio_eletronico": "e-mail cadastrado na Receita (dado pessoal)", "optante_simples": "opcao pelo Simples Nacional", "optante_mei": "opcao pelo MEI", "tipo_socio": "pessoa_juridica, pessoa_fisica ou estrangeiro",
    "nome_socio": "nome do socio (dado pessoal)", "documento_mascarado_socio": "CPF ou CNPJ do socio como a Receita publica (CPF mascarado)", "faixa_etaria": "faixa etaria do socio (1 a 9; 0 = nao se aplica)",
    "codigo": "codigo", "descricao": "descricao",
}


def _s():
    s = requests.Session()
    s.auth = (TOKEN, "")
    return s


def listar(path):
    r = _s().request("PROPFIND", WEBDAV + path, data='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>',
                     headers={"Depth": "1", "Content-Type": "application/xml"}, timeout=90)
    r.raise_for_status()
    out = {}
    for resp in re.findall(r"<d:response>.*?</d:response>", r.text, re.S):
        h = re.search(r"<d:href>([^<]+)", resp).group(1).rstrip("/").split("/")[-1]
        t = re.search(r"getcontentlength>(\d+)", resp)
        out[h] = int(t.group(1)) if t else 0
    return out


LANDING_BUCKET, LANDING_PREFIXO = "landing", "receita"


def _minio():
    from airflow.providers.amazon.aws.hooks.s3 import S3Hook

    return S3Hook(aws_conn_id="minio_s3").get_conn()


def _arquivos_no_minio():
    """{mes: {arquivo: tamanho}} do que ja foi depositado em landing/receita/AAAA-MM/ por uma maquina no Brasil."""
    out = {}
    try:
        s3 = _minio()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=LANDING_BUCKET, Prefix=f"{LANDING_PREFIXO}/"):
            for o in page.get("Contents", []):
                _, mes, nome = o["Key"].split("/", 2)
                out.setdefault(mes, {})[nome] = o["Size"]
    except Exception as e:
        print("MinIO indisponivel para listar:", str(e)[:120])
    return out


def _arquivos_da_receita(mes):
    try:
        return listar(f"/{mes}/")
    except Exception:
        return {}


ESPELHO = "https://dados-abertos-rf-cnpj.casadosdados.com.br/arquivos"  # espelho publico da base da Receita; responde de fora do Brasil


def _pastas_do_espelho():
    """{AAAA-MM: 'AAAA-MM-DD'} das pastas do espelho (a pasta tem a data de publicacao; vale a mais nova de cada mes)."""
    try:
        r = requests.get(ESPELHO + "/", timeout=30)
        r.raise_for_status()
        out = {}
        for d in re.findall(r'href="(\d{4}-\d{2}-\d{2})/"', r.text):
            out[d[:7]] = max(out.get(d[:7], ""), d)
        return out
    except Exception as e:
        print("espelho indisponivel:", str(e)[:100])
        return {}


def _arquivos_do_espelho(mes):
    pasta = _pastas_do_espelho().get(mes)
    if not pasta:
        return {}
    try:
        r = requests.get(f"{ESPELHO}/{pasta}/", timeout=30)
        r.raise_for_status()
        return {n: 1 for n in re.findall(r'href="([A-Za-z]+\d?\.zip)"', r.text)}
    except Exception:
        return {}


def _esperados():
    return [f"{LAYOUT[t][0]}{i}.zip" for t in LAYOUT for i in (range(LAYOUT[t][1]) if LAYOUT[t][1] > 1 else [""])]


def mes_mais_recente():
    """Pasta AAAA-MM mais nova com todos os arquivos disponiveis na Receita, no espelho ou ja no MinIO.
    A Receita recusa conexao da VPS (Franca); nesse caso o espelho publico supre, e o MinIO guarda o que ja foi baixado."""
    no_minio = _arquivos_no_minio()
    meses = set(no_minio) | set(_pastas_do_espelho())
    try:
        meses |= {m for m in listar("/") if re.fullmatch(r"\d{4}-\d{2}", m)}
    except Exception as e:
        print("Receita inacessivel daqui:", str(e)[:100])
    for mes in sorted(meses, reverse=True):
        disponivel = set(no_minio.get(mes, {})) | set(_arquivos_da_receita(mes)) | set(_arquivos_do_espelho(mes))
        if not [n for n in _esperados() if n not in disponivel]:
            return mes
    raise RuntimeError("nenhuma pasta mensal completa na Receita, no espelho nem em landing/receita/ no MinIO")


def _baixar_url(url, destino, auth=None, tentativas=4):
    for tentativa in range(tentativas):
        try:
            with requests.get(url, auth=auth, stream=True, timeout=(30, 120)) as r:
                r.raise_for_status()
                with open(destino, "wb") as f:
                    for bloco in r.iter_content(8 * 1024 * 1024):
                        f.write(bloco)
            return True
        except Exception:
            time.sleep(5 * (tentativa + 1))
    return False


def _baixar(mes, nome, destino):
    """Ordem: cache no MinIO (landing/receita) -> Receita -> espelho publico. O que vier de fora e arquivado no MinIO."""
    try:
        s3 = _minio()
        s3.head_object(Bucket=LANDING_BUCKET, Key=f"{LANDING_PREFIXO}/{mes}/{nome}")
        s3.download_file(LANDING_BUCKET, f"{LANDING_PREFIXO}/{mes}/{nome}", destino)
        return "minio"
    except Exception:
        pass
    origem = None
    if _baixar_url(f"{WEBDAV}/{mes}/{nome}", destino, auth=(TOKEN, ""), tentativas=1):
        origem = "receita"
    else:
        pasta = _pastas_do_espelho().get(mes)
        if pasta and _baixar_url(f"{ESPELHO}/{pasta}/{nome}", destino):
            origem = "espelho"
    if not origem:
        raise RuntimeError(f"falha ao baixar {nome} da Receita e do espelho")
    try:
        _minio().upload_file(destino, LANDING_BUCKET, f"{LANDING_PREFIXO}/{mes}/{nome}")
    except Exception as e:
        print("aviso: nao arquivou no MinIO:", str(e)[:100])
    return origem


def limpar_minio(manter=2):
    """Retencao: mantem so os meses mais recentes de ZIPs no MinIO (cada mes pesa ~7,8 GB)."""
    s3 = _minio()
    meses = sorted(_arquivos_no_minio())
    for mes in meses[:-manter] if manter else meses:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=LANDING_BUCKET, Prefix=f"{LANDING_PREFIXO}/{mes}/"):
            for o in page.get("Contents", []):
                s3.delete_object(Bucket=LANDING_BUCKET, Key=o["Key"])
        print("removido do MinIO (retencao):", mes)


def _utf8(fileobj):
    while True:
        bloco = fileobj.read(8 * 1024 * 1024)
        if not bloco:
            return
        yield bloco.decode("latin-1").encode("utf-8")


def _ch():
    import clickhouse_connect

    return clickhouse_connect.get_client(host=os.environ["CLICKHOUSE_HOST"], password=os.environ["CLICKHOUSE_PASSWORD"], send_receive_timeout=3600)


def carregar_raw(tabela, mes):
    """Baixa os ZIPs da tabela, converte e insere como texto em raw_rfb.<tabela>__novo."""
    prefixo, n, cols = LAYOUT[tabela]
    ch = _ch()
    ch.command(f"CREATE DATABASE IF NOT EXISTS {RAW_DB}")
    ch.command(f"DROP TABLE IF EXISTS {RAW_DB}.`{tabela}__novo`")
    ch.command(f"CREATE TABLE {RAW_DB}.`{tabela}__novo` ({', '.join(f'`{c}` String' for c in cols)}) ENGINE = MergeTree ORDER BY tuple()")
    nomes = [f"{prefixo}{i}.zip" for i in range(n)] if n > 1 else [f"{prefixo}.zip"]
    total = 0
    for nome in nomes:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            destino = os.path.join(tmp, nome)
            origem = _baixar(mes, nome, destino)
            print(f"{tabela}: {nome} veio de {origem}", flush=True)
            with zipfile.ZipFile(destino) as z:
                for inner in z.namelist():
                    with z.open(inner) as f:
                        ch.raw_insert(f"{RAW_DB}.`{tabela}__novo`", column_names=cols, insert_block=_utf8(f), fmt="CSV",
                                      settings={"format_csv_delimiter": ";", "input_format_defaults_for_omitted_fields": 1})
        linhas = ch.query(f"SELECT count() FROM {RAW_DB}.`{tabela}__novo`").result_rows[0][0]
        print(f"{tabela}: {nome} ok, total {linhas} linhas", flush=True)
        total = linhas
    if total == 0:
        raise RuntimeError(f"{tabela} ficou vazia")
    return total


def tipar_e_publicar(tabela, mes):
    ch = _ch()
    esc = lambda s: s.replace("\\", "\\\\").replace("'", "\\'")
    ch.command(f"CREATE DATABASE IF NOT EXISTS {TRUSTED_DB}")
    ch.command(f"DROP TABLE IF EXISTS {TRUSTED_DB}.`{tabela}__novo`")
    sel, order = TIPAGEM[tabela]
    ch.command(f"CREATE TABLE {TRUSTED_DB}.`{tabela}__novo` ENGINE = MergeTree ORDER BY ({order}) AS SELECT {sel} FROM {RAW_DB}.`{tabela}__novo`")
    cols = [r[0] for r in ch.query(f"SELECT name FROM system.columns WHERE database='{TRUSTED_DB}' AND table='{tabela}__novo'").result_rows]
    for c in cols:
        if c in DESC_COL:
            ch.command(f"ALTER TABLE {TRUSTED_DB}.`{tabela}__novo` COMMENT COLUMN `{c}` '{esc(DESC_COL[c])}'")
    ch.command(f"ALTER TABLE {TRUSTED_DB}.`{tabela}__novo` MODIFY COMMENT '{esc(DESC_TABELA[tabela] + ' Snapshot de ' + mes + ' (publicacao mensal da Receita).')}'")
    n_raw = ch.query(f"SELECT count() FROM {RAW_DB}.`{tabela}__novo`").result_rows[0][0]
    n_tip = ch.query(f"SELECT count() FROM {TRUSTED_DB}.`{tabela}__novo`").result_rows[0][0]
    if n_raw != n_tip:
        raise RuntimeError(f"{tabela}: raw {n_raw} != trusted {n_tip}")
    existe = ch.query(f"SELECT count() FROM system.tables WHERE database='{TRUSTED_DB}' AND name='{tabela}'").result_rows[0][0]
    if existe:
        ch.command(f"EXCHANGE TABLES {TRUSTED_DB}.`{tabela}` AND {TRUSTED_DB}.`{tabela}__novo`")
        ch.command(f"DROP TABLE {TRUSTED_DB}.`{tabela}__novo`")
    else:
        ch.command(f"RENAME TABLE {TRUSTED_DB}.`{tabela}__novo` TO {TRUSTED_DB}.`{tabela}`")
    ch.command(f"DROP TABLE {RAW_DB}.`{tabela}__novo`")  # a raw e so degrau; o ZIP original continua na Receita
    ch.command(f"""CREATE TABLE IF NOT EXISTS {TRUSTED_DB}._controle (mes_referencia String, tabela String, linhas UInt64, carregado_em DateTime DEFAULT now())
                   ENGINE = ReplacingMergeTree(carregado_em) ORDER BY (tabela) COMMENT 'qual mes da Receita esta carregado em cada tabela'""")
    ch.insert(f"{TRUSTED_DB}._controle", [[mes, tabela, n_tip, pendulum.now("UTC").naive()]], column_names=["mes_referencia", "tabela", "linhas", "carregado_em"])
    print(f"{tabela}: publicada ({n_tip} linhas, mes {mes})", flush=True)
    return n_tip


def ja_carregado(mes):
    ch = _ch()
    try:
        r = ch.query(f"SELECT uniqExact(tabela), min(mes_referencia), max(mes_referencia) FROM {TRUSTED_DB}._controle FINAL").result_rows[0]
    except Exception:
        return False
    return r[0] >= len(LAYOUT) and r[1] == mes and r[2] == mes


NORM = "trimBoth(replaceRegexpAll(normalizeUTF8NFD(lowerUTF8(ifNull({x}, ''))), '[^a-z ]', ''))"

# Cada item: (tabela, sql, descricoes de coluna). {ref} = data de referencia (ultima atividade do CRM).
ENRIQUECIMENTO = []

ENRIQUECIMENTO.append(("dim_cnpj_cliente", """
CREATE OR REPLACE TABLE analytics.dim_cnpj_cliente ENGINE = MergeTree ORDER BY cliente_id
COMMENT 'DIMENSAO. Cadastro da Receita Federal dos clientes pessoa juridica da Capital Certo (CNPJ de 14 digitos): segmento real (CNAE), idade da empresa, situacao cadastral, porte, capital, MEI/Simples, localizacao e quantidade de socios. Snapshot mensal da base publica; liga a dim_cliente por cliente_id e a dim_socio_cliente por cnpj_basico.' AS
SELECT c.cliente_id AS cliente_id, c.pessoa_id AS pessoa_id, c.tenant AS tenant, c.empresa AS empresa, e.cnpj_basico AS cnpj_basico, right(e.cnpj, 4) AS documento_final,
       e.matriz_ou_filial AS matriz_ou_filial, em.razao_social AS razao_social, e.situacao_cadastral AS situacao_cadastral, e.data_inicio_atividade AS data_abertura,
       if(e.data_inicio_atividade IS NULL, NULL, dateDiff('month', e.data_inicio_atividade, toDate('{ref}'))) AS idade_empresa_meses,
       em.porte_empresa AS porte_receita, em.capital_social AS capital_social, em.natureza_juridica AS natureza_codigo, nat.descricao AS natureza_juridica,
       e.cnae_fiscal_principal AS cnae_codigo, cn.descricao AS cnae_descricao, substring(e.cnae_fiscal_principal, 1, 2) AS cnae_divisao,
       e.uf AS uf_receita, e.municipio_codigo AS municipio_codigo, mu.descricao AS municipio_receita, e.cep AS cep_receita,
       ifNull(s.optante_mei, false) AS optante_mei, ifNull(s.optante_simples, false) AS optante_simples, ifNull(so.qtd_socios, 0) AS qtd_socios,
       length(e.cnae_fiscal_secundaria) > 0 AS tem_cnae_secundario
FROM analytics.dim_cliente c
JOIN trusted_credhub.customer cr ON cr.id = c.cliente_id
JOIN trusted_rfb.estabelecimentos e ON e.cnpj = cr.cpf_cnpj
LEFT JOIN trusted_rfb.empresas em ON em.cnpj_basico = e.cnpj_basico
LEFT JOIN trusted_rfb.simples s ON s.cnpj_basico = e.cnpj_basico
LEFT JOIN trusted_rfb.cnaes cn ON cn.codigo = e.cnae_fiscal_principal
LEFT JOIN trusted_rfb.naturezas nat ON nat.codigo = em.natureza_juridica
LEFT JOIN trusted_rfb.municipios mu ON mu.codigo = e.municipio_codigo
LEFT JOIN (SELECT cnpj_basico, count() AS qtd_socios FROM trusted_rfb.socios GROUP BY cnpj_basico) so ON so.cnpj_basico = e.cnpj_basico
WHERE c.tipo_pessoa = 'CNPJ' AND NOT c.excluido
""", {"cnpj_basico": "8 primeiros digitos do CNPJ, une as tabelas da Receita", "idade_empresa_meses": "meses entre a abertura e a data de referencia",
      "situacao_cadastral": "ativa, suspensa, inapta, baixada, nula ou outra, HOJE (pode ser consequencia de inadimplencia)", "cnae_divisao": "2 primeiros digitos do CNAE (divisao)",
      "qtd_socios": "socios no quadro societario da Receita (MEI normalmente 0)", "municipio_codigo": "codigo de municipio da Receita (nao e o do IBGE)"}))

ENRIQUECIMENTO.append(("dim_socio_cliente", """
CREATE OR REPLACE TABLE analytics.dim_socio_cliente ENGINE = MergeTree ORDER BY (cliente_id, nome_socio)
COMMENT 'DIMENSAO. Quadro societario (Receita Federal) das empresas clientes: uma linha por cliente CNPJ e socio, com qualificacao, entrada na sociedade e faixa etaria. O CPF do socio vem mascarado pela Receita (so os 6 digitos do meio).' AS
SELECT c.cliente_id AS cliente_id, c.pessoa_id AS pessoa_id, c.empresa AS empresa, c.cnpj_basico AS cnpj_basico, s.tipo_socio AS tipo_socio, s.nome_socio AS nome_socio,
       s.documento_mascarado_socio AS documento_mascarado_socio, q.descricao AS qualificacao_socio, s.data_entrada_sociedade AS data_entrada_sociedade,
       if(s.data_entrada_sociedade IS NULL, NULL, dateDiff('month', s.data_entrada_sociedade, toDate('{ref}'))) AS meses_na_sociedade,
       multiIf(s.faixa_etaria = '1', '0 a 12', s.faixa_etaria = '2', '13 a 20', s.faixa_etaria = '3', '21 a 30', s.faixa_etaria = '4', '31 a 40', s.faixa_etaria = '5', '41 a 50',
               s.faixa_etaria = '6', '51 a 60', s.faixa_etaria = '7', '61 a 70', s.faixa_etaria = '8', '71 a 80', s.faixa_etaria = '9', 'acima de 80', 'nao se aplica') AS faixa_etaria_socio,
       count() OVER (PARTITION BY c.cliente_id) AS socios_do_cliente
FROM analytics.dim_cnpj_cliente c
JOIN trusted_rfb.socios s ON s.cnpj_basico = c.cnpj_basico
LEFT JOIN trusted_rfb.qualificacoes q ON q.codigo = s.qualificacao_socio
""", {"tipo_socio": "pessoa_juridica, pessoa_fisica ou estrangeiro", "documento_mascarado_socio": "documento como a Receita publica (CPF mascarado)", "socios_do_cliente": "quantidade de socios do cliente"}))

ENRIQUECIMENTO.append(("ponte_rede_socios", """
CREATE OR REPLACE TABLE analytics.ponte_rede_socios ENGINE = MergeTree ORDER BY (cliente_id, nome_socio)
COMMENT 'PONTE. Para cada socio pessoa fisica de um cliente, quantas empresas ele tem na base inteira da Receita (mesmo nome e mesmo CPF mascarado), quantas ativas, e com quantos OUTROS clientes da Capital Certo ele divide sociedade. Base para sinal de rede (socio com varias empresas).' AS
WITH alvos AS (SELECT DISTINCT nome_socio, documento_mascarado_socio FROM analytics.dim_socio_cliente WHERE tipo_socio = 'pessoa_fisica' AND nome_socio != ''),
     empresas_do_socio AS (
         SELECT s.nome_socio AS nome_socio, s.documento_mascarado_socio AS documento_mascarado_socio, s.cnpj_basico AS cnpj_basico
         FROM trusted_rfb.socios s JOIN alvos a ON a.nome_socio = s.nome_socio AND a.documento_mascarado_socio = s.documento_mascarado_socio WHERE s.tipo_socio = 'pessoa_fisica'),
     ativas AS (
         SELECT cnpj_basico, max(situacao_cadastral = 'ativa') AS ativa FROM trusted_rfb.estabelecimentos
         WHERE matriz_ou_filial = 'matriz' AND cnpj_basico IN (SELECT cnpj_basico FROM empresas_do_socio) GROUP BY cnpj_basico),
     compart AS (SELECT nome_socio, documento_mascarado_socio, uniqExact(cliente_id) AS n_clientes FROM analytics.dim_socio_cliente WHERE tipo_socio = 'pessoa_fisica' GROUP BY nome_socio, documento_mascarado_socio),
     por_socio AS (
         SELECT e.nome_socio AS nome_socio, e.documento_mascarado_socio AS documento_mascarado_socio, uniqExact(e.cnpj_basico) AS qtd_empresas_do_socio,
                uniqExactIf(e.cnpj_basico, a.ativa) AS qtd_empresas_ativas_do_socio
         FROM empresas_do_socio e LEFT JOIN ativas a ON a.cnpj_basico = e.cnpj_basico GROUP BY e.nome_socio, e.documento_mascarado_socio)
SELECT d.cliente_id AS cliente_id, d.cnpj_basico AS cnpj_basico, d.nome_socio AS nome_socio, d.qualificacao_socio AS qualificacao_socio, p.qtd_empresas_do_socio AS qtd_empresas_do_socio,
       p.qtd_empresas_ativas_do_socio AS qtd_empresas_ativas_do_socio,
       cmp.n_clientes - 1 AS outros_clientes_com_o_mesmo_socio
FROM analytics.dim_socio_cliente d JOIN por_socio p ON p.nome_socio = d.nome_socio AND p.documento_mascarado_socio = d.documento_mascarado_socio
JOIN compart cmp ON cmp.nome_socio = d.nome_socio AND cmp.documento_mascarado_socio = d.documento_mascarado_socio
WHERE d.tipo_socio = 'pessoa_fisica'
""", {"qtd_empresas_do_socio": "empresas (CNPJ basico) em que o socio aparece na base inteira, por nome + CPF mascarado", "qtd_empresas_ativas_do_socio": "dessas, as com matriz ativa",
      "outros_clientes_com_o_mesmo_socio": "outros clientes da Capital Certo em que o mesmo socio aparece (rede entre clientes)"}))

ENRIQUECIMENTO.append(("ponte_cliente_como_socio", """
CREATE OR REPLACE TABLE analytics.ponte_cliente_como_socio ENGINE = MergeTree ORDER BY (cliente_id, cnpj_basico)
COMMENT 'PONTE. Para clientes pessoa fisica (e responsaveis de CNPJ), as empresas da base da Receita em que a pessoa aparece como socia: casamento por nome e pelos 6 digitos do meio do CPF (a Receita mascara o resto). Dado indicativo: nome + 6 digitos nao e identificador unico.' AS
WITH pf AS (
    SELECT c.cliente_id AS cliente_id, c.pessoa_id AS pessoa_id,
           concat('***', substring(if(length(cr.cpf_cnpj) = 11, cr.cpf_cnpj, cr.responsible_cpf), 4, 6), '**') AS mascarado,
           trimBoth(replaceRegexpAll(normalizeUTF8NFD(lowerUTF8(ifNull(cr.name, ''))), '[^a-z ]', '')) AS nome_norm
    FROM analytics.dim_cliente c JOIN trusted_credhub.customer cr ON cr.id = c.cliente_id
    WHERE c.tipo_pessoa = 'CPF' AND NOT c.excluido AND length(cr.cpf_cnpj) = 11),
     achados AS (
    SELECT p.cliente_id AS cliente_id, s.cnpj_basico AS cnpj_basico, s.data_entrada_sociedade AS data_entrada_sociedade
    FROM trusted_rfb.socios s
    JOIN pf p ON s.documento_mascarado_socio = p.mascarado AND trimBoth(replaceRegexpAll(normalizeUTF8NFD(lowerUTF8(s.nome_socio)), '[^a-z ]', '')) = p.nome_norm
    WHERE s.tipo_socio = 'pessoa_fisica')
SELECT a.cliente_id AS cliente_id, a.cnpj_basico AS cnpj_basico, em.razao_social AS razao_social, est.situacao_cadastral AS situacao_cadastral, est.data_inicio_atividade AS data_abertura,
       est.cnae_fiscal_principal AS cnae_codigo, cn.descricao AS cnae_descricao, est.uf AS uf_receita, a.data_entrada_sociedade AS data_entrada_sociedade
FROM achados a
LEFT JOIN trusted_rfb.empresas em ON em.cnpj_basico = a.cnpj_basico
LEFT JOIN (SELECT cnpj_basico, any(situacao_cadastral) AS situacao_cadastral, any(data_inicio_atividade) AS data_inicio_atividade, any(cnae_fiscal_principal) AS cnae_fiscal_principal, any(uf) AS uf
           FROM trusted_rfb.estabelecimentos WHERE matriz_ou_filial = 'matriz' AND cnpj_basico IN (SELECT cnpj_basico FROM achados) GROUP BY cnpj_basico) est ON est.cnpj_basico = a.cnpj_basico
LEFT JOIN trusted_rfb.cnaes cn ON cn.codigo = est.cnae_fiscal_principal
""", {}))

for nome, tabela_rfb, desc in [("dim_cnae", "cnaes", "DIMENSAO. CNAE (codigo da Receita e descricao), com a divisao de 2 digitos."),
                               ("dim_natureza_juridica", "naturezas", "DIMENSAO. Naturezas juridicas da Receita."),
                               ("dim_municipio_receita", "municipios", "DIMENSAO. Municipios com o codigo da Receita (nao e o do IBGE).")]:
    extra = ", substring(codigo, 1, 2) AS divisao" if nome == "dim_cnae" else ""
    ENRIQUECIMENTO.append((nome, f"CREATE OR REPLACE TABLE analytics.{nome} ENGINE = MergeTree ORDER BY codigo COMMENT '{desc}' AS SELECT codigo, descricao{extra} FROM trusted_rfb.{tabela_rfb}", {}))


DESC_COL_ANALYTICS = {
    "cliente_id": "cliente no CRM, liga a dim_cliente.cliente_id", "pessoa_id": "hash do CPF/CNPJ do cliente", "tenant": "CNPJ da empresa no CRM (capital-certo ou castelo-forte)",
    "empresa": "empresa dona do CNPJ do tenant: Capital Certo agrupa os dois", "documento_final": "ultimos 4 digitos do documento", "matriz_ou_filial": "matriz ou filial",
    "razao_social": "nome empresarial na Receita", "data_abertura": "data de abertura do estabelecimento na Receita", "porte_receita": "micro_empresa, empresa_pequeno_porte, demais ou nao_informado",
    "capital_social": "capital social em R$", "natureza_codigo": "codigo da natureza juridica", "natureza_juridica": "descricao da natureza juridica", "cnae_codigo": "CNAE fiscal principal",
    "cnae_descricao": "descricao do CNAE principal", "uf_receita": "UF do estabelecimento na Receita", "municipio_receita": "municipio do estabelecimento na Receita", "cep_receita": "CEP na Receita",
    "optante_mei": "optante pelo MEI", "optante_simples": "optante pelo Simples Nacional", "tem_cnae_secundario": "tem CNAE secundario", "cnpj_basico": "8 primeiros digitos do CNPJ",
    "nome_socio": "nome do socio como a Receita publica (dado pessoal)", "qualificacao_socio": "qualificacao do socio (administrador, socio, titular...)", "data_entrada_sociedade": "entrada do socio na sociedade",
    "meses_na_sociedade": "meses de sociedade ate a data de referencia", "faixa_etaria_socio": "faixa etaria do socio publicada pela Receita", "codigo": "codigo", "descricao": "descricao",
    "divisao": "2 primeiros digitos do CNAE", "razao_social_socio": "nome empresarial", "situacao_cadastral": "situacao cadastral na Receita", "data_abertura_empresa": "abertura",
}


def enriquecer(ref):
    ch = _ch()
    esc = lambda x: x.replace("\\", "\\\\").replace("'", "\\'")
    for nome, sql, cols in ENRIQUECIMENTO:
        ch.command(sql.replace("{ref}", ref), settings={"allow_suspicious_low_cardinality_types": 1})
        n = ch.query(f"SELECT count() FROM analytics.{nome}").result_rows[0][0]
        todas = {**DESC_COL_ANALYTICS, **cols}
        existentes = [r[0] for r in ch.query(f"SELECT name FROM system.columns WHERE database='analytics' AND table='{nome}'").result_rows]
        for c in existentes:
            if c in todas:
                ch.command(f"ALTER TABLE analytics.{nome} COMMENT COLUMN `{c}` '{esc(todas[c])}'")
        print(f"analytics.{nome}: {n} linhas", flush=True)
