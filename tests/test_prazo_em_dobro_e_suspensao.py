"""
Item 6 da lista de pipeline de IA jurídica (PENDENCIAS.md, seção -106):
conclusão de "Prazo e data de segurança" — prerrogativa de prazo em dobro
(`Processo.prazo_em_dobro`, CPC arts. 180/183/229) e suspensão de prazo
(`Processo.suspenso_desde`, empurrando os prazos em aberto quando o
processo volta a tramitar). Ver app/utils/prazos_engine.py
(`_montar_prazo`, `empurrar_prazos_por_suspensao`) e
app/routes/processos.py::editar (hook único de mudança de status).
"""
import re
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Movimentacao, RegraProximaAcao, Prazo
from app.utils.prazos_engine import aplicar_regra_proxima_acao, empurrar_prazos_por_suspensao


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "dobro@teste.com", papel="advogado", nome="Advogado Dobro")
    cliente = Cliente(nome="Cliente Dobro", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-DOBRO-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id)
    db.session.add(processo)
    db.session.commit()
    return dict(usuario_id=usuario_id, unidade_id=unidade_id, processo_id=processo.id,
                cliente_id=cliente.id, usuario_email="dobro@teste.com")


# ---------- prazo_em_dobro via motor de próxima ação ----------

def test_prazo_com_regra_sai_em_dobro_quando_processo_tem_prerrogativa(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        processo.prazo_em_dobro = True
        db.session.commit()

        regra = RegraProximaAcao(ato_capturado="Citação", codigo_tpu="123", acao_exigida="Contestar",
                                  prazo_base_dias=15, unidade_prazo="dias_uteis", ativo=True)
        db.session.add(regra)
        db.session.flush()
        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1), codigo_tpu="123",
                            texto_integral="Citação do réu")
        db.session.add(mov)
        db.session.flush()

        prazo_dobro = aplicar_regra_proxima_acao(mov, permitir_generico=True)

        # Processo idêntico, sem a prerrogativa, pro comparativo.
        cliente2 = Cliente(nome="Cliente Sem Dobro", unidade_id=cenario["unidade_id"])
        db.session.add(cliente2)
        db.session.flush()
        processo_normal = Processo(numero_interno="P-NORMAL-1", cliente_id=cliente2.id,
                                    unidade_id=cenario["unidade_id"], area_direito="Cível",
                                    responsavel_id=cenario["usuario_id"])
        db.session.add(processo_normal)
        db.session.flush()
        mov2 = Movimentacao(processo_id=processo_normal.id, data=datetime(2026, 9, 1), codigo_tpu="123",
                             texto_integral="Citação do réu")
        db.session.add(mov2)
        db.session.flush()
        prazo_normal = aplicar_regra_proxima_acao(mov2, permitir_generico=True)

        dias_normal = (prazo_normal.data_vencimento - date(2026, 9, 1)).days
        dias_dobro = (prazo_dobro.data_vencimento - date(2026, 9, 1)).days
        # Em dias úteis o dobro exato de dias corridos pode variar por causa de
        # fins de semana/feriados no meio — o que importa é que o prazo em
        # dobro vence bem depois (pelo menos ~1.5x mais tarde em dias corridos).
        assert prazo_dobro.data_vencimento > prazo_normal.data_vencimento
        assert dias_dobro >= dias_normal * 1.5


def test_prazo_generico_sem_regra_tambem_dobra_com_prerrogativa(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        processo.prazo_em_dobro = True
        db.session.commit()

        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1),
                            texto_integral="Ato qualquer sem regra cadastrada")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        assert prazo.regra_aplicada_id is None
        assert prazo.data_vencimento == date(2026, 9, 1) + timedelta(days=10)


def test_prazo_generico_sem_prerrogativa_usa_cinco_dias(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        assert not processo.prazo_em_dobro

        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1),
                            texto_integral="Ato qualquer sem regra cadastrada")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        assert prazo.data_vencimento == date(2026, 9, 1) + timedelta(days=5)


# ---------- empurrar_prazos_por_suspensao (função pura) ----------

