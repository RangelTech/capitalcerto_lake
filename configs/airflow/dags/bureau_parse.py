"""Parser por regra das notas de analise de credito (Dora) e de integridade (DeskData) do Chatwoot da Capital Certo.
Sem modelo de linguagem. Cada funcao devolve um dict de campos; campo ausente na nota fica None (nunca 0)."""
import re
import unicodedata


def sem_acento(t):
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def brl(s):
    """'1.565,28' -> 1565.28"""
    try:
        return float(s.replace(".", "").replace(",", "."))
    except Exception:  # noqa
        return None


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


R_MOEDA = r"R\$\s*([\d\.]+,\d{2})"


def _ultima_moeda(linha):
    m = re.findall(R_MOEDA, linha)
    return brl(m[-1]) if m else None


def _secao(txt, ini, fim_opcoes):
    """Texto entre o rotulo `ini` e o primeiro rotulo de `fim_opcoes` que vem depois."""
    m = re.search(ini, txt, re.I)
    if not m:
        return ""
    resto = txt[m.end():]
    fim = len(resto)
    for f in fim_opcoes:
        k = re.search(f, resto, re.I)
        if k:
            fim = min(fim, k.start())
    return resto[:fim]


def _grupo_divida(sec, rotulo_re):
    """Dentro da secao de dividas (PF ou PJ), acha 'ROTULO ... N ocorrencias/registros ... Saldo total: R$ X'."""
    res = {"qtd": None, "valor": None}
    for linha in sec.split("\n"):
        if re.search(rotulo_re, linha, re.I):
            q = re.search(r"(\d+)\s*(?:ocorr|registro|cheque|protest)", linha, re.I)
            if q:
                res["qtd"] = int(q.group(1))
                v = re.search(r"(?:saldo[^R]{0,25}|total[^R]{0,15}|:)\s*" + R_MOEDA, linha, re.I)
                res["valor"] = brl(v.group(1)) if v else (_ultima_moeda(linha) if res["qtd"] else 0.0)
                if res["qtd"] == 0 and res["valor"] is None:
                    res["valor"] = 0.0
                return res
    return res


def parse_dora(txt):
    t = txt
    ta = sem_acento(t)
    out = {}
    g = lambda pat, src=ta, fl=re.I: (re.search(pat, src, fl) or [None, None])[1]  # noqa: E731

    out["cpf"] = so_digitos(g(r"CPF do (?:Tomador|Solicitante)[^:\n]*:\**\s*([\d\.\-]{11,14})")) or None
    out["cnpj_consultado"] = so_digitos(g(r"CNPJ Consultado[^:\n]*:\**\s*([\d\./\-]{14,18})")) or None
    out["cnpj_informado"] = so_digitos(g(r"CNPJ do Tomador Informado[^:\n]*:\**\s*([\d\./\-]{14,18})")) or None
    out["data_abertura_cnpj"] = g(r"Data de Abertura do CNPJ[^:\n]*:\**\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})")
    out["data_nascimento"] = g(r"Data de Nascimento[^:\n]*:\**\s*(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})")

    linha_pf = g(r"(Score PF[^\n]*)")
    if linha_pf:
        m = re.search(r"Score PF[^\d\n]{0,12}(\d{1,4})", linha_pf, re.I)
        out["score_pf"] = int(m.group(1)) if m else None
        m = re.search(r"faixa\s*([A-H])\b", linha_pf, re.I)
        out["faixa_pf"] = m.group(1).upper() if m else None
        m = re.search(r"inadimpl[^\d]{0,40}([\d\.,]+)\s*%", linha_pf, re.I)
        out["taxa_inadimplencia_pf"] = brl(m.group(1)) if m else None
    linha_pj = g(r"(Score PJ[^\n]*)")
    if linha_pj:
        m = re.search(r"Score PJ[^\d\n]{0,12}(\d{1,4})", linha_pj, re.I)
        out["score_pj"] = int(m.group(1)) if m else None
        out["pj_default"] = bool(re.search(r"DEFAULT", linha_pj, re.I))
    out["status_cadastral_pj"] = (g(r"Status Cadastral PJ[^:\n]*:\**\s*([A-Z]+)", ta, 0) or None)
    out["cnae"] = g(r"CNAE\s*(\d{7})")
    linha_end = g(r"(Endereco do CNPJ[^\n]*)")
    if linha_end:
        m = re.search(r"CEP\s*(\d{8})", linha_end)
        out["cep_cnpj"] = m.group(1) if m else None
        m = re.search(r"([A-Z][A-Z \.\-']+?)/([A-Z]{2})\s*\**\s*$", linha_end.strip())
        if m:
            out["cidade_cnpj"], out["uf_cnpj"] = m.group(1).strip(), m.group(2)
    # secoes de dividas
    fim_pf = [r"Dividas do CNPJ", r"2\.10\.", r"Dividas Totais", r"Processos Judiciais"]
    sec_pf = _secao(ta, r"2\.9\.\s*Dividas do CPF|Dividas do CPF\s*(?:\(SERASA PF\))?\s*:", fim_pf)
    sec_pj = _secao(ta, r"2\.10\.\s*Dividas do CNPJ|Dividas do CNPJ\s*(?:\(SERASA PJ\))?\s*:", [r"2\.11\.", r"Dividas Totais", r"Processos Judiciais", r"2\.12\."])
    for esf, sec in (("pf", sec_pf), ("pj", sec_pj)):
        if not sec:
            continue
        for nome, rot in (("pefin", r"\bPEFIN\b"), ("refin", r"\bREFIN\b"), ("protesto", r"Cartorio|Protesto"),
                          ("cheque", r"Cheques? sem fundo|CCF"), ("cobranca", r"cobranca")):
            r = _grupo_divida(sec, rot)
            out[f"{nome}_{esf}_qtd"], out[f"{nome}_{esf}_valor"] = r["qtd"], r["valor"]
        tot = re.search(r"Total (?:de )?dividas (?:do )?" + ("CPF" if esf == "pf" else "CNPJ") + r"[^\n]*", sec, re.I)
        if tot:
            out[f"divida_total_{esf}"] = _ultima_moeda(tot.group(0))
    # processos judiciais (contagem) e falencias
    for esf in ("pf", "pj"):
        m = re.search(r"Processos Judiciais " + esf.upper() + r"[^:\n]*:\**\s*(\d+)", ta, re.I)
        out[f"processos_{esf}_qtd"] = int(m.group(1)) if m else None
    # cnpjs vinculados
    out["cnpj_vinculado_negativado"] = bool(re.search(r"hasNegative:\s*true|Negativacao:\s*SIM", ta, re.I)) if re.search(r"CNPJs? Vinculados", ta, re.I) else None
    # veredito
    m = re.search(r"VEREDITO", ta, re.I)
    if m:
        trecho = ta[m.end(): m.end() + 120]
        k = re.search(r"NEGATIVO|POSITIVO|ALERTA|ANALISE MANUAL|APROVADO|REJEITADO|REPROVADO", trecho, re.I)
        out["veredito"] = k.group(0).upper() if k else None
    out["tamanho"] = len(t)
    return out


