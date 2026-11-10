# Imagem do Airflow deste projeto com os pacotes que os DAGs precisam pra falar com MinIO
# (S3 compativel) e ClickHouse. A imagem oficial apache/airflow nao vem com isso instalado
# por padrao, so registrar AIRFLOW_CONN_* nao e suficiente sem o driver (ver
# configs/airflow/README.md).
ARG AIRFLOW_VERSION=2.9.3
FROM apache/airflow:${AIRFLOW_VERSION}

# LightGBM precisa do runtime OpenMP do sistema (libgomp1), que a imagem enxuta do Airflow nao traz.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
USER airflow

# Os pacotes de ML (motor de credito) entram com o arquivo de constraints oficial do Airflow para nao subir numpy/pandas
# alem do que o Airflow aceita. A parte de midia (Whisper, OCR) entra depois, em outra camada.
RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.12.txt" \
    apache-airflow-providers-amazon \
    clickhouse-connect \
    scikit-learn \
    lightgbm \
    joblib

# Camada de LLM/visao/voz em CPU (rodam dentro do Airflow na madrugada): LLM pequeno (llama.cpp), OCR, Whisper, embeddings, quadros de video e estatistica.
# Estes pacotes ja estavam instalados a mao no container em producao; aqui entram na imagem para sobreviver a um rebuild. O modelo gguf NAO vai na imagem:
# fica no MinIO (models/llm/) e e baixado quando falta (credit_tom_delta.garantir_modelo).
RUN pip install --no-cache-dir     --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.12.txt"     statsmodels scipy  && pip install --no-cache-dir faster-whisper fastembed rapidocr-onnxruntime av  && pip install --no-cache-dir llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

