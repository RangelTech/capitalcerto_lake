# Roda numa maquina com IP BRASILEIRO (a Receita recusa conexao da VPS, que fica na Franca): baixa os ZIPs do mes mais recente
# da base de CNPJ e deposita em landing/receita/AAAA-MM/ no MinIO da Capital Certo. A DAG rfb_cnpj_mensal le dali.
# Uso:  python rfb_enviar_para_minio.py            (mes mais recente)   |   python rfb_enviar_para_minio.py 2026-09
# Credencial: service account do bucket landing, em ~/.claude/secrets/capital-certo-stack.md (MINIO_LANDING_*).
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import boto3
import requests

WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav"
AUTH = ("YggdBLfdninEJX9", "")
ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://169.58.240.203:9000")


def listar(path):
    r = requests.request("PROPFIND", WEBDAV + path, data='<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>',
                         headers={"Depth": "1", "Content-Type": "application/xml"}, auth=AUTH, timeout=90)
    r.raise_for_status()
    out = {}
    for resp in re.findall(r"<d:response>.*?</d:response>", r.text, re.S):
        h = re.search(r"<d:href>([^<]+)", resp).group(1).rstrip("/").split("/")[-1]
        t = re.search(r"getcontentlength>(\d+)", resp)
        out[h] = int(t.group(1)) if t else 0
    return out


def main():
    txt = open(os.path.expanduser("~/.claude/secrets/capital-certo-stack.md"), encoding="utf-8").read()
    ak = re.search(r"MINIO_LANDING_ACCESS_KEY=(\S+)", txt).group(1)
    sk = re.search(r"MINIO_LANDING_SECRET_KEY=(\S+)", txt).group(1)
    s3 = boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=ak, aws_secret_access_key=sk)
    mes = sys.argv[1] if len(sys.argv) > 1 else sorted(m for m in listar("/") if re.fullmatch(r"\d{4}-\d{2}", m))[-1]
    arquivos = {k: v for k, v in listar(f"/{mes}/").items() if k.endswith(".zip")}
    print(f"mes {mes}: {len(arquivos)} arquivos, {sum(arquivos.values())/1e9:.2f} GB")
    def enviar(item):
        nome, tam = item
        chave = f"receita/{mes}/{nome}"
        try:
            if s3.head_object(Bucket="landing", Key=chave)["ContentLength"] == tam:
                print("ja esta la:", nome, flush=True)
                return
        except Exception:
            pass
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, nome)
            for tentativa in range(5):
                try:
                    with requests.get(f"{WEBDAV}/{mes}/{nome}", auth=AUTH, stream=True, timeout=(30, 300)) as r:
                        r.raise_for_status()
                        with open(dest, "wb") as f:
                            for bloco in r.iter_content(8 * 1024 * 1024):
                                f.write(bloco)
                    break
                except Exception as e:
                    print("retry", nome, str(e)[:80], flush=True)
                    time.sleep(10 * (tentativa + 1))
            s3.upload_file(dest, "landing", chave)
        print("enviado:", nome, f"{tam/1e6:.0f} MB", flush=True)

    # maiores primeiro, 4 conexoes: a Receita serve ~2 MB/s por conexao
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(enviar, sorted(arquivos.items(), key=lambda kv: -kv[1])))
    print("pronto. Agora a DAG rfb_cnpj_mensal no Airflow carrega o mes", mes)


if __name__ == "__main__":
    main()
