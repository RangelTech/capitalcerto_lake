# Resumo por pessoa (cliente do CRM) escrito por um LLM pequeno em CPU (llama.cpp, Qwen3), A PARTIR DE FATOS ESTRUTURADOS da base: CRM, risco do motor,
# bureau (Dora e DeskData), PDFs (extratos), audio (transcricao), imagens (classificacao CLIP) e trechos da conversa. O LLM so reescreve os fatos; todo
# numero do texto tem de existir nos fatos, senao o resumo e descartado e entra o resumo estruturado (montado por modelo fixo, sem LLM).
# Gravado em analytics.resumo_pessoa; a API de risco apenas le (a geracao leva ~40 s por pessoa em CPU, entao roda na carga diaria, so para quem mudou).
# Uso: python credit_resumo.py [limite] [threads] [todos|recentes]
import json
import re
import sys
import time

import numpy as np
import pandas as pd

import credit_features as cf

MODELO = "/tmp/llm/Qwen3-1.7B-Q8_0.gguf"
VERSAO = "resumo-2026-11-10"
DDL = """CREATE TABLE IF NOT EXISTS analytics.resumo_pessoa (
    cliente_id String COMMENT 'cliente do CRM (analytics.dim_cliente)',
    gerado_em DateTime COMMENT 'quando o resumo foi gerado',
    resumo_estruturado String COMMENT 'resumo montado por modelo fixo a partir dos fatos (sem LLM): sempre disponivel',
    resumo_llm String COMMENT 'resumo escrito pelo LLM a partir dos fatos; vazio se reprovado na checagem de numeros',
    llm_ok UInt8 COMMENT '1 se o resumo do LLM passou na checagem (todo numero existe nos fatos)',
    modelo LowCardinality(String) COMMENT 'modelo usado', versao LowCardinality(String) COMMENT 'versao do gerador',
    fatos String COMMENT 'JSON com os fatos usados (auditoria)', segundos_llm Float32 COMMENT 'tempo gasto pelo LLM'
) ENGINE = ReplacingMergeTree(gerado_em) ORDER BY cliente_id
COMMENT 'Resumo por cliente: um estruturado (sempre) e um escrito por LLM pequeno a partir dos mesmos fatos. A API de risco devolve os dois.'"""


def _n(x, f="{:,.0f}"):
    try:
        return f.format(float(x)).replace(",", ".") if x is not None and not pd.isna(x) else None
    except Exception:  # noqa
        return None


