# Teste noturno das variaveis PEDIDAS pelo chat do Data IA Hub (hub_sandbox.motor_parametros, estado 'pedido').
# Para cada pedido cuja coluna ja existe em analytics_ml.motor_features_contrato: mede a relacao com calote/inadimplencia e o GANHO de acerto fora do tempo no dia 0 e no dia 1
# (com intervalo de bootstrap) e grava o resultado em portugues claro; estado vira 'testado' e a variavel fica DESLIGADA ate o administrador ligar na aba.
# Se a coluna ainda nao existe, o pedido continua pendente com a explicacao do que falta implementar. Uso: python credit_pedidos.py
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import credit_experimentos as ex
import credit_features as cf
import credit_variaveis as cv


def ganho(df, alvo, cats, nums_base, nova, modelo="rf"):
    d = df[df[alvo].notna()]
    ob = ex.avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, cats, [c for c in nums_base if c in df.columns], modelo, ultimos_meses=5)
    on = ex.avalia_temporal(df, alvo, cf.HORIZONTE_ALVO, cats, [c for c in nums_base + [nova] if c in df.columns], modelo, ultimos_meses=5)
    if not len(ob) or not len(on) or len(ob) != len(on):
        return None
    yb, pb, pn = ob["y"].values, ob["p"].values, on["p"].values
    rng = np.random.RandomState(7)
    ds = [roc_auc_score(yb[i], pn[i]) - roc_auc_score(yb[i], pb[i]) for i in (rng.randint(0, len(yb), len(yb)) for _ in range(300)) if len(set(yb[i])) > 1]
    return roc_auc_score(yb, pb), roc_auc_score(yb, pn), float(np.percentile(ds, 2.5)), float(np.percentile(ds, 97.5)), len(yb)


def main():
    ch = cf.conecta()
    try:
        ped = ch.query_df("SELECT variavel, descricao, definicao FROM hub_sandbox.motor_parametros FINAL WHERE estado = 'pedido'")
    except Exception as e:  # noqa
        print("sem tabela de pedidos:", str(e)[:80])
        return
    ped.columns = [c.split(".")[-1] for c in ped.columns]
    if not len(ped):
        print("nenhum pedido pendente")
        return
    df = cf.montar(ch)
    df["data_inicio"] = pd.to_datetime(df["data_inicio"])
    base0 = ["pix_agendado", "idade_na_contratacao", "prev_pct_atrasadas", "e_renovacao", "cep_vazio"]
    for r in ped.itertuples():
        v = r.variavel
        if v not in df.columns:
            texto = f"Pedido recebido, mas a variável '{v}' ainda não existe no motor. Para testar é preciso calcular a coluna a partir de: {r.definicao or '(sem definição informada)'}. Fica pendente."
            print(v, "-> pendente (coluna inexistente)")
        else:
            x = pd.to_numeric(df[v], errors="coerce") if df[v].dtype != object else None
            cat = x is None or x.notna().sum() < 0.5 * len(df)
            corr = {}
            for nome, col in (("calote", "ye_0"), ("inadimplencia", "yi_0"), ("cluster ruim", "cluster_ruim")):
                a, p, n = cv.assoc(df[v] if cat else x, pd.to_numeric(df[col], errors="coerce"), cat)
                corr[nome] = (a, p, n)
            partes = [f"{k}: {('V de Cramér' if cat else 'correlação')} {a:+.2f} (p={p:.3f}, n={n})" for k, (a, p, n) in corr.items() if not np.isnan(a)]
            g0 = ganho(df, "ye_0", ["provedor_pagamento"], base0, v) if not cat else None
            g1 = ganho(df, "ye_1", ["provedor_pagamento"], base0, v) if not cat else None
            linhas = [f"Relação com o resultado: {'; '.join(partes) or 'poucos dados'}."]
            for rot, g in (("dia 0", g0), ("dia 1", g1)):
                if g:
                    linhas.append(f"Ganho no {rot}: acerto {g[0]:.3f} → {g[1]:.3f} (IC95 do ganho {g[2]:+.3f} a {g[3]:+.3f}, {g[4]} contratos): {'GANHO REAL' if g[2] > 0 else 'sem ganho comprovado'}.")
            if cat:
                linhas.append("Variável categórica: teste de ganho no modelo não executado automaticamente.")
            ganho_real = any(g and g[2] > 0 for g in (g0, g1))
            linhas.append("Conclusão: " + ("vale ligar no motor (ganho real fora do tempo)." if ganho_real else "não vale ligar ainda; fica desligada. Pode ser ligada na aba Motor de crédito."))
            texto = " ".join(linhas)
            print(v, "->", texto[:200])
        ch.command(f"INSERT INTO hub_sandbox.motor_parametros (variavel, ativa, estado, pedido_por, descricao, definicao, resultado, atualizado_por) VALUES ('{v.replace(chr(39), '')}', 0, "
                   f"'{'pedido' if v not in df.columns else 'testado'}', 'chat', '{(r.descricao or '').replace(chr(39), ' ')}', '{(r.definicao or '').replace(chr(39), ' ')}', '{texto.replace(chr(39), ' ')}', 'teste noturno')")


if __name__ == "__main__":
    main()
