import os

import clickhouse_connect


def get_client():
    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
    )


def get_features(contract_id: str) -> dict:
    """Busca as features do Feature Mart para um contrato.

    O nome da tabela e as colunas reais entram quando o Feature Mart estiver definido
    (ver SPEC.md secao 3.1, pendencia sobre schema do ClickHouse).
    """
    client = get_client()
    result = client.query(
        "SELECT * FROM feature_mart.contratos WHERE contract_id = {contract_id:String}",
        parameters={"contract_id": contract_id},
    )
    rows = result.result_rows
    if not rows:
        return {}
    return dict(zip(result.column_names, rows[0]))
