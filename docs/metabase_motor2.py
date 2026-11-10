"""Dashboard 5 do Metabase, 3 abas novas depois de "Motor de credito":
  1. "Consulta por contrato"      filtros de telefone e nome -> ficha do contrato, chance de cada cluster, risco por etapa, fatores, parcelas, resumo
  2. "Contratos que pioraram"    lista de contratos ativos cujo risco subiu, ordenada por prioridade (1 = mais atencao)
  3. "API e pesos"               como chamar a API de risco e como o score e calculado, em linguagem simples
Idempotente: guarda os ids em mb_motor2_state.json e substitui so as proprias abas. Uso: python metabase_motor2.py (precisa de mb_lib.py ao lado, que le o segredo do Metabase)."""
import json
import os
import uuid

import mb_lib as m

DB, COLL, DASH = 4, 6, 5
STATE = "mb_motor2_state.json"
state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
NOMES = ["Motor de crédito", "Consulta por contrato", "Contratos que pioraram", "Mapa de testes", "API e pesos"]
AZUL, LARANJA, VERDE, VERMELHO = "#1F6FEB", "#F2994A", "#2E8B57", "#C0392B"

# filtros do dashboard = "filtro de campo" do Metabase ligado as colunas telefone e nome de analytics.contrato_consulta: ao digitar, aparecem os valores que existem.
from mb2_conteudo import ABAS  # noqa: E402
from mb_ajuda import larg_ajuda  # noqa: E402
CAMPOS = {}

def descobre_campos():
    """Garante que o Metabase conhece a view, e acha os ids dos campos telefone e nome; liga a busca com sugestao."""
    m.post(f"/api/database/{DB}/sync_schema", {})
    for _ in range(12):
        meta = m.get(f"/api/database/{DB}/metadata?include_hidden=true")
        t = [t for t in meta["tables"] if t["name"] == "contrato_consulta"]
        if t:
            CAMPOS.update({f["name"]: f["id"] for f in t[0]["fields"] if f["name"] in ("telefone", "nome")})
            if len(CAMPOS) == 2:
                break
        import time
        time.sleep(10)
    for n, fid in CAMPOS.items():
        m.put(f"/api/field/{fid}", {"has_field_values": "search", "semantic_type": "type/Category" if False else None})


def ensure_card(chave, item):
    native = {"query": item["sql"]}
    if item.get("filtros"):
        native["template-tags"] = {n: {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "cc-" + n)), "name": n, "display-name": n.capitalize(), "type": "dimension", "dimension": ["field", CAMPOS[n], None], "widget-type": "string/="} for n in ("telefone", "nome")}
    body = {"name": item["name"], "display": item["display"], "visualization_settings": item["viz"], "collection_id": COLL,
            "dataset_query": {"database": DB, "type": "native", "native": native}}
    if chave in state:
        try:
            m.put(f"/api/card/{state[chave]}", body)
            return state[chave]
        except Exception as e:  # noqa
            print("cartao sumiu, recriando:", chave, str(e)[:80])
    c = m.post("/api/card", body)
    state[chave] = c["id"]
    return c["id"]


def main():
    descobre_campos()
    dash = m.get(f"/api/dashboard/{DASH}")
    json.dump(dash, open("dash5_before_motor2.json", "w", encoding="utf-8"), ensure_ascii=False)
    minhas = set(state.get("_tabs", []))
    tabs = [t for t in dash["tabs"] if t["id"] not in minhas and t["name"] not in NOMES]
    keep = {t["id"] for t in tabs}
    cards = [c for c in dash["dashcards"] if c["dashboard_tab_id"] in keep]
    tmp, novas, ids_abas = -1, [], {}
    for nome in NOMES:
        ids_abas[nome] = tmp
        tmp -= 1
    for nome in NOMES:
        y = 0
        for linha in ABAS[nome]:
            x, hmax = 0, 0
            for item, w, h in linha:
                tab_id = ids_abas[nome]
                ajuda = item.get("ajuda")
                hw = larg_ajuda(w) if ajuda else 0
                w_graf = w - hw
                if "text" in item:
                    dc = {"id": tmp, "card_id": None, "dashboard_tab_id": tab_id, "row": y, "col": x, "size_x": w, "size_y": h,
                          "visualization_settings": {"virtual_card": {"name": None, "display": "text", "archived": False}, "text": item["text"]}, "parameter_mappings": [], "series": []}
                else:
                    cid = ensure_card(f"{nome}|{item['name']}", item)
                    pm = [{"parameter_id": n, "card_id": cid, "target": ["dimension", ["template-tag", n]]} for n in ("telefone", "nome")] if item.get("filtros") else []
                    dc = {"id": tmp, "card_id": cid, "dashboard_tab_id": tab_id, "row": y, "col": x, "size_x": w_graf, "size_y": h, "visualization_settings": {}, "parameter_mappings": pm, "series": []}
                tmp -= 1
                novas.append(dc)
                if ajuda:
                    novas.append({"id": tmp, "card_id": None, "dashboard_tab_id": tab_id, "row": y, "col": x + w_graf, "size_x": hw, "size_y": h,
                                  "visualization_settings": {"virtual_card": {"name": None, "display": "text", "archived": False}, "text": ajuda}, "parameter_mappings": [], "series": []})
                    tmp -= 1
                x += w
                hmax = max(hmax, h)
            assert x == 24, f"linha soma {x}"
            y += hmax
    ordem = sorted(tabs, key=lambda t: t["position"])
    tabs_payload, pos = [], 0
    for nome in NOMES:   # as abas do motor primeiro, na ordem de NOMES; as antigas depois
        tabs_payload.append({"id": ids_abas[nome], "name": nome, "position": pos})
        pos += 1
    for t in ordem:
        tabs_payload.append({"id": t["id"], "name": t["name"], "position": pos})
        pos += 1
    params = [p for p in dash.get("parameters", []) if p.get("id") not in ("telefone", "nome")]
    params += [{"id": "telefone", "name": "Telefone", "slug": "telefone", "type": "string/="}, {"id": "nome", "name": "Nome", "slug": "nome", "type": "string/="}]
    cards_payload = [{k: c[k] for k in ("id", "card_id", "dashboard_tab_id", "row", "col", "size_x", "size_y", "visualization_settings", "parameter_mappings", "series") if k in c} for c in cards]
    m.put(f"/api/dashboard/{DASH}", {"name": dash["name"], "tabs": tabs_payload, "dashcards": cards_payload + novas, "parameters": params})
    d2 = m.get(f"/api/dashboard/{DASH}")
    state["_tabs"] = [t["id"] for t in d2["tabs"] if t["name"] in NOMES]
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("abas:", [(t["id"], t["name"], t["position"]) for t in d2["tabs"]])
    print("cartoes nas abas novas:", sum(1 for c in d2["dashcards"] if c["dashboard_tab_id"] in state["_tabs"]))


if __name__ == "__main__":
    main()
