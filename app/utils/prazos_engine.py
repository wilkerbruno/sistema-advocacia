"""
Motor de prazos (seção 7 do briefing).

Cálculo automático de data fatal em dias úteis (CPC art. 219), lendo a
tabela `Feriado` (nacional, por tribunal, ou período — recesso forense).
Também aplica o motor de próxima ação (seção 7.1): dado um ato/código TPU
capturado, cria automaticamente o `Prazo` correspondente usando a regra
cadastrada em `RegraProximaAcao`.

Limitação assumida: o calendário de feriados (`Feriado`) precisa estar
populado por tribunal para o cálculo ser preciso. O sistema já vem com os
feriados nacionais fixos e o recesso forense (20/12–20/01) — feriados
forenses locais por tribunal/comarca continuam dependendo de cadastro
manual (não há fonte pública única e estruturada para isso).
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Feriado, Prazo, RegraProximaAcao


def _feriados_no_intervalo(data_ini: date, data_fim: date, tribunal: str | None):
    """Devolve o conjunto de datas não úteis (feriados/recesso) no intervalo,
    considerando feriados nacionais (tribunal nulo) e os do tribunal informado."""
    query = Feriado.query.filter(
        db.or_(Feriado.tribunal.is_(None), Feriado.tribunal == tribunal)
    )
    datas_bloqueadas = set()
    for feriado in query.all():
        if feriado.abrange_todo_periodo and feriado.data_fim:
            d = feriado.data
            while d <= feriado.data_fim:
                if data_ini <= d <= data_fim:
                    datas_bloqueadas.add(d)
                d += timedelta(days=1)
        else:
            if data_ini <= feriado.data <= data_fim:
                datas_bloqueadas.add(feriado.data)
    return datas_bloqueadas


def eh_dia_util(dia: date, tribunal: str | None = None) -> bool:
    if dia.weekday() >= 5:  # sábado=5, domingo=6
        return False
    bloqueados = _feriados_no_intervalo(dia, dia, tribunal)
    return dia not in bloqueados


def proxima_data_util(dia: date, tribunal: str | None = None) -> date:
    """
    Primeiro dia útil ESTRITAMENTE POSTERIOR a `dia` (nunca o próprio `dia`,
    mesmo que já seja útil) — usado para achar a "data de publicação" a
    partir da "data de disponibilização" de uma publicação no Diário de
    Justiça Eletrônico (Lei 11.419/2006, art. 4º, §3º: "Considera-se como
    data da publicação o primeiro dia útil seguinte ao da disponibilização
    da informação no Diário da Justiça eletrônico"). Ver
    app/utils/conector_djen.py, onde isso alimenta `Prazo.data_inicial`
    pela captura por OAB (item 1 — PENDENCIAS.md, seção -102).
    """
    d = dia
    # margem de segurança — mesmo raciocínio de calcular_data_fatal abaixo
    for _ in range(60):
        d += timedelta(days=1)
        if eh_dia_util(d, tribunal):
            return d
    return d  # calendário mal cadastrado — devolve o melhor palpite em vez de travar


def calcular_data_fatal(data_inicial: date, dias: int, tribunal: str | None = None,
                         unidade_prazo: str = "dias_uteis", prazo_em_dobro: bool = False) -> date:
    """
    Calcula a data fatal a partir da data inicial (publicação/ciência),
    contando `dias` dias úteis (padrão CPC art. 219) ou corridos, pulando
    fins de semana e feriados/recesso forense do tribunal informado.

    `prazo_em_dobro`: dobra a contagem de dias (litisconsórcio com
    procuradores distintos, Fazenda Pública, Defensoria etc. — art. 229/183
    CPC), quando aplicável ao caso.
    """
    total_dias = dias * 2 if prazo_em_dobro else dias

    if unidade_prazo == "dias_corridos":
        return data_inicial + timedelta(days=total_dias)

    # dias_uteis (padrão CPC art. 219: só se contam dias úteis)
    contados = 0
    d = data_inicial
    # margem de segurança para não entrar em loop infinito em calendário mal cadastrado
    limite_iteracoes = total_dias * 5 + 60
    iteracoes = 0
    while contados < total_dias and iteracoes < limite_iteracoes:
        d += timedelta(days=1)
        iteracoes += 1
        if eh_dia_util(d, tribunal):
            contados += 1
    return d


def _encontrar_regra(codigo_tpu, texto):
    """Compartilhado por `aplicar_regra_proxima_acao` (Movimentacao) e
    `aplicar_regra_a_publicacao` (Publicacao, item 1 — PENDENCIAS.md, seção
    -102) — mesma lógica de sempre: por código TPU primeiro, por texto do
    ato contido na descrição como aproximação depois."""
    regra = None
    if codigo_tpu:
        regra = RegraProximaAcao.query.filter_by(codigo_tpu=codigo_tpu, ativo=True).first()

    if regra is None and texto:
        texto_lower = texto.lower()
        for candidata in RegraProximaAcao.query.filter_by(ativo=True).all():
            if candidata.ato_capturado.lower() in texto_lower:
                regra = candidata
                break
    return regra


def _montar_prazo(processo, regra, data_inicial, tipo_ato_fallback, publicacao_id=None):
    """Compartilhado pelas duas funções públicas abaixo — monta o `Prazo`
    (não commitado) a partir de uma regra já encontrada (ou None, caso em
    que gera o prazo genérico de "análise necessária" — seção 7.1: "ato
    sem regra cadastrada gera tarefa genérica de análise, nunca é
    ignorado")."""
    if regra is None:
        return Prazo(
            processo_id=processo.id,
            publicacao_id=publicacao_id,
            tipo_ato=(tipo_ato_fallback or "")[:120],
            descricao="Análise necessária — ato sem regra de próxima ação cadastrada",
            data_inicial=data_inicial,
            data_vencimento=data_inicial + timedelta(days=5),  # prazo provisório curto, sempre editável
            calculo_automatico=False,
            prioridade="alta",
            status="pendente",
            responsavel_id=processo.responsavel_id,
        )

    if regra.unidade_prazo == "data_evento" or regra.prazo_base_dias is None:
        # prazo depende de data de evento (ex: audiência) ou "conforme despacho" —
        # não é calculável automaticamente; cria com data provisória e marca para revisão manual.
        data_vencimento = data_inicial + timedelta(days=15)
        calculo_automatico = False
    else:
        data_vencimento = calcular_data_fatal(
            data_inicial, regra.prazo_base_dias,
            tribunal=processo.tribunal, unidade_prazo=regra.unidade_prazo,
        )
        calculo_automatico = True

    return Prazo(
        processo_id=processo.id,
        publicacao_id=publicacao_id,
        tipo_ato=regra.ato_capturado,
        regra_aplicada_id=regra.id,
        descricao=regra.acao_exigida,
        data_inicial=data_inicial,
        data_vencimento=data_vencimento,
        calculo_automatico=calculo_automatico,
        data_original_calculada=data_vencimento if calculo_automatico else None,
        prioridade="normal",
        status="pendente",
        responsavel_id=processo.responsavel_id,
    )


def aplicar_regra_proxima_acao(movimentacao, publicacao=None, permitir_generico=True):
    """
    Motor de próxima ação (seção 7.1): dado um ato capturado (Movimentacao),
    procura regra cadastrada por código TPU e, se não achar (comum
    enquanto o registro é manual e não há código TPU confiável — ver
    captura_conectores.py), tenta casar pelo texto do ato (`ato_capturado`)
    contido no texto da movimentação, como aproximação.

    Se não houver regra cadastrada, cria uma tarefa/prazo genérico de
    análise (nunca ignora o ato) — conforme exigido na seção 7.1:
    "Ato sem regra cadastrada gera tarefa genérica de análise, nunca é
    ignorado."

    `permitir_generico=False`: usado pra atos ANTIGOS sem regra cadastrada
    (ver critério de "antigo" — janela de dias — em
    captura_pipeline.JANELA_DIAS_MOVIMENTACAO_RECENTE e o motivo completo no
    docstring de captura_pipeline.registrar_movimentacoes_capturadas, seção
    -34 do PENDENCIAS.md) para NÃO criar o prazo genérico de "análise
    necessária" — um processo antigo capturado de uma vez (ou uma
    movimentação antiga só indexada tarde pelo tribunal, numa captura
    periódica) pode trazer dezenas desses, cada um com vencimento já
    expirado há anos, o que só cria ruído/alarme falso na tela de Prazos (o
    ato mais antigo sem regra já foi sucedido por outros atos depois — quem
    precisa de atenção é o mais recente). A movimentação continua
    registrada e visível (aba Governança, badge "triagem pendente") de
    qualquer forma — isso aqui só evita virar uma tarefa de prazo fantasma;
    quando HÁ regra cadastrada (por código ou por texto) o prazo sempre é
    gerado, não importa a data.

    Retorna o Prazo criado (não commitado — quem chama decide o commit).
    """
    regra = _encontrar_regra(movimentacao.codigo_tpu, movimentacao.texto_integral)
    processo = movimentacao.processo
    data_inicial = (publicacao.data_publicacao if publicacao and publicacao.data_publicacao
                     else movimentacao.data.date())

    if regra is None and not permitir_generico:
        return None

    return _montar_prazo(processo, regra, data_inicial, movimentacao.texto_integral,
                          publicacao_id=publicacao.id if publicacao else None)


def aplicar_regra_a_publicacao(publicacao, permitir_generico=True):
    """
    Mesmo motor de próxima ação de `aplicar_regra_proxima_acao` acima,
    aplicado direto a uma PUBLICAÇÃO (Diário de Justiça Eletrônico) em vez
    de uma Movimentacao — necessário pela captura por OAB (item 1 da lista
    de pipeline de IA jurídica — PENDENCIAS.md, seção -102): a API Comunica
    devolve publicações, não movimentações do sistema do tribunal (são
    fontes independentes: a mesma intimação pode aparecer nas duas, só
    numa delas, ou só na outra, dependendo de como cada tribunal alimenta
    cada base).

    Casa a regra pelo TEXTO da publicação (`publicacao.teor`) — API Comunica
    não expõe código TPU. Data inicial: `publicacao.data_publicacao`
    (já calculada como o 1º dia útil seguinte à disponibilização — ver
    app/utils/conector_djen.py e app/utils/prazos_engine.py::
    proxima_data_util) ou, na falta dela, `data_disponibilizacao`.

    Retorna o Prazo criado (não commitado — quem chama decide o commit).
    """
    regra = _encontrar_regra(None, publicacao.teor)
    processo = publicacao.processo
    data_inicial = publicacao.data_publicacao or publicacao.data_disponibilizacao

    if regra is None and not permitir_generico:
        return None

    return _montar_prazo(processo, regra, data_inicial,
                          publicacao.teor or "Publicação DJEN sem texto", publicacao_id=publicacao.id)
