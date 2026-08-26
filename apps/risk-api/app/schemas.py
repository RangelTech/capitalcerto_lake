# Contrato de request/response da Risk API. Ja definitivo mesmo com o modelo dummy rodando
# por baixo (ver app/model/interface.py), pra ninguem precisar mexer aqui quando o modelo
# real entrar.
from pydantic import BaseModel


class ScoreRequest(BaseModel):
    contract_id: str


class ScoreResponse(BaseModel):
    contract_id: str
    score_financeiro: float
    score_golpe: float
    decisao: str
    model_version: str
