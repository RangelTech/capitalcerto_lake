FROM python:3.12-slim

# LightGBM precisa do runtime OpenMP do sistema. Os modelos sao treinados no Airflow (mesmas versoes de numpy, scikit-learn e lightgbm
# do requirements.txt: precisam ser iguais para o arquivo de modelo carregar aqui).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