def test_empurrar_prazos_por_suspensao_empurra_apenas_prazos_em_aberto(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        venc_pendente = date(2026, 9, 10)
        venc_cumprido = date(2026, 9, 5)
        p_pendente = Prazo(processo_id=processo.id, descricao="Pendente", data_vencimento=venc_pendente,
                            data_seguranca=venc_pendente - timedelta(days=2), status="pendente")
        p_cumprido = Prazo(processo_id=processo.id, descricao="Cumprido", data_vencimento=venc_cumprido,
                            status="cumprido")
        p_perdido = Prazo(processo_id=processo.id, descricao="Perdido", data_vencimento=venc_cumprido,
                           status="perdido")
        p_historico = Prazo(processo_id=processo.id, descricao="Histórico", data_vencimento=venc_cumprido,
                             status="historico_anterior")
        db.session.add_all([p_pendente, p_cumprido, p_perdido, p_historico])
        db.session.commit()

        afetados = empurrar_prazos_por_suspensao(processo, 7)
        db.session.commit()

        assert len(afetados) == 1
        assert p_pendente.data_vencimento == venc_pendente + timedelta(days=7)
        assert p_pendente.data_seguranca == (venc_pendente - timedelta(days=2)) + timedelta(days=7)
        assert "empurrado em 7 dia(s)" in p_pendente.observacoes
        assert p_cumprido.data_vencimento == venc_cumprido
        assert p_perdido.data_vencimento == venc_cumprido
        assert p_historico.data_vencimento == venc_cumprido


def test_empurrar_prazos_por_suspensao_com_zero_dias_nao_faz_nada(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        p = Prazo(processo_id=processo.id, descricao="Pendente", data_vencimento=date(2026, 9, 10),
                  status="pendente")
        db.session.add(p)
        db.session.commit()

        assert empurrar_prazos_por_suspensao(processo, 0) == []
        assert empurrar_prazos_por_suspensao(processo, -3) == []
        assert p.data_vencimento == date(2026, 9, 10)
        assert p.observacoes is None


# ---------- hook de transição via rota editar() ----------

def _csrf_do_form(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def _dados_basicos_edicao(cenario, token, status):
    return {
        "csrf_token": token,
        "area_direito": "Cível",
        "status": status,
        "cliente_id": str(cenario["cliente_id"]),
    }


def test_suspender_processo_grava_suspenso_desde(client, login, cenario):
    login(cenario["usuario_email"])
    html = client.get(f"/processos/{cenario['processo_id']}/editar").data.decode("utf-8")
    token = _csrf_do_form(html)

    resp = client.post(f"/processos/{cenario['processo_id']}/editar",
                        data=_dados_basicos_edicao(cenario, token, "suspenso"), follow_redirects=True)
    assert resp.status_code == 200

    processo = db.session.get(Processo, cenario["processo_id"])
    assert processo.status == "suspenso"
    assert processo.suspenso_desde is not None


def test_reativar_processo_empurra_prazos_em_aberto(client, login, cenario):
    login(cenario["usuario_email"])
    processo = db.session.get(Processo, cenario["processo_id"])
    venc = date(2026, 9, 10)
    prazo = Prazo(processo_id=processo.id, descricao="Prazo em aberto", data_vencimento=venc,
                  status="pendente")
    db.session.add(prazo)
    processo.status = "suspenso"
    processo.suspenso_desde = datetime.utcnow() - timedelta(days=4)
    db.session.commit()
    prazo_id = prazo.id

    html = client.get(f"/processos/{cenario['processo_id']}/editar").data.decode("utf-8")
    token = _csrf_do_form(html)
    resp = client.post(f"/processos/{cenario['processo_id']}/editar",
                        data=_dados_basicos_edicao(cenario, token, "ativo"), follow_redirects=True)
    assert resp.status_code == 200

    processo = db.session.get(Processo, cenario["processo_id"])
    assert processo.status == "ativo"
    assert processo.suspenso_desde is None

    prazo_atualizado = db.session.get(Prazo, prazo_id)
    assert prazo_atualizado.data_vencimento == venc + timedelta(days=4)
    assert "empurrado" in (prazo_atualizado.observacoes or "")


def test_reativar_processo_sem_suspenso_desde_nao_quebra(client, login, cenario):
    # Dado legado: processo suspenso antes da coluna existir (suspenso_desde
    # nulo mesmo estando suspenso). Reativar não deve derrubar nada nem
    # inventar quantos dias durou a suspensão.
    login(cenario["usuario_email"])
    processo = db.session.get(Processo, cenario["processo_id"])
    processo.status = "suspenso"
    processo.suspenso_desde = None
    db.session.commit()

    html = client.get(f"/processos/{cenario['processo_id']}/editar").data.decode("utf-8")
    token = _csrf_do_form(html)
    resp = client.post(f"/processos/{cenario['processo_id']}/editar",
                        data=_dados_basicos_edicao(cenario, token, "ativo"), follow_redirects=True)
    assert resp.status_code == 200

    processo = db.session.get(Processo, cenario["processo_id"])
    assert processo.status == "ativo"
    assert processo.suspenso_desde is None


def test_suspender_e_reativar_no_mesmo_dia_nao_empurra_nem_avisa(client, login, cenario):
    login(cenario["usuario_email"])
    processo = db.session.get(Processo, cenario["processo_id"])
    venc = date(2026, 9, 10)
    prazo = Prazo(processo_id=processo.id, descricao="Prazo em aberto", data_vencimento=venc,
                  status="pendente")
    db.session.add(prazo)
    processo.status = "suspenso"
    processo.suspenso_desde = datetime.utcnow()
    db.session.commit()
    prazo_id = prazo.id

    html = client.get(f"/processos/{cenario['processo_id']}/editar").data.decode("utf-8")
    token = _csrf_do_form(html)
    resp = client.post(f"/processos/{cenario['processo_id']}/editar",
                        data=_dados_basicos_edicao(cenario, token, "ativo"), follow_redirects=True)
    assert resp.status_code == 200

    prazo_atualizado = db.session.get(Prazo, prazo_id)
    assert prazo_atualizado.data_vencimento == venc
    assert prazo_atualizado.observacoes is None


def test_marcar_prazo_em_dobro_via_formulario_de_edicao(client, login, cenario):
    login(cenario["usuario_email"])
    html = client.get(f"/processos/{cenario['processo_id']}/editar").data.decode("utf-8")
    token = _csrf_do_form(html)

    dados = _dados_basicos_edicao(cenario, token, "ativo")
    dados["prazo_em_dobro"] = "on"
    resp = client.post(f"/processos/{cenario['processo_id']}/editar", data=dados, follow_redirects=True)
    assert resp.status_code == 200

    processo = db.session.get(Processo, cenario["processo_id"])
    assert processo.prazo_em_dobro is True

    # Desmarcando de novo (form não manda o campo quando checkbox está
    # desmarcado) deve voltar a False, não ficar "grudado" em True.
    html = client.get(f"/processos/{cenario['processo_id']}/editar").data.decode("utf-8")
    token = _csrf_do_form(html)
    resp = client.post(f"/processos/{cenario['processo_id']}/editar",
                        data=_dados_basicos_edicao(cenario, token, "ativo"), follow_redirects=True)
    assert resp.status_code == 200
    processo = db.session.get(Processo, cenario["processo_id"])
    assert processo.prazo_em_dobro is False
