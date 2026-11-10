"""Textos curtos (markdown) que ficam ao lado de cada grafico/tabela do dashboard, explicando o que significa. Chave = titulo do cartao."""
AJUDA = {
    # ---- aba Consulta por contrato
    "Contratos do cliente": "**O que é**\nTodos os contratos do cliente escolhido, com as três chances de problema e o nível de risco.\n\n**Como ler**\nOlhe o *Nível de risco* junto com a *Confiança do modelo*. Confiança baixa = use como alerta, não como certeza.",
    "Chance de o contrato cair em cada grupo de comportamento (contrato mais recente)": "**O que é**\nPara onde o contrato tende a ir. Cada barra é a chance de ele terminar naquele jeito de pagar. As barras somam 100%.\n\n**Atenção**\n\"Paga no começo e para\" é o padrão de abandono ou golpe.",
    "Chance de calote, inadimplência e cluster ruim": "**O que é**\nTrês perguntas diferentes sobre o mesmo contrato.\n\n- **Calote:** parcelas vencidas e em aberto\n- **Inadimplência:** atraso de mais de 3 dias (quase todos têm)\n- **Cluster ruim:** abandono",
    "O risco de calote subiu ou desceu ao longo do contrato?": "**O que é**\nA chance de calote calculada de novo a cada etapa da vida do contrato.\n\n**Como ler**\nLinha subindo = o contrato está piorando. Nos primeiros dias o modelo é menos confiável.",
    "O que mais empurra o risco deste contrato para cima": "**O que é**\nOs fatores que mais aumentam o risco deste contrato.\n\n**Como ler**\nQuanto maior o número, mais o fator pesa. \"Sem informação\" quer dizer que o dado faltava.",
    "Parcelas do contrato": "**O que é**\nCada parcela do contrato, com vencimento, pagamento e atraso.\n\n**Como ler**\nProcure atrasos e parcelas vencidas em aberto.",
    "Resumo do cliente": "**O que é**\nResumo do cliente montado com cadastro, contratos e conversas.\n\nSe aparecer um aviso, o resumo ainda não foi gerado.",
    # ---- aba Contratos que pioraram
    "Contratos que pioraram": "**Quantos contratos ativos** tiveram o risco subindo mais de 3 pontos entre duas avaliações.",
    "Valor dos contratos que pioraram (R$)": "**Soma do valor** dos contratos que pioraram.",
    "Contratos que mudaram para um nível de risco pior": "**Quantos** saltaram de nível, por exemplo de Médio para Alto.",
    "Contratos que pioraram: do que mais precisa de atenção (1) para o que menos": "**O que é**\nFila de atenção. A posição 1 é o contrato para olhar primeiro.\n\n**Prioridade** = chance de calote × valor × (1 + piora).\n\n**Dica:** use o telefone para o contato.",
    "Contratos que pioraram, por nível de risco atual": "**O que é**\nEm que nível de risco estão hoje os contratos que pioraram.\n\nMuitos em \"Muito alto\" = piora de quem já estava mal.",
    "Quanto o risco piorou em média, por momento do contrato (pontos percentuais)": "**O que é**\nO quanto o risco subiu, em média, em cada momento do contrato.\n\n10 pontos = a chance de calote subiu 10 pontos percentuais.",
    # ---- aba API e pesos
    "Fatores mais importantes de cada momento (previsão de calote)": "**O que é**\nOs fatores que mais pesam na previsão em cada momento.\n\n**Como ler**\nCompare os pesos só dentro do mesmo momento: modelos diferentes usam escalas diferentes.",
    "Quão confiável é o modelo em cada momento (testado em contratos que ele não viu)": "**O que é**\nQuanto o modelo acerta em contratos que ele não viu no treino.\n\n**Acerto:** 0,5 = chute, 1 = perfeito. O intervalo mostra a margem de erro.",
    # ---- aba Motor de credito
    "Contratos pontuados hoje": "**Quantos contratos** receberam risco no último cálculo.",
    "Em risco alto ou muito alto": "**Quantos contratos** estão com chance de calote acima de 30%.",
    "Valor principal em risco alto ou muito alto": "**Soma do valor** dos contratos em risco alto ou muito alto.",
    "Probabilidade média de calote": "**Média** da chance de calote de todos os contratos pontuados.",
    "Contratos por faixa de risco e confiança do modelo": "**O que é**\nQuantos contratos há em cada nível de risco, separados pela confiança do modelo.\n\nBarras em confiança Nenhuma/Baixa: use com cuidado.",
    "Contratos por idade (etapa do motor) e faixa": "**O que é**\nOs contratos agrupados pela idade e pelo nível de risco.\n\nQuanto mais velho o contrato, mais confiável o risco.",
    "Contratos de maior risco com confiança média ou alta": "**O que é**\nOs 40 contratos de maior risco em que o modelo tem confiança média ou alta.\n\nÉ a lista para a cobrança agir primeiro.",
    "Confiança do motor por idade do contrato (AUC fora do tempo, modelo escolhido)": "**O que é**\nO acerto do modelo em cada idade do contrato.\n\n0,5 = chute, 1 = perfeito. Sobe com os dias porque o comportamento de pagamento aparece.",
    "Calibração na etapa D+7: previsto x observado por décimo de risco": "**O que é**\nCompara o que o modelo previu com o que aconteceu, em dez grupos de risco.\n\nBarras do mesmo tamanho = o modelo está calibrado.",
    "Fatores mais importantes na etapa D+7": "**O que é**\nOs fatores que mais pesam na previsão do dia 7.\n\nPeso maior = mais influência no risco.",
    "AUC do motor por dia de execução (D+7 e D+14)": "**O que é**\nComo o acerto do modelo evolui a cada dia em que ele é refeito.\n\nQueda forte = alerta de que o modelo piorou.",
    "Calote em 30 dias: com e sem Pix agendado, por provedor (contratos de jun/26 em diante)": "**O que é**\nA taxa de calote com e sem cobrança por Pix agendado, em cada provedor.\n\nPix agendado dá calote bem maior, no mesmo provedor.",
    "Participação do Pix agendado nos contratos novos, por mês": "**O que é**\nA parcela dos contratos novos que usam Pix agendado, mês a mês.\n\nO uso passou de poucos casos em maio para quase todos em outubro.",
}


