"""
Detecção automática de audiência a partir do texto de uma movimentação
capturada (PENDENCIAS.md, seção -77) — pedido explícito do usuário depois
de testar a captura pelo e-SAJ público: "buscar também... as audiências
com seus status ... reais".

Por que isto é heurístico, não uma leitura estruturada:

Nem o DataJud nem o e-SAJ público (ver app/utils/conector_esaj_publico.py)
devolvem hoje um campo estruturado "isto é uma audiência, data X, status Y"
— só texto livre de movimentação (`Movimentacao.texto_integral`, mais
`complemento` quando houver). O DataJud até tem código TPU (Tabela
Processual Unificada) por movimentação, mas não existe hoje neste projeto
uma tabela confiável de "quais códigos TPU são de audiência" (mesma
cautela já registrada em app/utils/tribunais_datajud.py para outro
problema parecido) — por isso este módulo reconhece por PADRÃO DE TEXTO em
português, não por código.

Isso significa, na prática:
- Cobre as fórmulas mais comuns ("audiência ... designada para
  DD/MM/AAAA às HH:MM", "audiência realizada", "audiência cancelada",
  "audiência redesignada/remarcada"), mas qualquer tribunal que fugir
  muito dessas fórmulas simplesmente não é reconhecido — a movimentação
  continua registrada normalmente (aba Governança), só não vira Audiencia
  sozinha. NUNCA finge ter reconhecido algo incerto.
- Toda Audiencia criada ou atualizada por aqui fica marcada com
  `deteccao_automatica=True` e `movimentacao_id` apontando pro texto de
  origem exato (ver Audiencia em app/models/processo.py) — nunca se
  disfarça de cadastro manual, e sempre dá pra conferir/corrigir.
- Uma movimentação de status (realizada/cancelada/remarcada) só atualiza
  uma Audiencia já existente com status "agendada" — nunca inventa uma
  audiência do nada sem pelo menos uma data (ou uma audiência agendada
  prévia pra associar o status). Quando há mais de uma "agendada" no
  processo, escolhe a mais próxima em data da própria movimentação (mais
  provável de ser a mesma referida) — outra aproximação, não uma certeza.
- Uma audiência "designada" só é criada quando dá pra extrair uma data no
  texto (dd/mm/aaaa) — sem data extraível, não cria nada (não dá pra
  agendar uma audiência sem data).
- Deduplicação: antes de criar uma nova audiência "designada", confere se
  já existe uma com a MESMA data/hora neste processo (evita duplicar
  quando duas fontes diferentes — ou duas capturas do mesmo texto —
  relatam o mesmo evento).
"""
import re
from datetime import datetime

from app.extensions import db
from app.models import Audiencia

_RE_DATA = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_RE_HORA = re.compile(r"(?:às|as)\s*(\d{1,2})[:h](\d{2})", re.IGNORECASE)

_TIPOS_PADROES = (
    (re.compile(r"instru[cç][ãa]o\s+e\s+julgamento", re.IGNORECASE), "instrução e julgamento"),
    (re.compile(r"concilia[cç][ãa]o", re.IGNORECASE), "conciliação"),
    (re.compile(r"instru[cç][ãa]o", re.IGNORECASE), "instrução"),
    (re.compile(r"julgamento", re.IGNORECASE), "julgamento"),
    (re.compile(r"justifica[cç][ãa]o", re.IGNORECASE), "justificação"),
    (re.compile(r"admonit[oó]ria", re.IGNORECASE), "admonitória"),
    (re.compile(r"\buna\b", re.IGNORECASE), "una"),
)

_RE_TEM_AUDIENCIA = re.compile(r"audi[êe]ncia", re.IGNORECASE)
_RE_CANCELADA = re.compile(r"cancelada|n[ãa]o\s+(?:foi\s+)?realizada|n[ãa]o\s+houve", re.IGNORECASE)
_RE_REALIZADA = re.compile(r"realizada", re.IGNORECASE)
_RE_REMARCADA = re.compile(r"redesignada|remarcada", re.IGNORECASE)
_RE_DESIGNADA = re.compile(r"designada|marcada|aprazada|agendada", re.IGNORECASE)


def _texto_busca(movimentacao) -> str:
    return " ".join(p for p in (movimentacao.texto_integral, movimentacao.complemento) if p)


def _classificar(texto: str) -> str | None:
    """Devolve "designada", "remarcada", "realizada", "cancelada" ou None
    (não parece ser sobre audiência, ou não bate com nenhum padrão
    conhecido). A ordem dos testes importa: "redesignada"/"remarcada"
    contêm a substring "designada"/"marcada", então precisam ser
    conferidos ANTES do padrão genérico, senão toda redesignação seria
    classificada como uma designação nova por engano. Da mesma forma,
    "não realizada" precisa ser conferida antes de "realizada"."""
    if not _RE_TEM_AUDIENCIA.search(texto):
        return None
    if _RE_CANCELADA.search(texto):
        return "cancelada"
    if _RE_REALIZADA.search(texto):
        return "realizada"
    if _RE_REMARCADA.search(texto):
        return "remarcada"
    if _RE_DESIGNADA.search(texto):
        return "designada"
    return None


def _extrair_tipo(texto: str) -> str | None:
    for padrao, nome in _TIPOS_PADROES:
        if padrao.search(texto):
            return nome
    return None


