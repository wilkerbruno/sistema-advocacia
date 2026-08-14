"""
Camada de tradução do vocabulário processual (seção 6 do briefing).

Traduz o código bruto da movimentação (TPU/CNJ) para o estado de negócio
correspondente, usando o mapa cadastrado em `MapaEstadoTPU` (editável por
tela — ver app/routes/governanca.py). Quando o código não está mapeado, o
requisito do briefing é explícito: "movimentação não mapeada cai em fila
de triagem, nunca é descartada em silêncio" — por isso a movimentação é
marcada com `triagem_pendente=True` em vez de ficar sem estado.
"""
from datetime import datetime

from app.models import HistoricoEstadoProcesso, MapaEstadoTPU


def traduzir_movimentacao(movimentacao):
    """
    Aplica a máquina de estados a uma Movimentacao recém-capturada/registrada:
    - Busca o código TPU no MapaEstadoTPU.
    - Se encontrado: atualiza `estado_negocio_resultante` na movimentação,
      `estado_negocio_atual` no processo, e cria um HistoricoEstadoProcesso
      (evento datado, usado para medir tempo por fase — seção 9).
    - Se não encontrado: marca `triagem_pendente=True` na movimentação e
      NÃO altera o estado do processo.

    Não faz commit — quem chama decide o commit (permite agrupar com outras
    operações, ex: aplicar_regra_proxima_acao, na mesma transação).
    """
    processo = movimentacao.processo

    if not movimentacao.codigo_tpu:
        movimentacao.triagem_pendente = True
        return None

    mapa = MapaEstadoTPU.query.filter_by(codigo_tpu=movimentacao.codigo_tpu, ativo=True).first()
    if mapa is None:
        movimentacao.triagem_pendente = True
        return None

    movimentacao.triagem_pendente = False
    movimentacao.estado_negocio_resultante = mapa.estado_negocio
    processo.estado_negocio_atual = mapa.estado_negocio
    processo.ultima_movimentacao_em = movimentacao.data

    historico = HistoricoEstadoProcesso(
        processo_id=processo.id,
        estado_negocio=mapa.estado_negocio,
        data_evento=movimentacao.data or datetime.utcnow(),
        origem_movimentacao_id=movimentacao.id,
    )
    return historico
