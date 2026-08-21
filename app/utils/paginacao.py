"""
Paginação de listas grandes (PENDENCIAS.md, seção -47) — item
"Paginação em listas grandes (processos, painel)" da tabela de
prioridades do relatório de 20/08/2026.

Problema que isto resolve: várias telas de listagem (a mais crítica
sendo Processos, mas também a Fila de intimações e os widgets do Painel
de governança) montavam a query certa, filtrada e ordenada, mas
terminavam com `.all()` — carregando a tabela INTEIRA de uma vez, sem
limite nenhum. Num escritório pequeno isso nem se nota; num escritório
de grande porte (o público-alvo deste sistema, ver
`AUDITORIA_GRANDE_PORTE.md`), com milhares de processos, isso vira uma
tela lenta pra carregar, pesada pro banco, e didicilmente utilizável
(ninguém rola manualmente uma tabela de 3.000 linhas procurando um
processo).

`paginar(query)` é o ponto único usado nas telas de listagem "de
verdade" (linha por linha, com paginação completa — Anterior/Próxima,
número de página): lê "pagina"/"por_pagina" da própria URL
(`?pagina=2`), nunca deixa `por_pagina` passar de `POR_PAGINA_MAXIMO`
mesmo que alguém edite a URL na mão (pra não reabrir o mesmo problema
por outra porta), e nunca quebra a tela com 404 por causa de um
parâmetro de página inválido (`error_out=False` — página fora do
intervalo só volta uma lista vazia).

Para telas tipo dashboard (ex: os widgets do Painel de governança, que
mostram "os N mais urgentes" e não uma listagem linha-a-linha pra
navegar), o padrão usado é diferente: um teto fixo (`limitar_com_total`)
que mostra o total real e um link pra tela completa e paginada (a Fila
de intimações), em vez de adicionar controle de página num widget de
dashboard — mesmo espírito das listas já limitadas em
`app/routes/dashboard.py` (prazos_vencendo, proximas_audiencias etc.).
"""
from flask import request, url_for

POR_PAGINA_PADRAO = 25
POR_PAGINA_MAXIMO = 100

# Teto usado nos widgets tipo dashboard (ver limitar_com_total abaixo) —
# bem maior que uma página normal, porque aqui não tem "próxima página"
# pra recorrer: é só uma rede de segurança contra carregar milhares de
# linha numa tela que não foi pensada pra navegação.
TETO_WIDGET_PADRAO = 50


def paginar(query, por_pagina=POR_PAGINA_PADRAO, por_pagina_maximo=POR_PAGINA_MAXIMO):
    """
    Aplica paginação de verdade (com Anterior/Próxima) numa query já
    filtrada/ordenada, lendo "pagina" e "por_pagina" da própria URL.
    """
    try:
        pagina = int(request.args.get("pagina", 1))
    except (TypeError, ValueError):
        pagina = 1
    pagina = max(1, pagina)

    try:
        tamanho = int(request.args.get("por_pagina", por_pagina))
    except (TypeError, ValueError):
        tamanho = por_pagina
    tamanho = max(1, min(tamanho, por_pagina_maximo))

    return query.paginate(page=pagina, per_page=tamanho, error_out=False)


def limitar_com_total(query, teto=TETO_WIDGET_PADRAO):
    """
    Para widget de dashboard (não uma lista navegável): devolve até
    `teto` itens (já respeitando a ordenação da query) MAIS o total real
    de itens que bateriam no filtro, pra tela poder avisar honestamente
    "mostrando os 50 mais urgentes de 312" em vez de fingir que a lista
    inteira coube. Duas queries (uma count(), uma limit()) — não dá pra
    aproveitar uma só porque LIMIT muda o que COUNT enxergaria.
    """
    total = query.order_by(None).count()
    itens = query.limit(teto).all()
    return itens, total


def url_pagina(pagina):
    """
    Monta a URL da página N mantendo todos os outros parâmetros da URL
    atual (filtro de status, área, termo de busca etc.) — só troca
    "pagina". Registrado como global do Jinja em app/__init__.py, pra
    poder ser chamado direto do template de paginação
    (templates/_paginacao.html) sem precisar passar isso explicitamente
    em toda `render_template`.
    """
    args = request.args.to_dict(flat=True)
    args.update(request.view_args or {})
    args["pagina"] = pagina
    return url_for(request.endpoint, **args)
