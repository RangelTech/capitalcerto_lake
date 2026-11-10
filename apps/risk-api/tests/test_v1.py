"""Testes sem ClickHouse nem MinIO: normalizacao, mascara e autenticacao."""
import os

from fastapi.testclient import TestClient

os.environ["RISK_API_KEYS"] = "chave-de-teste"

from app.main import app  # noqa: E402
from app.v1 import cep_para_uf, mascara_doc, normaliza_telefone  # noqa: E402

c = TestClient(app)


def test_telefone_normaliza():
    assert normaliza_telefone("+55 (62) 99999-8888") == "62999998888"
    assert normaliza_telefone("5562999998888") == "62999998888"
    assert normaliza_telefone("999998888") == "999998888"


def test_mascara_documento():
    assert mascara_doc("123.456.789-09") == "123.***.***-09"
    assert mascara_doc("12345678000190") == "12.***.***/0001-90"


def test_cep_para_uf():
    assert cep_para_uf("74520-070") == "GO"
    assert cep_para_uf("01310-100") == "SP"
    assert cep_para_uf("123") is None


def test_sem_chave_401():
    assert c.post("/v1/consulta", json={"telefone": "62999998888"}).status_code == 401
    assert c.post("/v1/consulta", json={"telefone": "62999998888"}, headers={"X-API-Key": "errada"}).status_code == 401


def test_pedido_vazio_422():
    assert c.post("/v1/consulta", json={}, headers={"X-API-Key": "chave-de-teste"}).status_code == 422


def test_health_sem_chave():
    assert c.get("/health").status_code == 200
