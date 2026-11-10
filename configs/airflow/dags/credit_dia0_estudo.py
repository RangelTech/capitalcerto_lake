# Estudo do DIA 0 (10/11/2026): o que da configuracao do agendamento de cobranca (Efi automatico, Pluggy, Efi Pix) e do momento da contratacao separa quem da calote?
# So entram informacoes FIXAS na criacao (dia da semana do debito, periodicidade, hora, quantidade de parcelas agendadas / contratadas...), nao o status atual (que ja traz o desfecho).
# Testes: univariado (Mann-Whitney / Fisher) com FDR, em tres estratos (todos, Pix agendado, sem Pix). Uso: python credit_dia0_estudo.py
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


def main():
    ch = cf.conecta()
    base = ch.query_df("select contrato_id, data_inicio, ye_0, yi_0, y_calote_30, pix_agendado, provedor_pagamento, qtd_parcelas_contratadas, valor_principal, valor_parcela, ordem_contrato_cliente from analytics_ml.motor_features_contrato")
    base.columns = [c.split(".")[-1] for c in base.columns]
    base["data_inicio"] = pd.to_datetime(base["data_inicio"])
    efi = ch.query_df("""select contract_id as contrato_id, argMin(weekday, created_at) as efi_dia_semana, argMin(periodicidade, created_at) as efi_periodicidade, argMin(toUInt8(allow_retry), created_at) as efi_permite_retentativa,
                         argMin(scheduled_count, created_at) as efi_parcelas_agendadas, min(created_at) as efi_criado_em, argMin(start_date, created_at) as efi_inicio_debito, count() as efi_n_assinaturas
                         from trusted_credhub.efi_automatic_subscription group by contract_id""")
    plg = ch.query_df("""select contract_id as contrato_id, argMin(scheduled_count, created_at) as plg_parcelas_agendadas, min(created_at) as plg_criado_em, count() as plg_n_pedidos
                         from trusted_credhub.pluggy_scheduled_payment_request group by contract_id""")
    ct = ch.query_df("select id as contrato_id, created_at as contrato_criado_em from trusted_credhub.contract")
    for d in (efi, plg, ct):
        d.columns = [c.split(".")[-1] for c in d.columns]
    df = base.merge(efi, on="contrato_id", how="left").merge(plg, on="contrato_id", how="left").merge(ct, on="contrato_id", how="left")
    df["contrato_criado_em"] = pd.to_datetime(df["contrato_criado_em"])
    df["hora_criacao"] = df["contrato_criado_em"].dt.hour + df["contrato_criado_em"].dt.minute / 60
    df["fim_de_semana"] = (df["contrato_criado_em"].dt.dayofweek >= 5).astype(float)
    df["horario_comercial"] = df["hora_criacao"].between(8, 18).astype(float)
    df["efi_atraso_inicio_debito_dias"] = (pd.to_datetime(df["efi_inicio_debito"]) - df["data_inicio"]).dt.days
    df["frac_agendada_efi"] = df["efi_parcelas_agendadas"] / df["qtd_parcelas_contratadas"].replace(0, np.nan)
    df["frac_agendada_plg"] = df["plg_parcelas_agendadas"] / df["qtd_parcelas_contratadas"].replace(0, np.nan)
    df["tem_efi_automatico"] = df["efi_criado_em"].notna().astype(float)
    df["tem_pluggy"] = df["plg_criado_em"].notna().astype(float)
    df["efi_dia_semana_num"] = pd.to_numeric(df["efi_dia_semana"], errors="coerce")
    df["parcelas_menor_60"] = (df["qtd_parcelas_contratadas"] < 60).astype(float)
    df["razao_parcela_valor"] = df["valor_parcela"].astype(float) / df["valor_principal"].astype(float)
    for _c in ("efi_dia_semana_num", "efi_permite_retentativa", "efi_parcelas_agendadas", "efi_n_assinaturas", "efi_atraso_inicio_debito_dias", "plg_parcelas_agendadas", "plg_n_pedidos"):
        df[_c] = pd.to_numeric(df[_c].astype(str).replace({"<NA>": None, "None": None, "nan": None}), errors="coerce")
    cand = ["efi_dia_semana_num", "efi_permite_retentativa", "efi_parcelas_agendadas", "efi_n_assinaturas", "efi_atraso_inicio_debito_dias", "frac_agendada_efi", "plg_parcelas_agendadas", "plg_n_pedidos", "frac_agendada_plg",
            "tem_efi_automatico", "tem_pluggy", "hora_criacao", "fim_de_semana", "horario_comercial", "parcelas_menor_60", "razao_parcela_valor"]
    print("valores de periodicidade efi:", df["efi_periodicidade"].value_counts(dropna=False).to_dict())
    print("cobertura:", {c: int(df[c].notna().sum()) for c in cand})
    linhas = []
    for estrato, m in (("todos", df["pix_agendado"].notna()), ("Pix agendado", df["pix_agendado"] == 1), ("sem Pix", df["pix_agendado"] == 0)):
        for alvo in ("ye_0", "yi_0", "y_calote_30"):
            d = df[m & df[alvo].notna()]
            for c in cand:
                s = d[[c, alvo]].dropna()
                if len(s) < 40 or s[c].nunique() < 2 or s[alvo].nunique() < 2:
                    continue
                x, y = s[c].astype(float), s[alvo].astype(int)
                a, b = x[y == 1], x[y == 0]
                u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
                linhas.append({"estrato": estrato, "alvo": alvo, "variavel": c, "n": len(s), "auc": u / (len(a) * len(b)), "p": p, "media_calote": a.mean(), "media_nao": b.mean()})
    r = pd.DataFrame(linhas)
    r["q_fdr"] = fdr(r["p"].values)
    print(f"\n{len(r)} testes; q<0,10: {(r.q_fdr < 0.1).sum()}; p<0,05: {(r.p < 0.05).sum()} (acaso ~{0.05 * len(r):.0f})")
    print(r.sort_values("p").head(18).round(3).to_string(index=False))
    r.to_csv("/tmp/dia0_estudo.csv", index=False)
    # taxa de calote por categoria nas variaveis discretas
    for c in ("efi_dia_semana_num", "efi_permite_retentativa", "tem_efi_automatico", "tem_pluggy", "fim_de_semana", "horario_comercial"):
        for estrato, m in (("Pix", df["pix_agendado"] == 1), ("sem Pix", df["pix_agendado"] == 0)):
            d = df[m & df["ye_0"].notna() & df[c].notna()]
            if d[c].nunique() > 1:
                print(f"{c:26s} {estrato:8s}", d.groupby(c)["ye_0"].agg(["count", "mean"]).round(2).values.tolist())


if __name__ == "__main__":
    main()