def _extrair_data_hora(texto: str) -> tuple[datetime, bool] | None:
    """(data_hora, teve_horario). None quando não dá pra achar nem a data —
    sem data não tem o que criar/mover com segurança."""
    m_data = _RE_DATA.search(texto)
    if not m_data:
        return None
    dia, mes, ano = (int(v) for v in m_data.groups())
    try:
        from datetime import date as _date
        _date(ano, mes, dia)
    except ValueError:
        return None
    m_hora = _RE_HORA.search(texto)
    if m_hora:
        hora, minuto = (int(v) for v in m_hora.groups())
        try:
            return datetime(ano, mes, dia, hora, minuto), True
        except ValueError:
            pass
    return datetime(ano, mes, dia, 0, 0), False


def _observacao_auto(movimentacao, texto: str, teve_horario: bool = True, extra: str | None = None) -> str:
    data_mov = movimentacao.data.strftime("%d/%m/%Y") if movimentacao.data else "?"
    nota = f"Detectado automaticamente a partir da movimentação de {data_mov}: \"{texto[:200]}\""
    if not teve_horario:
        nota += " (horário não informado na movimentação — confira antes de contar com ele)."
    if extra:
        nota += f" {extra}"
    return nota


def _audiencia_mais_proxima(processo_id: int, referencia: datetime):
    """A audiência "agendada" deste processo mais provável de ser a mesma
    referida por uma movimentação de status (realizada/cancelada/
    remarcada) sem data nova — a de data mais próxima da própria
    movimentação. None quando não há nenhuma "agendada" pra associar
    (nunca inventa uma audiência do nada só por causa de um status)."""
    candidatas = Audiencia.query.filter_by(processo_id=processo_id, status="agendada").all()
    if not candidatas:
        return None
    return min(candidatas, key=lambda a: abs((a.data_hora - referencia).total_seconds()))


def detectar_e_aplicar_audiencia(movimentacao) -> "Audiencia | None":
    """
    Ponto de entrada — chamado para CADA movimentação nova persistida
    (ver app/utils/captura_pipeline.py::registrar_movimentacoes_capturadas),
    de qualquer origem (DataJud ou e-SAJ público, ver `origem_captura`).

    Devolve a Audiencia criada/atualizada (ainda não commitada — quem
    chama decide o commit, mesmo padrão de `aplicar_regra_proxima_acao`),
    ou None quando o texto não pareceu ser sobre audiência, ou pareceu mas
    faltou informação suficiente pra agir com segurança (ver docstring do
    módulo acima para os casos exatos).
    """
    texto = _texto_busca(movimentacao)
    tipo_evento = _classificar(texto)
    if tipo_evento is None:
        return None

    processo_id = movimentacao.processo_id
    referencia = movimentacao.data or datetime.utcnow()

    if tipo_evento == "designada":
        extraido = _extrair_data_hora(texto)
        if not extraido:
            return None
        data_hora, teve_horario = extraido
        ja_existe = Audiencia.query.filter_by(processo_id=processo_id, data_hora=data_hora).first()
        if ja_existe:
            return None
        audiencia = Audiencia(
            processo_id=processo_id, tipo=_extrair_tipo(texto), data_hora=data_hora,
            status="agendada", deteccao_automatica=True, movimentacao_id=movimentacao.id,
            observacoes=_observacao_auto(movimentacao, texto, teve_horario),
        )
        db.session.add(audiencia)
        return audiencia

    if tipo_evento == "remarcada":
        extraido = _extrair_data_hora(texto)
        candidata = _audiencia_mais_proxima(processo_id, referencia)
        if extraido:
            data_hora, teve_horario = extraido
            if candidata:
                data_antiga = candidata.data_hora.strftime("%d/%m/%Y %H:%M")
                candidata.data_hora = data_hora
                candidata.deteccao_automatica = True
                candidata.movimentacao_id = movimentacao.id
                nota = _observacao_auto(movimentacao, texto, teve_horario, extra=f"(era {data_antiga}.)")
                candidata.observacoes = f"{candidata.observacoes}\n{nota}" if candidata.observacoes else nota
                return candidata
            audiencia = Audiencia(
                processo_id=processo_id, tipo=_extrair_tipo(texto), data_hora=data_hora,
                status="agendada", deteccao_automatica=True, movimentacao_id=movimentacao.id,
                observacoes=_observacao_auto(movimentacao, texto, teve_horario),
            )
            db.session.add(audiencia)
            return audiencia
        if candidata:
            candidata.status = "remarcada"
            candidata.deteccao_automatica = True
            candidata.movimentacao_id = movimentacao.id
            nota = _observacao_auto(movimentacao, texto, extra="(nova data ainda não informada.)")
            candidata.observacoes = f"{candidata.observacoes}\n{nota}" if candidata.observacoes else nota
            return candidata
        return None

    if tipo_evento in ("realizada", "cancelada"):
        candidata = _audiencia_mais_proxima(processo_id, referencia)
        if not candidata:
            return None
        candidata.status = tipo_evento
        candidata.deteccao_automatica = True
        candidata.movimentacao_id = movimentacao.id
        nota = _observacao_auto(movimentacao, texto)
        candidata.observacoes = f"{candidata.observacoes}\n{nota}" if candidata.observacoes else nota
        return candidata

    return None
