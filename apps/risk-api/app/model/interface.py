# Contrato que o modelo real de credito precisa respeitar (ver SPEC.md secao 3.3).
# O modelo em si (variaveis, algoritmo, pesos) e conhecimento metodologico confidencial e nao
# entra neste repositorio de infra. Aqui so fica a interface e, por enquanto, a implementacao
# dummy que roda no lugar dele.
from abc import ABC, abstractmethod


class RiskModel(ABC):
    @abstractmethod
    def predict(self, features: dict) -> dict:
        """Recebe as features do Feature Mart de um contrato e devolve um dict com, no minimo:
        score_financeiro (float, 0 a 10), score_golpe (float, 0 a 10), decisao (str) e
        model_version (str).
        """
        raise NotImplementedError
