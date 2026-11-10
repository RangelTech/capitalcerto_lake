# Estudo do DIA 0 com a trilha de auditoria do CredHub (audit_event): eventos de pre-autorizacao do agendamento (Efi e Pluggy), QR e diagnosticos criados NA ASSINATURA
# (primeiras 6 horas e primeiras 24 horas depois da criacao do contrato) e o motivo das primeiras falhas (error_message). So conta o que aconteceu ate a janela escolhida.
# Uso: python credit_dia0_auditoria.py   (grava /tmp/dia0_auditoria.csv)
import re

import numpy as np
import pandas as pd
from scipy import stats

import credit_features as cf


def fdr(p):
    p = np.asarray(p, float)
    ok = ~np.isnan(p)
    q = np.full_like(p, np.nan)
    pv = p[ok]
    o = np.argsort(pv)
    r = pv[o] * len(pv) / (np.arange(len(pv)) + 1)
    r = np.minimum.accumulate(r[::-1])[::-1]
    out = np.empty_like(pv)
    out[o] = np.clip(r, 0, 1)
    q[ok] = out
    return q


def categoria(msg):
    m = (msg or "").lower()
    for chave, nome in (("saldo", "saldo insuficiente"), ("insufficient", "saldo insuficiente"), ("limite", "limite excedido"), ("revog", "consentimento revogado"), ("cancel", "cancelado pelo cliente"),
                        ("expir", "consentimento expirado"), ("rejeit", "rejeitado pelo banco"), ("timeout", "tempo esgotado"), ("autoriz", "sem autorizacao"), ("conta", "problema na conta")):
        if chave in m:
            return nome
    return "outro" if m else "sem motivo"


def main():
    ch = cf.conecta()
    base = ch.query_df("select contrato_id, data_inicio, ye_0, yi_0, y_calote_30, pix_agendado, provedor_pagamento from analytics_ml.motor_features_contrato")
    base.columns = [c.split(".")[-1] for c in base.columns]
    ct = ch.query_df("select id as contrato_id, created_at as criado_em from trusted_credhub.contract")
    ct.columns = [c.split(".")[-1] for c in ct.columns]
    ev = ch.query_df("""select entity_id as contrato_id, action, created_at as quando, error_message, status from trusted_credhub.audit_event
                        where entity = 'Contract' and (action like 'EFI%' or action like 'PLUGGY%' or action = 'WHATSAPP_CHARGE_SENT')""")
    ev.columns = [c.split(".")[-1] for c in ev.columns]
    ev = ev.merge(ct, on="contrato_id")
    ev["h"] = (pd.to_datetime(ev["quando"]) - pd.to_datetime(ev["criado_em"])).dt.total_seconds() / 3600
    print("eventos:", len(ev), "| horas desde a criacao (percentis 1,5,25,50,75,95):", np.percentile(ev["h"].dropna(), [1, 5, 25, 50, 75, 95]).round(1).tolist())
    df = base.copy()
    for janela, nome in ((6, "6h"), (24, "24h")):
        w = ev[(ev["h"] >= -1) & (ev["h"] <= janela)]
        piv = w.pivot_table(index="contrato_id", columns="action", values="h", aggfunc="count", fill_value=0)
        piv.columns = [f"aud{nome}_{c.lower()}" for c in piv.columns]
        df = df.merge(piv, left_on="contrato_id", right_index=True, how="left")
        w2 = w[w["error_message"].fillna("") != ""].copy()
        w2["cat"] = w2["error_message"].map(categoria)
        df[f"aud{nome}_falhas"] = df["contrato_id"].map(w2.groupby("contrato_id").size()).fillna(0)
        for cat in w2["cat"].unique():
            df[f"aud{nome}_motivo_{re.sub(r'[^a-z]+', '_', cat)}"] = df["contrato_id"].map(w2[w2["cat"] == cat].groupby("contrato_id").size()).fillna(0)
    cols = [c for c in df.columns if c.startswith("aud")]
    df[cols] = df[cols].fillna(0)
    ativos = [c for c in cols if (df[c] > 0).sum() >= 15]
    print("variaveis com pelo menos 15 contratos com evento:", len(ativos))
    linhas = []
    for estrato, m in (("todos", df["pix_agendado"].notna()), ("Pix agendado", df["pix_agendado"] == 1), ("sem Pix", df["pix_agendado"] == 0)):
        for alvo in ("ye_0", "yi_0", "y_calote_30"):
            d = df[m & df[alvo].notna()]
            for c in ativos:
                x, y = d[c].astype(float), d[alvo].astype(int)
                if x.nunique() < 2 or y.nunique() < 2 or len(d) < 40:
                    continue
                a, b = x[y == 1], x[y == 0]
                u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
                tem = x > 0
                linhas.append({"estrato": estrato, "alvo": alvo, "variavel": c, "n": len(d), "n_com_evento": int(tem.sum()), "auc": u / (len(a) * len(b)), "p": p,
                               "calote_com_evento": float(y[tem].mean()) if tem.any() else np.nan, "calote_sem_evento": float(y[~tem].mean()) if (~tem).any() else np.nan})
    r = pd.DataFrame(linhas)
    r["q_fdr"] = fdr(r["p"].values)
    r.to_csv("/tmp/dia0_auditoria.csv", index=False)
    print(f"{len(r)} testes; q<0,10: {(r.q_fdr < 0.1).sum()}; p<0,05: {(r.p < 0.05).sum()} (acaso ~{0.05 * len(r):.0f})")
    print(r.sort_values("p").head(25).round(3).to_string(index=False))
    # motivos das falhas por modo de cobranca
    w = ev[(ev["h"] >= -1) & (ev["h"] <= 24) & (ev["error_message"].fillna("") != "")].copy()
    w["cat"] = w["error_message"].map(categoria)
    w = w.merge(base[["contrato_id", "pix_agendado", "ye_0"]], on="contrato_id")
    print("\nmotivo da primeira falha (24h) por modo de cobranca: contratos e calote")
    pri = w.sort_values("h").drop_duplicates("contrato_id")
    print(pri.groupby(["pix_agendado", "cat"]).agg(contratos=("contrato_id", "size"), calote=("ye_0", "mean")).round(2).to_string())


if __name__ == "__main__":
    main()
