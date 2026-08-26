# PLACEHOLDER: nao e o modelo de credito real. So devolve um score deterministico
# pra manter a arquitetura testavel enquanto o modelo de verdade nao existe (ver SPEC.md
# secao 3.3). Trocar por uma implementacao real de RiskModel quando o modelo estiver pronto,
# sem precisar mexer em rota, schema ou no resto da stack.
import hashlib

from app.model.interface import RiskModel


class DummyModel(RiskModel):
    def predict(self, features: dict) -> dict:
        contract_id = features.get("contract_id", "")
        seed = int(hashlib.sha256(contract_id.encode()).hexdigest(), 16)

        score_financeiro = round((seed % 1000) / 100, 2)
        score_golpe = round((seed % 700) / 100, 2)
        decisao = "aprovado" if score_financeiro >= 5 and score_golpe < 5 else "negado"

        return {
            "score_financeiro": score_financeiro,
            "score_golpe": score_golpe,
            "decisao": decisao,
            "model_version": "dummy-0.1",
        }
