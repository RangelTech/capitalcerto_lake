"""Gera docs/catalogo_variaveis.csv (e opcionalmente carrega em analytics_ml.motor_catalogo_variaveis): o MAPA de todas as variaveis candidatas do motor de credito,
em que momento cada uma e conhecida, de onde vem, o que a literatura diz, e o status do teste. Fonte unica para a spec (SPEC_MAPEAMENTO_VARIAVEIS.md)."""
import csv
import os

# momento: M0 = antes da assinatura (lead, conversa, cadastro); M1 = dia da assinatura; M2 = renovacao (ja foi cliente); M3 = comportamento D+1..D+14
# status: SINAL (passou FDR), SINAL_FRACO (p<0,05 sem FDR ou so em subgrupo), SEM_SINAL (testada, nada), NAO_TESTADA, INDISPONIVEL (nao existe na base), BLOQUEADA (precisa de decisao/dado externo)
L = [
    # familia, variavel, descricao, momento, fonte, disponibilidade, literatura, status, resultado, proximo
    ("A cadastro/identidade", "idade", "idade na contratacao (CRM birth_date; Dora/DeskData)", "M0", "CRM, bureau", "59% (so 1o contrato dos mais novos)", "Serasa MEI Score usa cadastro; padrao em scorecards", "SINAL", "mais velho, menos calote (AUC 0,61; protege dentro de 'sem Pix' p=0,02)", "completar idade pela Receita/CPF quando faltar"),
    ("A cadastro/identidade", "sexo", "sexo do cadastro (CRM)", "M0", "CRM", "~70%", "pedido do cliente (hipotese)", "SEM_SINAL", "inadimplencia y_30: MASCULINO -12 pp (q=0,014) mas instavel; calote nada", "retestar com mais dados e dentro de 'sem Pix'"),
    ("A cadastro/identidade", "raca_cor", "raca ou cor", "M0", "-", "0%", "pedido do cliente", "INDISPONIVEL", "nao existe em nenhuma fonte; nao se infere por nome/foto (impreciso e discriminatorio)", "so se o cliente fornecer o campo"),
    ("A cadastro/identidade", "tipo_pessoa", "CPF (pessoa fisica) ou CNPJ", "M0", "CRM", "100%", "modelos separados por classe MEI/ME/PF (literatura BR)", "SEM_SINAL", "sem diferenca (p=0,74 no valor; nada em calote)", "modelo por classe se n permitir"),
    ("A cadastro/identidade", "regiao_fiscal_cpf", "9o digito do CPF: regiao onde o CPF foi emitido (proxy de origem)", "M0", "CPF", "93%", "pedido do cliente (naturalidade)", "SEM_SINAL", "10 regioes, nada passa FDR", "-"),
    ("A cadastro/identidade", "naturalidade", "municipio/estado de nascimento", "M0", "DeskData (75 relatorios)", "<1%", "pedido do cliente", "INDISPONIVEL", "so 75 notas citam; sem poder", "pedir o campo na fonte da consulta"),
    ("A cadastro/identidade", "nome (comprimento, tokens, parece empresa, primeiro nome)", "caracteristicas do nome", "M0", "CRM", "100%", "-", "SEM_SINAL", "nada", "-"),
    ("A cadastro/identidade", "cadastro_incompleto / sem_idade", "falta de idade/sexo/segmento/UF no cadastro", "M0", "CRM", "41% sem idade", "indicadores de dado incompleto/falso (Point Predictive: 17 indicadores)", "SINAL_FRACO", "cadastro sem idade: 35% x 16% de calote em 1o contrato sem Pix (coorte antiga mistura)", "controlar por safra"),
    ("B localizacao", "uf, ddd, regiao_ddd", "UF e regiao pelo DDD do telefone", "M0", "telefone", "100%", "pedido do cliente", "SEM_SINAL", "nada passa FDR", "-"),
    ("B localizacao", "uf_cep / cep_vazio", "UF pelo CEP; CEP ausente", "M0", "CRM addresses", "61% com CEP", "-", "SINAL", "CEP vazio: +8 pp calote (q=0,043); 'sem Pix + com CEP' 10% x 25%", "incluido no D+0"),
    ("B localizacao", "cidade_empresa", "cidade do estabelecimento (CRM, Dora, Receita)", "M0", "CRM, Dora, Receita", "100% (65% casa com o IBGE)", "pedido do cliente (PIB/leis estaduais)", "SINAL_FRACO", "ver IBGE abaixo", "melhorar casamento (codigo IBGE pelo CEP)"),
    ("B localizacao", "pib_per_capita_municipio / quartil", "PIB per capita do municipio (IBGE 2023)", "M0", "IBGE (baixado)", "65% casados", "pedido do cliente", "SINAL_FRACO", "menor quartil: -11 a -15 pp de calote (q=0,03 a 0,10); dentro de 'sem Pix' p=0,017", "carregar IBGE no lake e testar com cidade melhor casada"),
    ("B localizacao", "populacao_municipio / porte", "populacao estimada do municipio", "M0", "IBGE", "65%", "-", "SINAL_FRACO", "municipio de 50 a 200 mil habitantes: -16 pp de inadimplencia 30d (q=0,024)", "idem"),
    ("B localizacao", "capital x interior", "cidade e capital de UF", "M0", "IBGE", "65%", "-", "SEM_SINAL", "nada", "-"),
    ("B localizacao", "pib_per_capita_uf", "PIB per capita da UF", "M0", "IBGE", "65%", "pedido do cliente (estado)", "SEM_SINAL", "nada passa", "-"),
    ("B localizacao", "leis estaduais / ambiente juridico", "legislacao de cobranca por UF", "M0", "-", "0%", "pedido do cliente", "BLOQUEADA", "nao ha variavel objetiva; proxy = UF (testada)", "pesquisa juridica antes de qualquer variavel"),
    ("B localizacao", "geocodificacao (lat/long, distancia)", "latitude e longitude do endereco", "M0", "CRM addresses.latitude", "0% preenchido", "mobilidade (ICIS 2019)", "INDISPONIVEL", "campo vazio; fotos sem GPS (WhatsApp remove)", "geocodificar pelo CEP (API publica) e testar"),
    ("C empresa/CNPJ", "cnae_divisao", "ramo de atividade (2 digitos)", "M0", "Receita + Dora", "86%", "pedido do cliente (ramo 'bichado'); Serasa PJ", "SINAL", "divisao 95 (reparacao): 82-90% calote (n=10); cluster 'paga e para' q=0,002", "validar com mais dados (n pequeno)"),
    ("C empresa/CNPJ", "cnae_classe (4 digitos) / familias", "mercearia, hamburgueria, bares, restaurante", "M0", "Receita + Dora", "86%", "pedido do cliente", "SEM_SINAL", "mercearia 24% (n=33), hamburgueria 31% (n=13), bares 47% (n=17, p=0,04), restaurantes 24%", "retestar quando n crescer"),
    ("C empresa/CNPJ", "segmento (CRM)", "segmento declarado no cadastro", "M0", "CRM", "69%", "pedido do cliente", "SEM_SINAL", "bares 47% (p=0,04 sem FDR)", "-"),
    ("C empresa/CNPJ", "idade_da_empresa", "meses desde a abertura do CNPJ", "M0", "Receita/Dora", "54-88%", "Serasa PJ, literatura PME", "SEM_SINAL", "nada passa; empresa nova (<6m) tambem nada", "-"),
    ("C empresa/CNPJ", "porte, capital_social, MEI, Simples, natureza", "caracteristicas formais", "M0", "Receita", "54%", "literatura PME", "SEM_SINAL", "nada", "-"),
    ("C empresa/CNPJ", "quadro societario (qtd socios, idade do socio, qualificacao)", "composicao dos socios", "M0", "Receita", "19%", "tie-strength / laços (UCD/Dublin thesis)", "SEM_SINAL", "quase todo MEI tem 1 socio", "-"),
    ("C empresa/CNPJ", "rede de socios / clientes ligados", "socio que e outro cliente; socio compartilhado; calote de ligado", "M0", "Receita + CRM", "10 contratos", "tie-strength", "SEM_SINAL", "sem poder (10 contratos com vinculo)", "crescer a base antes"),
    ("D bureau", "score_pf, faixa_pf, taxa_pf", "Score PF (HSPN), faixa A-H", "M0", "Dora (notas)", "69% dos contratos", "bureaus: padrao ouro", "SEM_SINAL", "59% no piso (350): variavel saturada; AUC ~0,5 entre aprovados", "retestar fora do piso; selecao: bureau decide a aprovacao (AUC 0,87)"),
    ("D bureau", "score_pj, default_pj, status cadastral", "Score PJ (HPJ8)", "M0", "Dora", "~60%", "bureaus PJ", "SEM_SINAL", "nada", "-"),
    ("D bureau", "pefin, refin, protesto, cheque, cobranca (qtd e valor, PF e PJ)", "negativacoes", "M0", "Dora", "20-86%", "bureaus", "SEM_SINAL", "nada passa FDR", "-"),
    ("D bureau", "divida_total_pf/pj, acima de 50 mil", "dividas totais", "M0", "Dora", "23%", "regra do proprio relatorio", "SEM_SINAL", "nada", "-"),
    ("D bureau", "processos judiciais (qtd, ativos, passivo)", "acoes e processos", "M0", "Dora + DeskData", "77-100%", "bureaus", "SEM_SINAL", "nada", "-"),
    ("D bureau", "identidade (confirmada/divergente), cpf regular", "integridade DeskData", "M0", "DeskData", "94%", "FPD: identidade sintetica (Experian)", "SEM_SINAL", "nada", "-"),
    ("D bureau", "vinculo da maquininha (proprio/terceiro)", "maquininha em nome do lead", "M0", "DeskData", "99%", "bust-out/terceiros", "SEM_SINAL", "nada", "-"),
    ("D bureau", "vendas mensais informadas", "faturamento declarado", "M0", "DeskData", "77%", "capacidade de pagamento", "SINAL_FRACO", "valor do contrato sobe com ele (rho +0,35); sozinho sem efeito no calote", "razao parcela/faturamento"),
    ("D bureau", "cnpjs vinculados negativados", "outros CNPJs do CPF com negativacao", "M0", "Dora", "89%", "tie-strength", "SEM_SINAL", "nada", "-"),
    ("E contrato/operacao", "pix_agendado", "cobranca por Pix agendado (definido na criacao)", "M1", "CRM contract", "100%", "-", "SINAL", "calote 74% x 20% desde jun (OR 8,2 por mes); cluster 'paga e para' 57% x 19%", "alavanca operacional; investigar EFI_PAYMENT_FAILED (104 mil eventos)"),
    ("E contrato/operacao", "provedor_pagamento", "PLUGGY, EFI, EFI_AUTOMATICO, ASAAS, MANUAL", "M1", "CRM", "100%", "-", "SINAL", "sem Pix: PLUGGY 12%, EFI_AUT 18%, EFI 33%, MANUAL 46%", "ja no D+0"),
    ("E contrato/operacao", "valor_principal", "valor emprestado", "M1", "CRM", "100%", "pedido do cliente (valor)", "SINAL", "inadimplencia AUC 0,62 (q=0); calote: so dentro de 'sem Pix' (AUC 0,61, p=0,0006)", "incluir em D+0 'sem Pix' (testar)"),
    ("E contrato/operacao", "qtd_parcelas_contratadas", "numero de parcelas (59-60 na grande maioria)", "M1", "CRM", "100%", "-", "SINAL", "inadimplencia AUC 0,64 (q=0, estavel)", "testar no D+0"),
    ("E contrato/operacao", "valor_parcela e razao parcela/principal", "peso da parcela", "M1", "CRM", "100%", "capacidade de pagamento", "SINAL", "inadimplencia AUC 0,59-0,64 (q=0)", "testar no D+0"),
    ("E contrato/operacao", "taxa_juros_pct", "taxa", "M1", "CRM", "100%", "-", "SINAL", "inadimplencia AUC 0,58 (q=0,015); fuga de garantia q=0,04", "testar no D+0"),
    ("E contrato/operacao", "valor / faturamento declarado", "razao do contrato sobre faturamento", "M1", "CRM + DeskData", "~60%", "capacidade de pagamento", "NAO_TESTADA", "-", "TESTAR"),
    ("E contrato/operacao", "dia da semana / dia do mes / mes de inicio", "sazonalidade da contratacao", "M1", "CRM", "100%", "efeitos de calendario em credito", "NAO_TESTADA", "-", "TESTAR (inclui feriados)"),
    ("E contrato/operacao", "etiqueta fuga de garantia", "marcado pela operacao (7,5%)", "M3", "CRM", "100%", "-", "SINAL", "provedor MANUAL +11,5 pp; valor, juros, tempo de casa (q<0,05)", "usar como alvo de golpe"),
    ("F conversa/lead", "n_msgs, tamanho medio/max, hora e dia da 1a msg, madrugada, fim de semana", "estilo basico da conversa ate 1 dia apos o contrato", "M0", "Chatwoot", "97%", "SMS/phone (UCT, ICIS)", "SEM_SINAL", "nada passa FDR", "-"),
    ("F conversa/lead", "latencia de resposta (mediana, % <60s)", "demora entre mensagens", "M0", "Chatwoot", "~85%", "pedido do cliente", "SEM_SINAL", "nada (x_chat_resp_mediana_s)", "refazer separando bot x humano"),
    ("F conversa/lead", "emojis, maiusculas, exclamacoes, perguntas, numeros", "estilo textual", "M0", "Chatwoot", "97%", "-", "SEM_SINAL", "nada", "-"),
    ("F conversa/lead", "termos (urgencia, preciso, pix, maquininha, negativado, agradece, taxa, palavrao)", "lexico por regra", "M0", "Chatwoot", "97%", "-", "SEM_SINAL", "nada", "-"),
    ("F conversa/lead", "tom direto/agressivo, recusa, desconfianca, objecoes", "tom da conversa avaliado por LLM", "M0", "Chatwoot + LLM", "250 contratos (piloto)", "pedido do cliente", "NAO_TESTADA", "piloto LLM 6 perguntas em andamento (31 s/contrato)", "TESTAR com conjunto maior de perguntas"),
    ("F conversa/lead", "embedding do texto (conversa e notas)", "vetor MiniLM 384d", "M0", "Chatwoot", "97%", "-", "SEM_SINAL", "conversa AUC 0,44-0,48; notas ye_0 0,66 n=107 (nao confirmado)", "retestar com mais dados"),
    ("F conversa/lead", "dias do 1o contato ate o contrato", "demora para fechar", "M0", "Chatwoot + CRM", "98%", "-", "SINAL", "AUC 0,58-0,59 so entre 1os contratos; deixa de sustentar controlando a cobranca", "retestar dentro de 'sem Pix'"),
    ("F conversa/lead", "linha de WhatsApp (inbox), anuncio/criativo Meta", "canal de aquisicao", "M0", "Chatwoot", "98%", "-", "SINAL_FRACO", "inbox 7: +9 pp (q=0,07); associado ao cluster (q=0,007); anuncio sem sinal", "retestar controlando a cobranca"),
    ("G funil", "tempo por estagio, n transicoes, estagios distintos, manual x automatico", "trajetoria no funil ate o contrato", "M0", "Chatwoot", "98%", "-", "SEM_SINAL", "n estagios distintos p=0,06 (q>0,1)", "-"),
    ("G funil", "resposta a proposta, etapa maquininha, confirmacao do CNPJ", "atributos da conversa (estado atual, pode vazar)", "M0", "Chatwoot", "-", "-", "SEM_SINAL", "sem poder (vazamento possivel)", "so se houver data do evento"),
    ("H midia", "foto de fachada (CLIP: fachada, residencia, interior, rua), nitidez", "classificacao zero-shot das fotos", "M0", "anexos", "~48% com imagem", "-", "SEM_SINAL", "m_img_max_fachada p=0,02-0,05, nao passa FDR", "retestar"),
    ("H midia", "reuso de foto (dHash em outros clientes)", "mesma imagem em clientes diferentes", "M0", "anexos", "~48%", "reuso de dados em varias aplicacoes (Point Predictive)", "SEM_SINAL", "nada", "-"),
    ("H midia", "print da maquininha (faturamento por OCR)", "total vendido, n vendas, ticket", "M0", "anexos + OCR", "~15%", "capacidade de pagamento", "NAO_TESTADA", "OCR nao rodado nos prints", "TESTAR (OCR dos prints)"),
    ("H midia", "extrato bancario (entradas/saidas, emprestimo descontado)", "campos do PDF", "M0", "anexos", "~20%", "capacidade de pagamento", "SEM_SINAL", "m_pdf_entradas p=0,05 (n=81)", "retestar com mais extratos"),
    ("H midia", "audio (valor citado, ritmo, hesitacao) e video (movimento, fala)", "features do Whisper e do video", "M0", "anexos", "~10-25%", "voz/SMS como dado alternativo", "SEM_SINAL", "nada (n pequeno)", "-"),
    ("I identidade/consistencia", "telefone/email/endereco/documento compartilhado entre clientes", "mesmo dado em varias aplicacoes", "M0", "CRM + Chatwoot", "-", "Point Predictive: telefone diferente em varias aplicacoes; BasePoint", "NAO_TESTADA", "-", "TESTAR (local)"),
    ("I identidade/consistencia", "indice de inconsistencias (identidade divergente, terceiro, CNPJ nao confirmado, DDD x CEP, dado faltante)", "contagem de alertas", "M0", "bureau + CRM", "~90%", "BasePoint (misrepresentation) e Point Predictive (>17 indicadores = 8x)", "NAO_TESTADA", "-", "TESTAR (local)"),
    ("I identidade/consistencia", "DDD x UF do CEP x UF do CNPJ", "incoerencia de localizacao", "M0", "CRM + Dora", "39%", "-", "SEM_SINAL", "nada", "-"),
    ("J historico/renovacao", "e_renovacao, n de contratos, tempo de casa", "ja foi cliente", "M2", "CRM", "100%", "repeat borrower (Point Predictive)", "SEM_SINAL", "renovacao 26% x 24%; tempo de casa liga a inadimplencia (AUC 0,41, instavel)", "-"),
    ("J historico/renovacao", "pagou bem o anterior / quitacao antecipada", "contrato anterior quitado sem atraso, antes do fim", "M2", "CRM", "17% das renovacoes", "pedido do cliente (golpe no 2o)", "SINAL", "pagou bem: calote 6% x 30% no seguinte; padrao de golpe: so 10 casos (10%)", "evento raro: monitorar"),
    ("J historico/renovacao", "salto de valor", "valor do 2o em relacao ao 1o", "M2", "CRM", "-", "bust-out", "SINAL_FRACO", "salto >25%: 35% x 21% (n=37)", "mais dados"),
    ("J historico/renovacao", "atraso e % atrasadas no contrato anterior; cluster do contrato anterior", "comportamento passado", "M2", "CRM", "17% (137 contratos)", "repeat default 8x (Point Predictive)", "SINAL", "AUC 0,75 (n=137); cluster anterior nao testado", "TESTAR cluster do anterior"),
    ("J historico/renovacao", "intervalo entre o fim do anterior e o novo", "dias de pausa", "M2", "CRM", "-", "-", "SEM_SINAL", "reaplicar em ate 3 dias: sem efeito", "-"),
    ("J historico/renovacao", "conversa antes da renovacao (tom, recusa, demora)", "mesmas features de F aplicadas a renovacao", "M2", "Chatwoot", "-", "-", "NAO_TESTADA", "-", "TESTAR"),
    ("K comportamento inicial", "pagamento nos primeiros dias (D+1..D+14): % no prazo, % atrasadas, atraso max, 1a parcela", "trajetoria de pagamento", "M3", "CRM", "100%", "first payment default (Experian)", "SINAL", "AUC 0,66 (D+3), 0,80 (D+7), 0,85 (D+14)", "ja no motor"),
    ("K comportamento inicial", "falha de cobranca (EFI_PAYMENT_FAILED, PLUGGY_PAYMENT_FAILED)", "eventos de falha por contrato", "M3", "CRM audit_event", "~100k eventos", "-", "NAO_TESTADA", "-", "TESTAR (pode explicar o Pix agendado)"),
    ("L externas/macro", "mes do ano, feriados, calendario escolar", "sazonalidade", "M1", "calendario", "100%", "efeitos sazonais", "NAO_TESTADA", "-", "TESTAR (local)"),
    ("L externas/macro", "Selic, inflacao, desemprego (mes)", "macro", "M1", "BCB/IBGE (publico)", "100%", "macro em modelos PD", "NAO_TESTADA", "-", "TESTAR (local); so vale com historico longo"),
    ("L externas/macro", "INMET/clima, eventos locais", "-", "M1", "-", "-", "-", "BLOQUEADA", "sem hipotese nem ligacao com o cliente", "descartar"),
]

