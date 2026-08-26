# Placeholder do 9Router deste projeto. Sobe so pra a stack ficar completa e testavel de ponta a
# ponta antes do pacote real do 9Router ser integrado aqui. Ver README.md desta pasta.
from fastapi import FastAPI

app = FastAPI(title="capitalcerto-ninerouter (placeholder)")


@app.get("/health")
def health():
    return {"status": "ok", "placeholder": True}