def larg_ajuda(w):
    return max(3, round(w * 0.28))

AJUDA.update({
    "Variáveis que mais se relacionam com o calote": "**O que é**\nAs 15 variáveis mais ligadas ao calote.\n\n**Como ler**\nCorrelação perto de +1 = mais da variável, mais calote; perto de -1 = o contrário. A lista completa e o liga/desliga estão na aba Motor de crédito do Data IA Hub.",
    "Calote real até o dia 45 por canal de cobrança (contratos de junho em diante)": "**O que é**\nA taxa de calote que realmente aconteceu, por canal de cobrança.\n\n**Como ler**\nO canal explica quase tudo que dá para prever na assinatura.",
    "Contratos ativos avaliados hoje": "**Quantos contratos** receberam avaliação de risco no último cálculo.",
    "Contratos ativos em risco Alto ou Muito alto": "**Quantos contratos** estão com chance de calote acima de 30%.",
    "Valor dos contratos ativos em risco Alto ou Muito alto": "**Soma do valor** desses contratos: o dinheiro mais exposto.",
    "Chance média de calote dos contratos ativos": "**Média** da chance de calote de todos os contratos avaliados.",
    "Contratos por nível de risco e confiança do modelo": "**O que é**\nQuantos contratos há em cada nível de risco, separados pela confiança do modelo.\n\nBarras com confiança Nenhuma ou Baixa: use com cuidado.",
    "Contratos por idade e nível de risco": "**O que é**\nOs contratos agrupados pela idade e pelo nível de risco.\n\nQuanto mais velho o contrato, mais confiável o risco.",
    "Contratos ativos de maior risco com confiança Média ou Alta": "**O que é**\nOs 40 contratos de maior risco em que o modelo tem confiança média ou alta.\n\nÉ a lista para a cobrança agir primeiro.",
    "Acerto do modelo por idade do contrato": "**O que é**\nO quanto o modelo acerta em cada idade do contrato, para cada tipo de previsão.\n\n0,5 = chute, 1 = perfeito. Sobe com os dias porque o comportamento de pagamento aparece.",
    "Previsto contra o que aconteceu, por grupo de risco (dia 7)": "**O que é**\nOs contratos são divididos em 10 grupos, do menor ao maior risco. Compara o que o modelo previu com o que realmente aconteceu.\n\nBarras do mesmo tamanho = o modelo está bem calibrado.",
    "Fatores que mais pesam na previsão do dia 7": "**O que é**\nOs fatores que mais influenciam a previsão no dia 7.\n\nPeso maior = mais influência.",
    "Acerto do modelo a cada dia em que é refeito (dias 7 e 14)": "**O que é**\nComo o acerto muda a cada dia em que o modelo é refeito.\n\nQueda forte = o modelo piorou e merece atenção.",
    "Calote em 30 dias com e sem Pix agendado, por provedor (contratos de junho em diante)": "**O que é**\nA taxa de calote com e sem cobrança por Pix agendado, em cada provedor.\n\nPix agendado dá calote bem maior, no mesmo provedor.",
    "Participação do Pix agendado nos contratos novos, por mês": "**O que é**\nA parcela dos contratos novos que usam Pix agendado, mês a mês.\n\nPassou de poucos casos em maio para quase todos em outubro.",
    "Combinações de informações testadas": "**Quantas combinações** de 1 a 4 informações (provedor, idade, valor, tom da conversa...) foram cruzadas com o calote.",
    "Combinações com sinal depois da correção": "**Quantas** resistiram à correção de falsa descoberta. Muitas se repetem entre si (mesmo grupo de clientes).",
    "Informações testadas com controle de Pix e mês": "**Quantas informações** tiveram o efeito medido descontando Pix, provedor e mês.",
    "Algoritmos comparados": "**Quantos tipos de modelo** foram comparados, além do comitê que faz a média deles.",
    "Efeito de cada informação, descontando Pix, provedor e mês": "**O que é**\nO efeito de cada informação já descontando Pix, provedor e mês de início.\n\n**Como ler**\n\"Protege\" = quem tem mais disso dá menos calote. \"Passa na correção\" = o achado resistiu ao teste de falsa descoberta.",
    "Quanto cada algoritmo acerta em média (todos os testes)": "**O que é**\nO acerto médio de cada tipo de modelo, em todos os testes feitos fora do tempo.\n\nDiferenças pequenas cabem na margem de erro: nenhum modelo é muito melhor que os outros.",
    "Quanto cada conjunto de informações acerta em média": "**O que é**\nO acerto médio de cada conjunto de informações.\n\nMais informação nem sempre ajuda: com poucos contratos o modelo decora.",
    "Melhor combinação de modelo e informações em cada situação": "**O que é**\nO modelo e o conjunto de informações com maior acerto em cada tipo de previsão.\n\nCom poucos contratos de teste, o melhor pode ser sorte: veja a coluna de contratos.",
    "Perfis de clientes com taxa muito diferente da média (que passam na correção)": "**O que é**\nGrupos de clientes (combinando até 3 informações) cuja taxa de calote é bem diferente da média.\n\nOs perfis se sobrepõem: o mesmo cliente aparece em vários.",
    "Sazonalidade: dia da semana, semana do mês e mês de início": "**O que é**\nTeste se o calote muda com o dia da semana, a semana do mês ou o mês em que o contrato começou.\n\n\"Mês de início\" existindo = a safra importa.",
    "Todas as informações candidatas e o que se descobriu": "**O que é**\nA lista de todas as informações que podem explicar o calote e o resultado de cada uma.\n\n\"Poucos dados\" = ainda não dá para concluir.",
})


import re as _re  # noqa: E402

AJUDA = {k: _re.sub(r"(\*\*[^*\n]+\*\*)\n(?!\n)", r"\1\n\n", v) for k, v in AJUDA.items()}   # titulo em negrito numa linha so, texto no paragrafo seguinte
