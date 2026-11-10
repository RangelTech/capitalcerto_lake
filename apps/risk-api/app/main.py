from fastapi import FastAPI

from app.clickhouse_client import get_features
from app.model.dummy_model import DummyModel
from app.v1 import router as router_v1
from app.schemas import ScoreRequest, ScoreResponse

app = FastAPI(title="Capital Certo Risk API", version="1.0")
app.include_router(router_v1)

model = DummyModel()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/score", response_model=ScoreResponse)
def score(request: ScoreRequest):
    features = get_features(request.contract_id)
    features["contract_id"] = request.contract_id
    result = model.predict(features)
    return ScoreResponse(contract_id=request.contract_id, **result)
