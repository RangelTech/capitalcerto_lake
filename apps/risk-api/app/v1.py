# API v1 do motor de credito da Capital Certo: consulta uma pessoa por TELEFONE (preferido), CPF ou CNPJ, junta tudo que a base tem
# (CRM CredHub, Chatwoot, bureau Dora e DeskData, midia, scores dos contratos) e infere o risco de um contrato (existentes e simulacao de um novo).
# Os dados sao os da ultima carga diaria do lake (madrugada); a inferencia roda na hora, com os modelos que o motor salva no MinIO.
import hmac
import io
import json
import os
import re
import threading
import time
from datetime import date

import boto3
import joblib
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.clickhouse_client import get_client

router = APIRouter(prefix="/v1", tags=["motor de credito"])

REGIAO_FISCAL = {"1": "DF/GO/MS/MT/TO", "2": "AC/AP/AM/PA/RO/RR", "3": "CE/MA/PI", "4": "AL/PB/PE/RN", "5": "BA/SE", "6": "MG", "7": "ES/RJ", "8": "SP", "9": "PR/SC", "0": "RS"}
DDD_UF = {}
for _uf, _d in {"SP": "11 12 13 14 15 16 17 18 19", "RJ": "21 22 24", "ES": "27 28", "MG": "31 32 33 34 35 37 38", "PR": "41 42 43 44 45 46", "SC": "47 48 49", "RS": "51 53 54 55",
                "DF": "61", "GO": "62 64", "TO": "63", "MT": "65 66", "MS": "67", "AC": "68", "RO": "69", "BA": "71 73 74 75 77", "SE": "79", "PE": "81 87", "AL": "82", "PB": "83", "RN": "84",
                "CE": "85 88", "PI": "86 89", "PA": "91 93 94", "AM": "92 97", "RR": "95", "AP": "96", "MA": "98 99"}.items():
    for _x in _d.split():
        DDD_UF[_x] = _uf
CEP_UF = [(1000, 19999, "SP"), (20000, 28999, "RJ"), (29000, 29999, "ES"), (30000, 39999, "MG"), (40000, 48999, "BA"), (49000, 49999, "SE"), (50000, 56999, "PE"), (57000, 57999, "AL"),
          (58000, 58999, "PB"), (59000, 59999, "RN"), (60000, 63999, "CE"), (64000, 64999, "PI"), (65000, 65999, "MA"), (66000, 68899, "PA"), (68900, 68999, "AP"), (69000, 69299, "AM"),
          (69300, 69399, "RR"), (69400, 69899, "AM"), (69900, 69999, "AC"), (70000, 72799, "DF"), (72800, 72999, "GO"), (73000, 73699, "DF"), (73700, 76799, "GO"), (76800, 76999, "RO"),
          (77000, 77995, "TO"), (78000, 78899, "MT"), (79000, 79999, "MS"), (80000, 87999, "PR"), (88000, 89999, "SC"), (90000, 99999, "RS")]


# ------------------------------------------------------------------ seguranca
def exige_chave(x_api_key: str = Header(default=None)):
    chaves = [k.strip() for k in os.environ.get("RISK_API_KEYS", "").split(",") if k.strip()]
    if not chaves or not x_api_key or not any(hmac.compare_digest(x_api_key, k) for k in chaves):
        raise HTTPException(status_code=401, detail="chave de API ausente ou invalida (cabecalho X-API-Key)")


# ------------------------------------------------------------------ normalizacao
def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def normaliza_telefone(t):
    d = so_digitos(t)
    if d.startswith("55") and len(d) >= 12:
        d = d[2:]
    return d


def cep_para_uf(cep):
    d = so_digitos(cep)
    if len(d) != 8:
        return None
    n = int(d[:5])
    return next((uf for a, b, uf in CEP_UF if a <= n <= b), None)