def fatos_clientes(ch, limite=None, so_recentes=False):
    cli = ch.query_df("""select cliente_id, nome, tipo_pessoa, segmento, uf, cidade, idade, situacao_atual, qtd_contratos, total_emprestado, total_pago,
                                maior_atraso_em_aberto_dias, valor_vencido_em_aberto
                         from analytics.dim_cliente where empresa = 'Capital Certo' and excluido = 0""")
    cts = ch.query_df("""select f.cliente_id as cliente_id, f.data_inicio as data_inicio, f.valor_principal as valor, f.status as status, f.parcelas_pagas as pagas, f.parcelas_atrasadas as atrasadas,
                                f.maior_atraso_em_aberto_dias as atraso, d.provedor_pagamento as provedor, d.pix_agendado as pix, s.prob_calote as prob, s.faixa_risco as faixa, s.confianca_modelo as conf
                         from analytics.fato_contrato f left join analytics.dim_contrato d on d.contrato_id = f.contrato_id
                         left join analytics.score_contrato_atual s on s.contrato_id = f.contrato_id
                         where f.empresa = 'Capital Certo' order by f.data_inicio""")
    pon = ch.query_df("select cliente_id, toInt64(contato_id) as contato_id from analytics.ponte_lead_cliente where vinculo_valido = 1")
    fc = ch.query_df("""select toInt64(contato_id) as contato_id, conversa_id, estagio_atual, dora_veredito, dora_score_pf, dora_faixa_pf, dora_divida_total_pf, dora_pefin_pf_qtd, dora_protesto_pf_qtd, dora_processos_pf_qtd,
                               int_identidade, int_vendas_mensais_valor, int_vinculo_maquininha, int_processos_ativos, conversa_criada_em
                        from analytics.ficha_credito_conversa order by conversa_criada_em""")
    anex = ch.query_df("select conversation_id as conversa_id, attachment_id, file_type from trusted_chatwoot.anexo_arquivo final where status = 'ok'")
    pdf = ch.query_df("select attachment_id, tipo_documento, emissor, total_entradas, total_vendido, tem_emprestimo from trusted_chatwoot.anexo_pdf final")
    aud = ch.query_df("select attachment_id, texto, valores_citados from trusted_chatwoot.anexo_audio final")
    img = ch.query_df("select attachment_id, p_fachada_comercio, p_captura_tela, p_pessoa, p_interior_loja, p_casa_residencial from trusted_chatwoot.anexo_imagem final")
    msgs = ch.query_df("""select conversation_id as conversa_id, created_at as quando, content from trusted_chatwoot.messages final
                          where message_type = 0 and private = 0 and content is not null and length(content) > 25 order by created_at""")
    if so_recentes:
        rec = ch.query_df("select distinct cliente_id from analytics.ponte_lead_cliente p join trusted_chatwoot.conversations c on toInt64(c.contact_id) = toInt64(p.contato_id) "
                          "where p.vinculo_valido = 1 and c.updated_at >= now() - interval 3 day")
        cli = cli[cli["cliente_id"].isin(rec["cliente_id"])]
    if limite:
        cli = cli.sort_values("qtd_contratos", ascending=False).head(limite)
    saida = []
    for r in cli.itertuples():
        ct = cts[cts["cliente_id"] == r.cliente_id]
        contatos = pon[pon["cliente_id"] == r.cliente_id]["contato_id"].tolist()
        f = fc[fc["contato_id"].isin(contatos)]
        convs = f["conversa_id"].tolist()
        a = anex[anex["conversa_id"].isin(convs)]
        fato = {"cliente": {"tipo": r.tipo_pessoa, "segmento": r.segmento or None, "uf": (r.uf if r.uf not in (None, "", "NI") else None), "idade": _n(r.idade), "situacao_no_crm": r.situacao_atual, "contratos": int(r.qtd_contratos or 0),
                            "total_emprestado_reais": _n(r.total_emprestado), "total_pago_reais": _n(r.total_pago), "maior_atraso_em_aberto_dias": _n(r.maior_atraso_em_aberto_dias),
                            "valor_vencido_em_aberto_reais": _n(r.valor_vencido_em_aberto)},
                "contratos": [{"inicio": str(c.data_inicio)[:10], "valor_reais": _n(c.valor), "status": c.status, "parcelas_pagas": _n(c.pagas), "parcelas_atrasadas": _n(c.atrasadas),
                               "provedor": c.provedor, "pix_agendado": None if pd.isna(c.pix) else bool(c.pix), "risco_prob_calote_pct": None if pd.isna(c.prob) else _n(c.prob * 100, "{:.0f}"),
                               "faixa_risco": c.faixa, "confianca_modelo": c.conf} for c in ct.tail(4).itertuples()]}
        if len(f):
            u = f.iloc[-1]
            fato["bureau"] = {k: v for k, v in {"estagio_funil": (None if pd.isna(u["estagio_atual"]) or u["estagio_atual"] in ("Michael", "Adriel") else u["estagio_atual"]), "veredito_ia": u["dora_veredito"], "score_pf": _n(u["dora_score_pf"]), "faixa_pf": u["dora_faixa_pf"],
                                               "divida_total_pf_reais": _n(u["dora_divida_total_pf"]), "pefin_pf_qtd": _n(u["dora_pefin_pf_qtd"]), "protestos_pf_qtd": _n(u["dora_protesto_pf_qtd"]),
                                               "processos_pf_qtd": _n(u["dora_processos_pf_qtd"]), "identidade": u["int_identidade"], "vendas_mensais_informadas_reais": _n(u["int_vendas_mensais_valor"]),
                                               "maquininha": u["int_vinculo_maquininha"], "processos_ativos": _n(u["int_processos_ativos"])}.items() if not pd.isna(v) and v not in ("", "nan")}
        if len(a):
            ids = a["attachment_id"].tolist()
            m = {"imagens": int((a["file_type"] == 0).sum()), "audios": int((a["file_type"] == 1).sum()), "videos": int((a["file_type"] == 2).sum()), "arquivos": int((a["file_type"] == 3).sum())}
            p = pdf[pdf["attachment_id"].isin(ids)]
            if len(p):
                m["extratos"] = int((p["tipo_documento"] == "extrato").sum())
                m["relatorios_de_vendas"] = int((p["tipo_documento"] == "relatorio_vendas").sum())
                if p["total_entradas"].notna().any():
                    m["maior_entrada_em_extrato_reais"] = _n(p["total_entradas"].max())
            i = img[img["attachment_id"].isin(ids)]
            if len(i):
                m["fotos_de_fachada"] = int((i["p_fachada_comercio"] > 0.5).sum())
                m["prints_de_aplicativo"] = int((i["p_captura_tela"] > 0.5).sum())
            au = aud[aud["attachment_id"].isin(ids)]
            if len(au):
                m["trechos_de_audio"] = [re.sub(r"\s+", " ", t)[:220] for t in au["texto"].head(2)]
            fato["midia"] = m
        mm = msgs[msgs["conversa_id"].isin(convs)].head(3)
        if len(mm):
            fato["trechos_da_conversa_do_cliente"] = [re.sub(r"\s+", " ", t)[:200] for t in mm["content"]]
        saida.append((r.cliente_id, fato))
    return saida


