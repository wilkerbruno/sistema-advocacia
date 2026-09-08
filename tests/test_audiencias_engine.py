"""
Detecção automática de audiência a partir do texto de uma movimentação
capturada (PENDENCIAS.md, seção -77) — app/utils/audiencias_engine.py.
"""
from datetime import datetime

import pytest

from app.extensions import db
from app.models import Audiencia, Cliente, Movimentacao, Processo
from app.utils import audiencias_engine as mod
from app.utils.captura_conectores import MovimentacaoCapturada
from app.utils.captura_pipeline import registrar_movimentacoes_capturadas


@pytest.fixture()
def processo(app, empresa_basica):
    unidade_id = empresa_basica["unidade_id"]
    cliente = Cliente(nome="Cliente Teste", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    proc = Processo(numero_processo="1234567-89.2023.8.26.0100", cliente_id=cliente.id,
                     unidade_id=unidade_id, area_direito="Cível", status="ativo")
    db.session.add(proc)
    db.session.commit()
    return proc


def _mov(processo_id, texto, data=None, complemento=None):
    m = Movimentacao(processo_id=processo_id, data=data or datetime(2026, 1, 10),
                      texto_integral=texto, origem_captura="esaj_publico",
                      hash_dedup=f"hash-{texto}-{complemento}", complemento=complemento)
    db.session.add(m)
    db.session.flush()
    return m


def test_texto_sem_audiencia_nao_faz_nada(app, processo):
    m = _mov(processo.id, "Juntada de petição de contestação")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado is None
    assert Audiencia.query.count() == 0


def test_designada_com_data_e_hora_cria_audiencia(app, processo):
    m = _mov(processo.id, "Audiência de conciliação designada para 15/03/2027 às 14:30 na sala 3")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    db.session.commit()
    assert resultado is not None
    assert resultado.processo_id == processo.id
    assert resultado.data_hora == datetime(2027, 3, 15, 14, 30)
    assert resultado.tipo == "conciliação"
    assert resultado.status == "agendada"
    assert resultado.deteccao_automatica is True
    assert resultado.movimentacao_id == m.id
    assert "Detectado automaticamente" in resultado.observacoes


def test_designada_sem_data_nao_cria_nada():
    """"Audiência a ser designada por carta precatória" contém a palavra
    "designada" mas não tem data nenhuma pra extrair — não dá pra agendar
    uma audiência sem data, então não cria nada."""
    texto = "Expedida carta precatória; audiência a ser designada oportunamente"
    assert mod._classificar(texto) == "designada"
    assert mod._extrair_data_hora(texto) is None


def test_designada_sem_horario_marca_meia_noite_e_avisa(app, processo):
    m = _mov(processo.id, "Audiência designada para 20/04/2027")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado.data_hora == datetime(2027, 4, 20, 0, 0)
    assert "horário não informado" in resultado.observacoes


def test_dedup_nao_duplica_mesma_data_hora(app, processo):
    existente = Audiencia(processo_id=processo.id, data_hora=datetime(2027, 3, 15, 14, 30),
                           status="agendada", tipo="conciliação")
    db.session.add(existente)
    db.session.commit()

    m = _mov(processo.id, "Audiência de conciliação designada para 15/03/2027 às 14:30")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado is None
    assert Audiencia.query.count() == 1


def test_realizada_atualiza_audiencia_agendada_mais_proxima(app, processo):
    agendada = Audiencia(processo_id=processo.id, data_hora=datetime(2026, 1, 5, 10, 0), status="agendada")
    db.session.add(agendada)
    db.session.commit()

    m = _mov(processo.id, "Audiência realizada", data=datetime(2026, 1, 5, 11, 0))
    resultado = mod.detectar_e_aplicar_audiencia(m)
    db.session.commit()
    assert resultado.id == agendada.id
    assert resultado.status == "realizada"
    assert resultado.deteccao_automatica is True
    assert resultado.movimentacao_id == m.id


def test_cancelada_atualiza_status(app, processo):
    agendada = Audiencia(processo_id=processo.id, data_hora=datetime(2026, 1, 5, 10, 0), status="agendada")
    db.session.add(agendada)
    db.session.commit()

    m = _mov(processo.id, "Audiência cancelada por ausência das partes")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado.status == "cancelada"


def test_nao_realizada_conta_como_cancelada(app, processo):
    """"Audiência não realizada" não deve ser confundida com "realizada"
    (checar "cancelada"/"não realizada" ANTES de "realizada" é o que evita
    isso — ver ordem em `_classificar`)."""
    agendada = Audiencia(processo_id=processo.id, data_hora=datetime(2026, 1, 5, 10, 0), status="agendada")
    db.session.add(agendada)
    db.session.commit()

    m = _mov(processo.id, "Audiência não realizada — parte ausente")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado.status == "cancelada"


def test_status_sem_audiencia_agendada_nao_faz_nada(app, processo):
    m = _mov(processo.id, "Audiência realizada")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado is None
    assert Audiencia.query.count() == 0


def test_redesignada_com_nova_data_atualiza_a_existente(app, processo):
    agendada = Audiencia(processo_id=processo.id, data_hora=datetime(2026, 1, 5, 10, 0), status="agendada")
    db.session.add(agendada)
    db.session.commit()

    m = _mov(processo.id, "Audiência redesignada para 20/02/2026 às 09:00")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    db.session.commit()
    assert resultado.id == agendada.id
    assert resultado.data_hora == datetime(2026, 2, 20, 9, 0)
    assert resultado.status == "agendada"
    assert "era 05/01/2026" in resultado.observacoes


def test_redesignada_sem_nova_data_marca_status_remarcada(app, processo):
    agendada = Audiencia(processo_id=processo.id, data_hora=datetime(2026, 1, 5, 10, 0), status="agendada")
    db.session.add(agendada)
    db.session.commit()

    m = _mov(processo.id, "Audiência remarcada de ofício")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado.id == agendada.id
    assert resultado.status == "remarcada"
    assert resultado.data_hora == datetime(2026, 1, 5, 10, 0)  # data não muda sem info nova


def test_redesignada_com_data_sem_audiencia_previa_cria_nova(app, processo):
    m = _mov(processo.id, "Audiência redesignada para 20/02/2026 às 09:00")
    resultado = mod.detectar_e_aplicar_audiencia(m)
    assert resultado is not None
    assert resultado.status == "agendada"
    assert resultado.data_hora == datetime(2026, 2, 20, 9, 0)


def test_pipeline_completo_cria_audiencia_a_partir_de_movimentacao_capturada(app, processo):
    """Integração: o hook em captura_pipeline.registrar_movimentacoes_capturadas
    dispara a detecção pra movimentações vindas de qualquer conector real
    (DataJud ou e-SAJ público), não só quando chamado diretamente."""
    capturada = MovimentacaoCapturada(
        data=datetime(2026, 5, 1, 9, 0), codigo_tpu=None,
        texto_integral="Audiência de instrução e julgamento designada para 10/06/2026 às 10:00",
        hash_dedup="hash-integracao-1",
    )
    registrar_movimentacoes_capturadas(processo, [capturada], origem_captura="esaj_publico")
    db.session.commit()

    audiencias = Audiencia.query.filter_by(processo_id=processo.id).all()
    assert len(audiencias) == 1
    assert audiencias[0].tipo == "instrução e julgamento"
    assert audiencias[0].deteccao_automatica is True