COLS = ["familia", "variavel", "descricao", "momento", "fonte", "disponibilidade", "literatura", "status_teste", "resultado", "proximo_passo"]


# resultados das baterias 1 e 2 (10/11/2026) aplicados por cima das linhas acima: texto de a "variavel" -> (status, resultado, proximo passo)
PATCH = {
    "valor / faturamento declarado": ("SINAL_FRACO", "AUC 0,56 a 0,60 (0,62 a 0,64 sem Pix, n 278-318); ajustado por Pix, provedor e mes: OR 1,44 por desvio padrao (p=0,024, q=0,14); metade nova AUC 0,80 (n=55)", "REPETIR com mais meses; candidato ao D+0"),
    "tom direto/agressivo, recusa, desconfianca, objecoes": ("SINAL_FRACO", "654 contratos, 12 perguntas: quem fala menos e engaja menos da mais calote (AUC 0,39 sem Pix, p=0,0002); ajustado por Pix, provedor e mes: agressivo OR 0,69 (q=0,087), recusa 0,72 (q=0,087), evasivo 0,76; estavel nas duas metades; metade do efeito e tamanho do texto; no modelo do D+0 0,637 -> 0,658 (dentro do IC)", "OBSERVAR; coleta diaria exige chave propria da API Qwen"),
    "print da maquininha (faturamento por OCR)": ("INSUFICIENTE", "768 prints lidos, 106 parecem tela de vendas, so 26 com total vendido: sem poder estatistico", "reavaliar quando houver 100+ casos com total"),
    "telefone/email/endereco/documento compartilhado entre clientes": ("SINAL", "endereco dividido: calote 43% x 30% (p=0,0014); algum dado dividido ajustado por Pix, provedor e mes: OR 1,35 por dp em 3 alvos (q=0,087); telefone/email/responsavel: n 10-24 sem poder", "ENTRA no D+0 (candidato do motor)"),
    "indice de inconsistencias (identidade divergente, terceiro, CNPJ nao confirmado, DDD x CEP, dado faltante)": ("SINAL_FRACO", "AUC 0,52 a 0,55 sozinho (p 0,05-0,39); metade nova 0,67 (n=85)", "manter como candidato no D+0"),
    "atraso e % atrasadas no contrato anterior; cluster do contrato anterior": ("SINAL", "atraso anterior AUC 0,75 (n=137); cluster anterior 'paga e para': novo contrato com calote 82% x 22% (n=11, p=0,0002)", "ENTRA no D+0; confirmar com mais renovacoes"),
    "conversa antes da renovacao (tom, recusa, demora)": ("INSUFICIENTE", "renovacoes com texto e rotulo: n pequeno para testar separado", "reavaliar com mais renovacoes"),
}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "catalogo_variaveis.csv")
    for i, r in enumerate(L):
        if r[1] in PATCH:
            st, res, prox = PATCH[r[1]]
            L[i] = r[:7] + (st, res, prox)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(L)
    from collections import Counter

    c = Counter(r[7] for r in L)
    print(len(L), "variaveis/familias mapeadas:", dict(c))


if __name__ == "__main__":
    main()