def parse_deskdata(txt):
    ta = sem_acento(txt)
    out = {}
    out["cpf"] = so_digitos((re.search(r"CPF do solicitante\**\s*:\s*\**\s*([\d\.\-]{11,14})", ta, re.I) or [None, ""])[1]) or None
    m = re.search(r"Veredito de Identidade\**\s*:\s*\**\s*\W*\s*(CONFIRMAD[OA]|DIVERGENCIA|IMPOSSIVEL[^\n]{0,30}|NOME[^\n]{0,20})", ta, re.I)
    out["identidade"] = m.group(1).split()[0].upper() if m else None
    m = re.search(r"VEREDITO\**\s*:\s*\**\s*\[?\s*([A-Z ]{4,20})", ta)
    out["veredito_integridade"] = m.group(1).strip() if m else None
    m = re.search(r"Situacao Cadastral\**\s*:\s*\**\s*([A-Z]+)", ta)
    out["situacao_cpf"] = m.group(1) if m else None
    m = re.search(r"Data de Nascimento\**\s*:\s*\**\s*(\d{2}/\d{2}/\d{4})[^\n]{0,20}?\((\d{1,3}) anos", ta)
    if m:
        out["data_nascimento"], out["idade"] = m.group(1), int(m.group(2))
    m = re.search(r"Media de vendas mensais\**\s*:\s*\**\s*([^\n]+)", ta, re.I)
    if m:
        s = m.group(1)
        v = re.search(R_MOEDA, s)
        out["vendas_mensais_texto"] = s.strip()[:60]
        out["vendas_mensais_valor"] = brl(v.group(1)) if v else None
        out["vendas_mensais_minimo"] = bool(re.search(r"mais de|acima|maior", s, re.I))
    m = re.search(r"Maquininhas? de cartao\**\s*:\s*\**\s*([^\n]+)", ta, re.I)
    out["maquininha"] = m.group(1).strip()[:40] if m else None
    m = re.search(r"Vinculo da maquininha\**\s*:\s*\**\s*([^\n]+)", ta, re.I)
    if m:
        s = m.group(1).lower()
        out["vinculo_maquininha"] = "terceiro" if "terceiro" in s and "proprio" not in s[:30] else ("proprio" if "proprio" in s else ("nao_informado" if "nao" in s else "outro"))
    m = re.search(r"Tempo de abertura do CNPJ\**\s*:\s*\**\s*([^\n]+)", ta, re.I)
    out["tempo_abertura_cnpj_texto"] = m.group(1).strip()[:50] if m else None
    m = re.search(r"E-?mail vinculado ao CPF\**\s*:\s*\**\s*([^\n]+)", ta, re.I)
    out["email_cpf"] = m.group(1).strip()[:60] if m else None
    # processos
    out["processos_total"] = len(re.findall(r"\*\*Processo \d+\*\*", ta)) or (int(x.group(1)) if (x := re.search(r"Identificados (\d+) processos", ta, re.I)) else None)
    out["processos_ativos"] = len(re.findall(r"Status:\s*ATIVO", ta, re.I))
    out["processos_polo_passivo"] = len(re.findall(r"Papel:\s*(EXECUTAD|REU)", ta, re.I))
    if re.search(r"(ausencia de processos|nenhum processo|sem processos)", ta, re.I) and out["processos_total"] in (None, 0):
        out["processos_total"] = 0
    out["tamanho"] = len(txt)
    return out


def classifica(txt):
    ta = sem_acento(txt).upper()
    if "SISTEMA DORA" in ta or ("SCORE PF" in ta and "VEREDITO" in ta) or "RELATORIO EXECUTIVO" in ta:
        return "dora"
    if "RELATORIO DE INTEGRIDADE" in ta or "DESKDATA" in ta or "VEREDITO DE IDENTIDADE" in ta:
        return "integridade"
    return "outro"