def mascara_doc(d):
    d = so_digitos(d)
    return (d[:3] + ".***.***-" + d[-2:]) if len(d) == 11 else (d[:2] + ".***.***/" + d[8:12] + "-" + d[-2:]) if len(d) == 14 else d


# ------------------------------------------------------------------ modelos
_cache = {"t": 0.0, "m": None}
_trava = threading.Lock()


def carregar_modelos():
    with _trava:
        if _cache["m"] is None or time.time() - _cache["t"] > 1800:
            s3 = boto3.client("s3", endpoint_url=os.environ["MINIO_ENDPOINT"], aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
            obj = s3.get_object(Bucket="models", Key="credit_motor/ultimo/modelos.joblib")
            _cache["m"] = joblib.load(io.BytesIO(obj["Body"].read()))
            _cache["t"] = time.time()
    return _cache["m"]


def confianca_de(auc):
    if auc is None or auc != auc:
        return "nenhuma"
    return "alta" if auc >= 0.78 else "media" if auc >= 0.68 else "baixa" if auc >= 0.58 else "nenhuma"


def faixa_de(p):
    return "baixo" if p < 0.15 else "medio" if p < 0.30 else "alto" if p < 0.50 else "muito_alto"


def aplicar_calibrador(cal, p):
    tipo, obj = cal
    p = np.asarray(p, dtype=float)
    if tipo == "isotonic":
        return np.clip(obj.predict(p), 0.02, 0.98)
    lg = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4))).reshape(-1, 1)
    return np.clip(obj.predict_proba(lg)[:, 1], 0.02, 0.98)


def explicar(pipe, modelo, X, nums, cats, top=3):
    pre, m = pipe.named_steps["p"], pipe.named_steps["m"]
    Z = pre.transform(X)
    if hasattr(Z, "toarray"):
        Z = Z.toarray()
    if modelo in ("logistica", "logistica_L1"):
        nomes, contrib = list(pre.get_feature_names_out()), Z * m.coef_[0]
    elif modelo in ("rf", "hgb"):
        # perturbacao: quanto a probabilidade cai quando cada variavel e tratada como desconhecida (imputada pela mediana do treino)
        p0 = float(pipe.predict_proba(X)[0, 1])
        efeitos = []
        for c in list(nums) + list(cats):
            X2 = X.copy()
            X2[c] = pd.Series([np.nan] * len(X2), index=X2.index, dtype=object) if c in cats else np.nan
            efeitos.append((c, p0 - float(pipe.predict_proba(X2)[0, 1])))
        nomes = [c for c, _ in efeitos]
        contrib = np.array([[e for _, e in efeitos]])
    else:
        nomes, contrib = nums + cats, m.booster_.predict(Z, pred_contrib=True)[:, :-1]
    ordem = np.argsort(-contrib[0])[:top]
    return [{"fator": nomes[j].replace("n__", "").replace("c__", ""), "efeito": round(float(contrib[0][j]), 3)} for j in ordem if contrib[0][j] > 0]


# ------------------------------------------------------------------ consulta
class ContratoNovo(BaseModel):
    valor_principal: float | None = Field(default=None, description="valor do contrato em R$")
    qtd_parcelas_contratadas: int | None = None
    taxa_juros_pct: float | None = None
    valor_parcela: float | None = None
    provedor_pagamento: str | None = Field(default=None, description="PLUGGY, EFI, EFI_AUTOMATICO, ASAAS ou MANUAL (padrao PLUGGY)")
    pix_agendado: bool | None = Field(default=None, description="cobranca por Pix agendado (padrao: sim, e o que 94% dos contratos novos usam)")


class ConsultaRequest(BaseModel):
    telefone: str | None = Field(default=None, description="telefone com ou sem DDD/+55 (preferido)")
    cpf: str | None = None
    cnpj: str | None = None
    contrato: ContratoNovo | None = Field(default=None, description="parametros do contrato a simular (opcional)")