def estruturado(f):
    c = f["cliente"]
    partes = [f"Cliente {c.get('tipo') or 'sem tipo'}" + (f", segmento {c['segmento']}" if c.get("segmento") else "") + (f", {c['uf']}" if c.get("uf") else "")
              + (f", {c['idade']} anos" if c.get("idade") else "") + f". Situação no CRM: {c.get('situacao_no_crm') or 'sem contrato'}; {c['contratos']} contrato(s)"
              + (f", R$ {c['total_emprestado_reais']} emprestados e R$ {c['total_pago_reais']} pagos" if c.get("total_emprestado_reais") else "") + "."]
    if c.get("valor_vencido_em_aberto_reais") and c["valor_vencido_em_aberto_reais"] != "0":
        partes.append(f"Vencido em aberto: R$ {c['valor_vencido_em_aberto_reais']} (maior atraso {c.get('maior_atraso_em_aberto_dias')} dias).")
    for k in f.get("contratos", [])[-2:]:
        if k.get("risco_prob_calote_pct") is not None:
            partes.append(f"Contrato de {k['inicio']} (R$ {k['valor_reais']}, {k['status']}): risco de calote {k['risco_prob_calote_pct']}% ({k['faixa_risco']}, confiança {k['confianca_modelo']}).")
    b = f.get("bureau")
    itens = [f"{k.replace('_', ' ')} {v}" for k, v in (b or {}).items() if k in ("veredito_ia", "score_pf", "faixa_pf", "divida_total_pf_reais", "identidade", "vendas_mensais_informadas_reais", "maquininha")]
    if itens:
        partes.append("Bureau: " + "; ".join(itens) + ".")
    m = f.get("midia")
    if m:
        partes.append("Anexos: " + ", ".join(f"{v} {k}" for k, v in m.items() if isinstance(v, int) and v) + (f"; maior entrada em extrato R$ {m['maior_entrada_em_extrato_reais']}" if m.get("maior_entrada_em_extrato_reais") else "") + ".")
    return " ".join(partes)


def prompt(f):
    base = estruturado(f)
    trechos = f.get("trechos_da_conversa_do_cliente", []) + [t for t in (f.get("midia", {}) or {}).get("trechos_de_audio", [])]
    p = ("Reescreva o RESUMO BASE abaixo como um parecer técnico de crédito em português do Brasil, em 4 a 6 linhas de texto corrido. "
         "Use SOMENTE o que está no RESUMO BASE e nos TRECHOS. NÃO acrescente nenhuma informação, causa, região, nome, estado do contrato ou conclusão que não esteja escrita. "
         "Copie os números exatamente como estão. Termine com uma frase de conclusão que repita o risco indicado no resumo base." + chr(10) + chr(10) + "RESUMO BASE:" + chr(10) + base)
    if trechos:
        p += chr(10) + chr(10) + "TRECHOS DO CLIENTE (diga em uma frase o assunto, sem citar nomes):" + chr(10) + "- " + (chr(10) + "- ").join(trechos[:4])
    return p


def numeros_ok(texto, fatos_json):
    """Todo numero do resumo precisa existir nos fatos (ignora numeros de 1 digito)."""
    fn = set(re.findall(r"\d+(?:[\.,]\d+)*", fatos_json))
    fn |= {x.replace(".", "").replace(",", ".") for x in fn} | {x.replace(".", "") for x in fn}
    texto = re.sub(r",00", "", texto)
    for n in re.findall(r"\d+(?:[\.,]\d+)*", texto):
        if len(n.replace(".", "").replace(",", "")) <= 1:
            continue
        if n not in fn and n.replace(".", "") not in fn and n.replace(".", "").replace(",", ".") not in fn:
            return False, n
    return True, None


def main():
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    thr = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    modo = sys.argv[3] if len(sys.argv) > 3 else "todos"
    ch = cf.conecta()
    ch.command(DDL)
    fatos = fatos_clientes(ch, lim, so_recentes=(modo == "recentes"))
    print(f"clientes a resumir: {len(fatos)}", flush=True)
    linhas, t0 = [], time.time()
    for i, (cid, f) in enumerate(fatos, 1):
        fj = json.dumps(f, ensure_ascii=False)
        est = estruturado(f)
        t = time.time()
        txt, ok = "", 0   # resumo por regra (estruturado): sem LLM em CPU; o LLM e opcional e fica no Data IA Hub
        linhas.append({"cliente_id": cid, "gerado_em": pd.Timestamp.now(), "resumo_estruturado": est, "resumo_llm": txt if ok else "", "llm_ok": ok, "modelo": "regra (sem LLM)", "versao": VERSAO,
                       "fatos": fj, "segundos_llm": float(time.time() - t)})
        if i % 5 == 0 or i == len(fatos):
            ch.insert_df("analytics.resumo_pessoa", pd.DataFrame(linhas))
            print(f"{i}/{len(fatos)} | {time.time() - t0:.0f}s | aprovados pela checagem: {sum(x['llm_ok'] for x in linhas)}/{len(linhas)}", flush=True)
            linhas = []


if __name__ == "__main__":
    main()
