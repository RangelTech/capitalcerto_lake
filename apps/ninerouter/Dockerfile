# Instancia propria e isolada do 9Router para este projeto (ver SPEC.md secao 1, isolamento
# total do ecossistema Rangel Tech). Placeholder ate o pacote real do 9Router ser definido aqui.
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY app.py .

EXPOSE 4000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4000"]