def _q(ch, sql, params=None):
    r = ch.query(sql, parameters=params or {})
    return [dict(zip(r.column_names, row)) for row in r.result_rows]


def _limpa(o):
    if isinstance(o, dict):
        return {k: _limpa(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_limpa(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (o != o or o in (float("inf"), float("-inf"))) else round(float(o), 4)
    if isinstance(o, (pd.Timestamp, date)):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def resolver(ch, tel, cpf, cnpj):
    """Acha clientes do CRM e contatos do Chatwoot pelos identificadores. Devolve (clientes, contatos, como_achou)."""
    clientes, contatos, como = {}, {}, []
    if tel:
        u8 = tel[-8:]
        ddd = tel[:2] if len(tel) >= 10 else ""
        for r in _q(ch, "SELECT id AS cliente_id, phone FROM trusted_credhub.customer WHERE right(replaceRegexpAll(ifNull(phone, ''), '[^0-9]', ''), 8) = {u:String}", {"u": u8}):
            if not ddd or ddd in so_digitos(r["phone"]):
                clientes[r["cliente_id"]] = True
                como.append("telefone no CRM")
        for r in _q(ch, "SELECT id AS contato_id, phone_number FROM trusted_chatwoot.contacts FINAL WHERE right(replaceRegexpAll(ifNull(phone_number, ''), '[^0-9]', ''), 8) = {u:String}", {"u": u8}):
            if not ddd or ddd in so_digitos(r["phone_number"]):
                contatos[int(r["contato_id"])] = True
                como.append("telefone no Chatwoot")
    for doc in [d for d in (so_digitos(cpf), so_digitos(cnpj)) if d]:
        for r in _q(ch, "SELECT id AS cliente_id FROM trusted_credhub.customer WHERE cpf_cnpj = {d:String} OR responsible_cpf = {d:String}", {"d": doc}):
            clientes[r["cliente_id"]] = True
            como.append("documento no CRM")
        for r in _q(ch, "SELECT DISTINCT toInt64(contato_id) AS contato_id FROM analytics.ficha_credito_conversa WHERE dora_cpf = {d:String} OR dora_cnpj_consultado = {d:String} OR int_cpf = {d:String}", {"d": doc}):
            contatos[int(r["contato_id"])] = True
            como.append("documento nos relatorios de bureau")
    if clientes:  # cliente -> contatos
        for r in _q(ch, "SELECT DISTINCT toInt64(contato_id) AS contato_id FROM analytics.ponte_lead_cliente WHERE vinculo_valido = 1 AND cliente_id IN {c:Array(String)}", {"c": list(clientes)}):
            contatos[int(r["contato_id"])] = True
    if contatos:  # contato -> clientes
        for r in _q(ch, "SELECT DISTINCT cliente_id FROM analytics.ponte_lead_cliente WHERE vinculo_valido = 1 AND toInt64(contato_id) IN {c:Array(Int64)}", {"c": list(contatos)}):
            clientes[r["cliente_id"]] = True
    return list(clientes), list(contatos), sorted(set(como))


def historico_vivo(ch, clientes):
    """Comportamento do cliente nos contratos existentes, como esta HOJE (para a simulacao de um contrato novo)."""
    if not clientes:
        return {"prev_pct_atrasadas": np.nan, "prev_atraso_max": np.nan, "e_renovacao": 0.0, "n_parcelas": 0}
    rows = _q(ch, "SELECT data_vencimento, data_pagamento, dias_atraso_ate_pagar, dias_atraso_em_aberto, vencida_em_aberto FROM analytics.fato_parcela WHERE cliente_id IN {c:Array(String)} AND contrato_excluido = 0 AND data_vencimento > toDate('2000-01-01') AND data_vencimento < today()", {"c": clientes})
    n_ct = _q(ch, "SELECT count() AS n FROM analytics.fato_contrato WHERE cliente_id IN {c:Array(String)}", {"c": clientes})[0]["n"]
    if not rows:
        return {"prev_pct_atrasadas": np.nan, "prev_atraso_max": np.nan, "e_renovacao": float(n_ct > 0), "n_parcelas": 0}
    atr = np.array([(r["dias_atraso_ate_pagar"] or 0) if r["data_pagamento"] else (r["dias_atraso_em_aberto"] or 0) for r in rows], dtype=float)
    atr = np.clip(atr, 0, None)
    return {"prev_pct_atrasadas": float((atr > 3).mean()), "prev_atraso_max": float(atr.max()), "e_renovacao": float(n_ct > 0), "n_parcelas": len(rows)}


def simular_novo(ch, clientes, cad, tel, params):
    modelos = carregar_modelos()
    m0 = modelos.get(0)
    if not m0:
        return {"disponivel": False, "motivo": "modelo da etapa D+0 indisponivel"}
    p = params or ContratoNovo()
    h = historico_vivo(ch, clientes)
    idade = np.nan
    if cad and cad.get("birth_date") and str(cad["birth_date"]) > "1920-01-01":
        idade = (date.today() - pd.Timestamp(cad["birth_date"]).date()).days / 365.25
    cep = cad.get("zipcode") if cad else None
    cpf_ref = (cad.get("cpf_cnpj") if cad and len(so_digitos(cad.get("cpf_cnpj"))) == 11 else (cad.get("responsible_cpf") if cad else None)) or ""
    cpf_ref = so_digitos(cpf_ref)
    provedor = (p.provedor_pagamento or "PLUGGY").upper()
    pix = 1.0 if (p.pix_agendado is None or p.pix_agendado) else 0.0
    razao = (p.valor_parcela / p.valor_principal) if p.valor_parcela and p.valor_principal else np.nan
    uf_ddd = DDD_UF.get(tel[:2]) if tel and len(tel) >= 10 else None
    feats = {"pix_agendado": pix, "provedor_pagamento": provedor, "e_renovacao": h["e_renovacao"], "prev_pct_atrasadas": h["prev_pct_atrasadas"], "prev_atraso_max": h["prev_atraso_max"],
             "idade_na_contratacao": idade, "cep_vazio": 0.0 if cep_para_uf(cep) else 1.0, "valor_principal": p.valor_principal if p.valor_principal else np.nan,
             "valor_parcela": p.valor_parcela if p.valor_parcela else np.nan, "qtd_parcelas_contratadas": p.qtd_parcelas_contratadas if p.qtd_parcelas_contratadas else np.nan,
             "taxa_juros_pct": p.taxa_juros_pct if p.taxa_juros_pct is not None else np.nan, "razao_parcela_principal": razao, "ordem_contrato_cliente": float((h["e_renovacao"] or 0) + 1),
             "tipo_pessoa": (cad or {}).get("tipo_pessoa"), "sexo": (cad or {}).get("sex"), "segmento": (cad or {}).get("segment"), "tipo_empresa": (cad or {}).get("company_type"),
             "regiao_fiscal_cpf": REGIAO_FISCAL.get(cpf_ref[8]) if len(cpf_ref) == 11 else None, "uf_ddd": uf_ddd, "uf_cep": cep_para_uf(cep), "uf_cadastro": (cad or {}).get("state"),
             "tem_cep": 1.0 if cep_para_uf(cep) else 0.0, "idade_cadastro": idade, "tempo_de_casa_dias": np.nan}
    cols = m0["nums"] + m0["cats"]
    X = pd.DataFrame([{c: feats.get(c, np.nan) for c in cols}])[cols]
    for c in m0["nums"]:
        X[c] = pd.to_numeric(X[c], errors="coerce").astype(float)
    for c in m0["cats"]:
        X[c] = X[c].astype(object).where(X[c].notna(), np.nan)
    p_bruta = m0["pipe"].predict_proba(X)[:, 1]
    prob = float(aplicar_calibrador(m0["cal"], p_bruta)[0])
    conf = confianca_de(m0["auc_oot"])
    avisos = ["No dia da contratacao o modelo separa pouco bom de mau pagador; use como complemento, nao como decisao unica."] if conf in ("nenhuma", "baixa") else []
    faltando = [c for c in cols if feats.get(c) is None or (isinstance(feats.get(c), float) and feats.get(c) != feats.get(c))]
    # leitura direta (sem modelo): taxa de calote OBSERVADA em contratos do mesmo perfil na contratacao (jun/2026 em diante, era do Pix agendado)
    modo = "Pix agendado" if pix else "sem Pix agendado"
    if not h["e_renovacao"]:
        hist = "1o contrato (sem historico)"
    elif h["prev_atraso_max"] == h["prev_atraso_max"] and h["prev_atraso_max"] <= 3:
        hist = "renovacao: pagou bem o anterior"
    else:
        hist = "renovacao: nao pagou bem o anterior"
    fx = "idade desconhecida" if idade != idade else ("ate 35" if idade <= 35 else "35 a 50" if idade <= 50 else "acima de 50")
    perfis = {"1 variavel: cobranca": modo, "2 variaveis: cobranca x historico": f"{modo} | {hist}", "2 variaveis: cobranca x provedor": f"{modo} | {provedor}",
              "3 variaveis: cobranca x historico x idade": f"{modo} | {hist} | {fx}"}
    leitura = []
    try:
        rows = _q(ch, "SELECT nivel, perfil, alvo, n, calote_observado, ic_lo, ic_hi FROM analytics_ml.motor_dia0_segmentos WHERE apenas_desde_jun26 = 1 AND alvo = 'calote entre D+1 e D+45' AND n >= 8 ORDER BY nivel")
        for r in rows:
            if perfis.get(r["nivel"]) == r["perfil"]:
                leitura.append({"perfil": r["perfil"], "contratos_com_resultado": r["n"], "calote_observado": round(r["calote_observado"], 3),
                                "intervalo_95": [round(r["ic_lo"], 3), round(r["ic_hi"], 3)], "definido_por": r["nivel"]})
    except Exception:  # noqa
        pass
    return {"disponivel": True, "etapa": 0, "prob_calote": round(prob, 4), "faixa_risco": faixa_de(prob), "confianca_modelo": conf, "auc_fora_do_tempo": m0["auc_oot"],
            "modelo": m0["modelo"], "features": m0["features"], "motivos": explicar(m0["pipe"], m0["modelo"], X, m0["nums"], m0["cats"]),
            "premissas": {"provedor_pagamento": provedor, "pix_agendado": bool(pix), "historico_do_cliente": {k: (None if (isinstance(v, float) and v != v) else v) for k, v in h.items()}},
            "variaveis_sem_dado": faltando, "avisos": avisos, "leitura_observada_dia0": leitura,
            "leitura": "prob_calote = probabilidade de 20% ou mais das parcelas (depois da contratacao, ate o dia 45) ficarem em aberto e vencidas."}


@router.post("/consulta", dependencies=[Depends(exige_chave)])
def consulta(req: ConsultaRequest):
    tel = normaliza_telefone(req.telefone) if req.telefone else None
    if not (tel or req.cpf or req.cnpj):
        raise HTTPException(status_code=422, detail="informe telefone, cpf ou cnpj")
    if tel and len(tel) < 8:
        raise HTTPException(status_code=422, detail="telefone muito curto")
    ch = get_client()
    t0 = time.time()
    clientes, contatos, como = resolver(ch, tel, req.cpf, req.cnpj)
    out = {"encontrado": bool(clientes or contatos), "consulta": {"telefone": tel, "cpf": so_digitos(req.cpf) or None, "cnpj": so_digitos(req.cnpj) or None}, "achou_por": como,
           "dados_de": "ultima carga diaria do lake (madrugada); a inferencia de risco roda na hora"}
    if not out["encontrado"]:
        out["risco"] = {"novo_contrato": simular_novo(ch, [], None, tel, req.contrato)}
        out["avisos"] = ["Nenhum registro encontrado: o risco abaixo usa so o que o pedido informa (sem historico)."]
        out["tempo_ms"] = int((time.time() - t0) * 1000)
        return _limpa(out)
    cad = None
    if clientes:
        cads = _q(ch, """SELECT c.id AS cliente_id, c.name AS nome, c.cpf_cnpj AS cpf_cnpj, c.responsible_cpf AS responsible_cpf, c.phone AS phone, c.email AS email, c.birth_date AS birth_date, c.sex AS sex,
                                c.segment AS segment, c.company_type AS company_type, c.created_at AS cliente_desde, a.zipcode AS zipcode, a.state AS state, a.city AS city, k.tipo_pessoa AS tipo_pessoa,
                                k.situacao_atual AS situacao_atual, k.qtd_contratos AS qtd_contratos, k.total_emprestado AS total_emprestado, k.total_pago AS total_pago, k.maior_atraso_em_aberto_dias AS maior_atraso_em_aberto_dias,
                                k.valor_vencido_em_aberto AS valor_vencido_em_aberto
                         FROM trusted_credhub.customer c
                         LEFT JOIN (SELECT entity_id, argMax(zipcode, updated_at) zipcode, argMax(state, updated_at) state, argMax(city, updated_at) city FROM trusted_credhub.addresses WHERE entity_type = 'CUSTOMER' GROUP BY entity_id) a ON a.entity_id = c.id
                         LEFT JOIN analytics.dim_cliente k ON k.cliente_id = c.id WHERE c.id IN {c:Array(String)}""", {"c": clientes})
        for r in cads:
            r["cpf_cnpj_mascarado"] = mascara_doc(r.get("cpf_cnpj"))
        cad = cads[0] if cads else None
        out["pessoas"] = [{k: v for k, v in r.items() if k not in ("cpf_cnpj", "responsible_cpf", "phone", "email")} | {"telefone": r.get("phone"), "email": r.get("email")} for r in cads]
        cts = _q(ch, """SELECT f.contrato_id AS contrato_id, f.cliente_id AS cliente_id, f.data_inicio AS data_inicio, f.valor_principal AS valor_principal, f.qtd_parcelas_contratadas AS parcelas,
                               f.status AS status, f.parcelas_pagas AS parcelas_pagas, f.parcelas_atrasadas AS parcelas_atrasadas, f.maior_atraso_em_aberto_dias AS maior_atraso_em_aberto_dias,
                               f.valor_vencido_em_aberto AS valor_vencido_em_aberto, f.saldo_em_aberto AS saldo_em_aberto, d.provedor_pagamento AS provedor_pagamento, d.pix_agendado AS pix_agendado,
                               s.prob_calote AS prob_calote, s.prob_inadimplencia AS prob_inadimplencia, s.prob_cluster_ruim AS prob_cluster_ruim, s.faixa_risco AS faixa_risco,
                               s.confianca_modelo AS confianca_modelo, s.etapa AS etapa, s.idade_dias AS idade_dias, s.fase AS fase,
                               s.motivos AS motivos, s.calote_observado AS calote_observado,
                               g.tipo_contrato AS tipo_contrato, g.cluster_trajetoria AS cluster_trajetoria, g.anterior_paga_e_para AS anterior_paga_e_para,
                               g.cmp_endereco_compartilhado AS endereco_compartilhado, g.inconsistencias AS inconsistencias
                        FROM analytics.fato_contrato f LEFT JOIN analytics.dim_contrato d ON d.contrato_id = f.contrato_id
                        LEFT JOIN analytics.score_contrato_atual s ON s.contrato_id = f.contrato_id
                        LEFT JOIN analytics.contrato_risco_360 g ON g.contrato_id = f.contrato_id
                        WHERE f.cliente_id IN {c:Array(String)} AND f.empresa = 'Capital Certo' ORDER BY f.data_inicio DESC""", {"c": clientes})
        for c in cts:
            if c.get("fase") == "encerrado":
                # contrato com mais de 50 dias: a previsao cobre so o dia da avaliacao ate o dia 45, que ja passou. As probabilidades abaixo sao RETROATIVAS (auditoria), nao previsao.
                c["aviso"] = ("Contrato antigo (mais de 50 dias): a previsao cobria so ate o dia 45, que ja passou. As probabilidades sao retroativas, "
                              "use calote_observado e a situacao real das parcelas.")
            if c.get("motivos"):
                try:
                    c["motivos"] = json.loads(c["motivos"])
                except Exception:  # noqa
                    pass
        out["crm"] = {"contratos": cts}
    if contatos:
        conv = _q(ch, """SELECT conversa_id, contato_id, inbox_id, conversa_criada_em, estagio_atual, vendido, reprovado, desistiu, desqualificado, tem_dora, tem_integridade, dora_criado_em,
                                dora_veredito, dora_score_pf, dora_faixa_pf, dora_score_pj, dora_pj_default, dora_divida_total_pf, dora_divida_total_pj, dora_pefin_pf_qtd, dora_pefin_pf_valor, dora_refin_pf_valor,
                                dora_protesto_pf_qtd, dora_protesto_pf_valor, dora_processos_pf_qtd, dora_cnae, dora_cidade_cnpj, dora_uf_cnpj, int_criado_em, int_identidade, int_veredito_integridade,
                                int_situacao_cpf, int_idade, int_vendas_mensais_valor, int_vendas_mensais_texto, int_maquininha, int_vinculo_maquininha, int_processos_total, int_processos_ativos,
                                attr_deskdata_status, attr_proposta_escolha
                         FROM analytics.ficha_credito_conversa WHERE toInt64(contato_id) IN {c:Array(Int64)} ORDER BY conversa_criada_em DESC LIMIT 15""", {"c": contatos})
        ncont = _q(ch, "SELECT id AS contato_id, name AS nome, phone_number AS telefone FROM trusted_chatwoot.contacts FINAL WHERE id IN {c:Array(Int64)}", {"c": contatos})
        convs = [c["conversa_id"] for c in conv]
        out["chatwoot"] = {"contatos": ncont, "conversas": conv}
        if convs:
            out["midia"] = _q(ch, """SELECT multiIf(file_type = 0, 'imagem', file_type = 1, 'audio', file_type = 2, 'video', 'arquivo') AS tipo, count() AS anexos, countIf(status = 'ok') AS baixados
                                     FROM trusted_chatwoot.anexo_arquivo FINAL WHERE conversation_id IN {c:Array(UInt64)} GROUP BY file_type ORDER BY file_type""", {"c": convs})
            ext = _q(ch, """SELECT countIf(p.tipo_documento = 'extrato') AS extratos, countIf(p.tipo_documento = 'relatorio_vendas') AS relatorios_de_vendas, max(p.total_entradas) AS maior_entrada_extrato
                            FROM trusted_chatwoot.anexo_pdf p FINAL JOIN trusted_chatwoot.anexo_arquivo a FINAL ON a.attachment_id = p.attachment_id WHERE a.conversation_id IN {c:Array(UInt64)}""", {"c": convs})
            out["midia_pdf"] = ext[0] if ext else {}
    if clientes:
        try:
            rs = _q(ch, "SELECT cliente_id, gerado_em, resumo_estruturado, resumo_llm, llm_ok, modelo FROM analytics.resumo_pessoa FINAL WHERE cliente_id IN {c:Array(String)} ORDER BY gerado_em DESC", {"c": clientes})
            out["resumo"] = [{"cliente_id": r["cliente_id"], "gerado_em": r["gerado_em"], "resumo_llm": r["resumo_llm"] if r["llm_ok"] else None, "resumo_estruturado": r["resumo_estruturado"],
                              "fonte_do_resumo_llm": r["modelo"] if r["llm_ok"] else None} for r in rs]
        except Exception:  # noqa
            out["resumo"] = []
    out["risco"] = {"contratos_existentes": [{k: c.get(k) for k in ("contrato_id", "data_inicio", "fase", "idade_dias", "etapa", "tipo_contrato", "prob_calote", "prob_inadimplencia", "prob_cluster_ruim", "faixa_risco", "confianca_modelo", "motivos", "calote_observado", "aviso",
                                                                       "cluster_trajetoria", "anterior_paga_e_para", "endereco_compartilhado", "inconsistencias")}
                                              for c in out.get("crm", {}).get("contratos", [])],
                    "novo_contrato": simular_novo(ch, clientes, cad, tel, req.contrato)}
    out["tempo_ms"] = int((time.time() - t0) * 1000)
    return _limpa(out)


@router.get("/consulta", dependencies=[Depends(exige_chave)])
def consulta_get(telefone: str | None = None, cpf: str | None = None, cnpj: str | None = None):
    return consulta(ConsultaRequest(telefone=telefone, cpf=cpf, cnpj=cnpj))


@router.get("/score/contrato/{contrato_id}", dependencies=[Depends(exige_chave)])
def score_contrato(contrato_id: str):
    ch = get_client()
    r = _q(ch, "SELECT * FROM analytics.score_contrato_atual WHERE contrato_id = {c:String}", {"c": contrato_id})
    if not r:
        raise HTTPException(status_code=404, detail="contrato sem score (nao existe ou ainda nao foi pontuado)")
    s = r[0]
    if s.get("motivos"):
        try:
            s["motivos"] = json.loads(s["motivos"])
        except Exception:  # noqa
            pass
    return _limpa(s)


@router.get("/modelo", dependencies=[Depends(exige_chave)])
def modelo():
    """Versao e confianca de cada etapa do motor (do ultimo treino diario)."""
    ch = get_client()
    rows = _q(ch, "SELECT alvo, etapa, features, modelo, auc, auc_ic_lo, auc_ic_hi, n, run_id, data_referencia FROM analytics_ml.motor_execucoes WHERE escolhido = 1 AND run_id = (SELECT max(run_id) FROM analytics_ml.motor_execucoes) ORDER BY alvo, etapa")
    for r in rows:
        r["confianca"] = confianca_de(r["auc"])
    return _limpa({"etapas": rows, "leitura": "etapa = idade do contrato em dias; auc = capacidade de separar quem da calote (0,5 = acaso)."})


@router.get("/clusters", dependencies=[Depends(exige_chave)])
def clusters():
    """Clusters de inadimplencia (trajetoria de pagamento D+1..D+45): tamanho, taxa de calote, valor. O cluster 'paga no comeco e para' e o de golpe/abandono."""
    return _limpa({"clusters": _q(get_client(), "SELECT * FROM analytics.cluster_inadimplencia"),
                   "leitura": "taxa_calote_d45 = parcelas depois do dia 1 ate o 45 ainda em aberto e vencidas em 20% ou mais; so contratos maduros entram."})


@router.get("/renovacao/{contrato_id}", dependencies=[Depends(exige_chave)])
def renovacao(contrato_id: str):
    """Contexto de renovacao: como foi o contrato anterior (cluster), salto de valor, score. Renovacao depois de 'paga e para' tem calote muito acima da media."""
    r = _q(get_client(), "SELECT * FROM analytics.risco_renovacao WHERE contrato_id = {c:String}", {"c": contrato_id})
    if not r:
        raise HTTPException(status_code=404, detail="contrato nao e renovacao ou nao existe")
    return _limpa(r[0])
