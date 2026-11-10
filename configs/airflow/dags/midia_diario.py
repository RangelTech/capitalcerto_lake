# DAG diaria de midia: baixa os anexos NOVOS do cliente (ultimos 3 dias, qualquer conversa, so GET na API do Chatwoot), confere o MD5, guarda no
# MinIO e processa imagem (CLIP, dHash), PDF (texto e campos), audio (Whisper) e video (quadros + CLIP) em tabelas trusted_chatwoot.anexo_*.
# Sem schedule proprio: a orquestradora_diaria_capital_certo dispara depois do chatwoot_diario. Cada passada e idempotente (o catalogo e a fila).
import pendulum
from airflow import DAG
from airflow.operators.python import PythonOperator

with DAG(
    dag_id="midia_diario",
    start_date=pendulum.datetime(2026, 11, 9, tz="America/Sao_Paulo"),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": pendulum.duration(minutes=10)},
    tags=["capital_certo", "chatwoot", "midia", "raw", "trusted"],
) as dag:

    def _baixar(**_):
        import midia_baixar

        midia_baixar.rodar(n_threads=3, escopo="recentes", dias=3)

    def _proc(tipo):
        def f(**_):
            import credit_features as cf
            import midia_processa as mp

            getattr(mp, tipo)(cf.conecta())

        return f

    baixar = PythonOperator(task_id="baixar_anexos_novos", python_callable=_baixar, execution_timeout=pendulum.duration(hours=1))
    tarefas = [PythonOperator(task_id=f"processar_{t}", python_callable=_proc(t), execution_timeout=pendulum.duration(hours=1)) for t in ("imagem", "pdf", "video", "audio")]
    baixar >> tarefas[0] >> tarefas[1] >> tarefas[2] >> tarefas[3]
