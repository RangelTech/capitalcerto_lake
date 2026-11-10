# Acesso ao portal do CredHub (https://credhub.pro) por HTTP, sem navegador: login NextAuth com MFA (TOTP), lista os
# backups diarios por tenant e baixa o dump (.sql.gz) e o zip de CSV (.zip, com manifest.json de contagens).
# Credenciais na Airflow Connection `credhub_portal` (login = e-mail, password = senha, extra = {"mfa_secret": "<base32>"}).
#
# Fatos verificados em 2026-11-09 (ver spec_ingestao_crm_chatwoot_anexos.md):
#  - o portal rejeita o mesmo codigo TOTP dentro da mesma janela de 30 s (reuso), entao um retry espera a janela virar;
#  - o codigo e calculado com o cabecalho Date do proprio servidor (o relogio da maquina pode estar errado);
#  - os backups saem as 06:00 UTC (03:00 de Brasilia).
import base64
import email.utils
import hashlib
import hmac
import struct
import time

import requests

BASE = "https://credhub.pro"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def _conexao():
    from airflow.hooks.base import BaseHook

    c = BaseHook.get_connection("credhub_portal")
    extra = c.extra_dejson
    return c.login, c.password, extra["mfa_secret"], extra.get("base_url", BASE)


def _totp(segredo, t):
    k = base64.b32decode(segredo.replace(" ", "").upper())
    h = hmac.new(k, struct.pack(">Q", int(t // 30)), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10**6)


def _hora_do_servidor(r):
    return email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp()


def login(tentativas=3):
    """Devolve uma requests.Session ja autenticada. Em falha, espera a proxima janela de TOTP e tenta de novo."""
    email_, senha, segredo, base = _conexao()
    ultimo = None
    for n in range(tentativas):
        s = requests.Session()
        s.headers["User-Agent"] = UA
        r = s.get(base + "/api/auth/csrf", timeout=30)
        r.raise_for_status()
        csrf = r.json()["csrfToken"]
        t = _hora_do_servidor(r)
        if 30 - (t % 30) < 5:  # janela quase virando: espera a proxima
            time.sleep(31 - (t % 30))
            t += 31 - (t % 30)
        codigo = _totp(segredo, t)
        r = s.post(
            base + "/api/auth/callback/credentials",
            data={"email": email_, "password": senha, "code": codigo, "redirect": "false", "csrfToken": csrf,
                  "callbackUrl": base + "/dados/backups", "json": "true"},
            timeout=30,
            allow_redirects=False,
        )
        if r.status_code == 200 and any("session-token" in k for k in s.cookies.keys()):
            return s
        ultimo = f"HTTP {r.status_code}: {r.text[:160]}"
        time.sleep(32)  # anti-reuso do mesmo codigo: so tenta de novo na janela seguinte
    raise RuntimeError(f"login no CredHub falhou apos {tentativas} tentativas ({ultimo})")


def listar(s, base=BASE):
    r = s.get(base + "/api/data/backups", timeout=60)
    r.raise_for_status()
    return r.json()["tenants"]


def baixar(s, tenant_id, dia, formato, destino, base=BASE, esperado=None):
    """formato: 'zip' ou 'sql.gz'. Grava em `destino` e confere o tamanho contra o da listagem."""
    url = f"{base}/api/data/backups/{tenant_id}/{dia}.{formato}"
    with s.get(url, timeout=600, stream=True) as r:
        r.raise_for_status()
        total = 0
        with open(destino, "wb") as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
                total += len(bloco)
    if esperado is not None and total != esperado:
        raise RuntimeError(f"{url}: recebidos {total} bytes, a listagem dizia {esperado}")
    return total


def slug(nome):
    import re
    import unicodedata

    t = unicodedata.normalize("NFD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")
