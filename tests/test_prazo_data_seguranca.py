"""
Item 6 da lista de pipeline de IA jurídica (PENDENCIAS.md, seção -103):
"Prazo e data de segurança" — `Prazo.data_seguranca`, calculada
automaticamente (dias úteis antes da data fatal) tanto pelo motor de
próxima ação (regra encontrada ou o prazo genérico "análise necessária")
quanto no cadastro manual de prazo (app/routes/processos.py::add_prazo).
"""
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Movimentacao, RegraProximaAcao, Prazo
from app.utils.prazos_engine import (
    calcular_data_seguranca, DIAS_SEGURANCA_PADRAO, aplicar_regra_proxima_acao,
)


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "seguranca@teste.com", papel="advogado", nome="Advogado Segurança")
    cliente = Cliente(nome="Cliente Segurança", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-SEG-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id)
    db.session.add(processo)
    db.session.commit()
    return dict(usuario_id=usuario_id, unidade_id=unidade_id, processo_id=processo.id,
                usuario_email="seguranca@teste.com")


# ---------- calcular_data_seguranca ----------
# Assim como `calcular_data_fatal`, esta função consulta a tabela `Feriado`
# (via `eh_dia_util`) para saber quais dias são úteis, então precisa de um
# contexto de aplicação/banco mesmo nestes testes "unitários".

def test_calcular_data_seguranca_conta_dias_uteis_para_tras(app):
    with app.app_context():
        # sexta-feira 2026-09-18 vence; 2 dias úteis antes = quarta 2026-09-16
        venc = date(2026, 9, 18)
        seg = calcular_data_seguranca(venc, dias_seguranca=2)
        assert seg == date(2026, 9, 16)


def test_calcular_data_seguranca_pula_fim_de_semana(app):
    with app.app_context():
        # segunda-feira 2026-09-21 vence; 2 dias úteis antes deveria pular o
        # fim de semana anterior (sábado 19, domingo 20) e cair na quinta 17.
        venc = date(2026, 9, 21)
        seg = calcular_data_seguranca(venc, dias_seguranca=2)
        assert seg == date(2026, 9, 17)
        assert seg.weekday() < 5


def test_calcular_data_seguranca_usa_padrao_quando_nao_informado(app):
    with app.app_context():
        venc = date(2026, 9, 18)
        seg_padrao = calcular_data_seguranca(venc)
        seg_explicito = calcular_data_seguranca(venc, dias_seguranca=DIAS_SEGURANCA_PADRAO)
        assert seg_padrao == seg_explicito


def test_calcular_data_seguranca_nunca_fica_antes_da_data_minima(app):
    with app.app_context():
        venc = date(2026, 9, 16)
        data_inicial = date(2026, 9, 15)
        seg = calcular_data_seguranca(venc, dias_seguranca=10, data_minima=data_inicial)
        assert seg == data_inicial


def test_calcular_data_seguranca_com_zero_dias_devolve_a_propria_data_vencimento(app):
    with app.app_context():
        venc = date(2026, 9, 18)
        assert calcular_data_seguranca(venc, dias_seguranca=0) == venc


def test_calcular_data_seguranca_com_vencimento_none():
    # Único caso que não toca o banco: retorna None imediatamente.
    assert calcular_data_seguranca(None) is None


# ---------- motor de próxima ação (aplicar_regra_proxima_acao) ----------

def test_prazo_gerado_por_regra_tem_data_seguranca(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        regra = RegraProximaAcao(ato_capturado="Citação", codigo_tpu="123", acao_exigida="Contestar",
                                  prazo_base_dias=15, unidade_prazo="dias_uteis", ativo=True)
        db.session.add(regra)
        db.session.flush()
        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1), codigo_tpu="123",
                            texto_integral="Citação do réu")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        assert prazo.data_seguranca is not None
        assert prazo.data_seguranca < prazo.data_vencimento
        assert prazo.data_seguranca == calcular_data_seguranca(prazo.data_vencimento, tribunal=processo.tribunal,
                                                                 data_minima=prazo.data_inicial)


def test_prazo_gerado_por_regra_respeita_dias_seguranca_customizado(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        regra = RegraProximaAcao(ato_capturado="Intimação urgente", codigo_tpu="999",
                                  acao_exigida="Agir rápido", prazo_base_dias=10,
                                  unidade_prazo="dias_uteis", dias_seguranca=4, ativo=True)
        db.session.add(regra)
        db.session.flush()
        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1), codigo_tpu="999",
                            texto_integral="Intimação urgente")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        esperado = calcular_data_seguranca(prazo.data_vencimento, dias_seguranca=4,
                                            tribunal=processo.tribunal, data_minima=prazo.data_inicial)
        assert prazo.data_seguranca == esperado


def test_prazo_generico_sem_regra_tambem_tem_data_seguranca(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1),
                            texto_integral="Ato qualquer sem regra cadastrada")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        assert prazo.regra_aplicada_id is None
        assert prazo.data_seguranca is not None
        assert prazo.data_seguranca <= prazo.data_vencimento


# ---------- cadastro manual (rota add_prazo) ----------

def test_add_prazo_manual_calcula_data_seguranca_automaticamente(client, login, cenario):
    login(cenario["usuario_email"])
    r = client.get(f"/processos/{cenario['processo_id']}")
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode("utf-8")).group(1)

    resp = client.post(f"/processos/{cenario['processo_id']}/prazos", data={
        "descricao": "Prazo manual de teste", "data_vencimento": "2026-09-18",
        "prioridade": "normal", "csrf_token": token,
    }, follow_redirects=True)
    assert resp.status_code == 200

    prazo = Prazo.query.filter_by(processo_id=cenario["processo_id"], descricao="Prazo manual de teste").first()
    assert prazo is not None
    assert prazo.data_seguranca is not None
    assert prazo.data_seguranca < prazo.data_vencimento


def test_add_prazo_manual_aceita_data_seguranca_customizada(client, login, cenario):
    login(cenario["usuario_email"])
    r = client.get(f"/processos/{cenario['processo_id']}")
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode("utf-8")).group(1)

    resp = client.post(f"/processos/{cenario['processo_id']}/prazos", data={
        "descricao": "Prazo com segurança customizada", "data_vencimento": "2026-09-18",
        "data_seguranca": "2026-09-10", "prioridade": "alta", "csrf_token": token,
    }, follow_redirects=True)
    assert resp.status_code == 200

    prazo = Prazo.query.filter_by(processo_id=cenario["processo_id"],
                                   descricao="Prazo com segurança customizada").first()
    assert prazo.data_seguranca == date(2026, 9, 10)
