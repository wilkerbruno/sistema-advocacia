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
    regra = None
    if movimentacao.codigo_tpu:
        regra = RegraProximaAcao.query.filter_by(
            codigo_tpu=movimentacao.codigo_tpu, ativo=True
        ).first()

    if regra is None and movimentacao.texto_integral:
        texto = movimentacao.texto_integral.lower()
        for candidata in RegraProximaAcao.query.filter_by(ativo=True).all():
            if candidata.ato_capturado.lower() in texto:
                regra = candidata
                break

    processo = movimentacao.processo
    data_inicial = (publicacao.data_publicacao if publicacao and publicacao.data_publicacao
                     else movimentacao.data.date())

    if regra is None:
        if not permitir_generico:
            return None
        prazo = Prazo(
            processo_id=processo.id,
            publicacao_id=publicacao.id if publicacao else None,
            tipo_ato=movimentacao.texto_integral[:120],
            descricao="Análise necessária — ato sem regra de próxima ação cadastrada",
            data_inicial=data_inicial,
            data_vencimento=data_inicial + timedelta(days=5),  # prazo provisório curto, sempre editável
            calculo_automatico=False,
            prioridade="alta",
            status="pendente",
            responsavel_id=processo.responsavel_id,
        )
        return prazo

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

    responsavel_id = processo.responsavel_id
    prazo = Prazo(
        processo_id=processo.id,
        publicacao_id=publicacao.id if publicacao else None,
        tipo_ato=regra.ato_capturado,
        regra_aplicada_id=regra.id,
        descricao=regra.acao_exigida,
        data_inicial=data_inicial,
        data_vencimento=data_vencimento,
        calculo_automatico=calculo_automatico,
        data_original_calculada=data_vencimento if calculo_automatico else None,
        prioridade="normal",
        status="pendente",
        responsavel_id=responsavel_id,
    )
    return prazo
