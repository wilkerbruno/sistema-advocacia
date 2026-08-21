"""
Testa o RBAC de acesso financeiro (PENDENCIAS.md, seção -45): admin e
gestor sempre têm acesso; qualquer outro papel só com
`acesso_financeiro=True` concedido explicitamente. Cobre tanto a aba
Financeiro em si quanto a persona "Negócios" do Agente de IA (mesmo
vazamento, achado durante aquela rodada — ver seção -45 pro histórico
completo).
"""
from datetime import date

import pytest

from app.extensions import db
from app.models import Cliente, Lancamento, ConversaAgenteIA


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]

    admin_id = criar_usuario(unidade_id, "admin@rbac.com", papel="admin")
    gestor_id = criar_usuario(unidade_id, "gestor@rbac.com", papel="gestor")
    advogado_id = criar_usuario(unidade_id, "adv@rbac.com", papel="advogado")
    socio_id = criar_usuario(unidade_id, "socio@rbac.com", papel="advogado", acesso_financeiro=True)
    funcionario_id = criar_usuario(unidade_id, "estag@rbac.com", papel="funcionario")

    cliente = Cliente(nome="Cliente RBAC", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    lancamento = Lancamento(descricao="Honorários", natureza="receita", status="pendente",
                             valor=1000, data_vencimento=date.today(), unidade_id=unidade_id,
                             cliente_id=cliente.id)
    db.session.add(lancamento)
    db.session.flush()

    conv_socio = ConversaAgenteIA(usuario_id=socio_id, unidade_id=unidade_id, persona="negocios")
    conv_advogado = ConversaAgenteIA(usuario_id=advogado_id, unidade_id=unidade_id, persona="negocios")
    db.session.add_all([conv_socio, conv_advogado])
    db.session.commit()

    return dict(admin_id=admin_id, gestor_id=gestor_id, advogado_id=advogado_id, socio_id=socio_id,
                funcionario_id=funcionario_id, lancamento_id=lancamento.id,
                conv_socio_id=conv_socio.id, conv_advogado_id=conv_advogado.id)


def test_admin_acessa_financeiro(client, login, cenario):
    login("admin@rbac.com")
    r = client.get("/financeiro/")
    assert r.status_code == 200
    r = client.get("/")
    assert b"Financeiro" in r.data, "admin deveria ver o link Financeiro no menu"


def test_gestor_acessa_financeiro(client, login, cenario):
    login("gestor@rbac.com")
    r = client.get("/financeiro/")
    assert r.status_code == 200


def test_advogado_comum_bloqueado_em_todo_o_blueprint_financeiro(client, login, cenario):
    login("adv@rbac.com")
    r = client.get("/financeiro/")
    assert r.status_code == 403
    r = client.get("/")
    assert b"Financeiro" not in r.data, "advogado sem concessão não deveria ver o link Financeiro"
    assert client.get("/financeiro/novo").status_code == 403
    assert client.get(f"/financeiro/{cenario['lancamento_id']}/recibo").status_code == 403


def test_funcionario_bloqueado(client, login, cenario):
    login("estag@rbac.com")
    assert client.get("/financeiro/").status_code == 403


def test_socio_com_concessao_acessa_financeiro(client, login, cenario):
    login("socio@rbac.com")
    r = client.get("/financeiro/")
    assert r.status_code == 200
    r = client.get("/")
    assert b"Financeiro" in r.data


def test_advogado_comum_nao_cria_conversa_negocios(client, login, post_csrf, cenario):
    login("adv@rbac.com")
    r = post_csrf("/agente-ia/nova", {"persona": "negocios"}, get_url="/agente-ia/")
    assert r.status_code == 403


def test_cartao_negocios_nao_aparece_pro_advogado_comum(client, login, cenario):
    login("adv@rbac.com")
    r = client.get("/agente-ia/")
    assert "sócios" not in r.data.decode("utf-8")


def test_socio_abre_a_propria_conversa_negocios(client, login, cenario):
    login("socio@rbac.com")
    r = client.get(f"/agente-ia/{cenario['conv_socio_id']}")
    assert r.status_code == 200


def test_advogado_comum_nao_reabre_conversa_negocios_antiga(client, login, post_csrf, cenario):
    login("adv@rbac.com")
    r = client.get(f"/agente-ia/{cenario['conv_advogado_id']}")
    assert r.status_code == 403, "acesso revogado depois de criada a conversa deve bloquear reabertura"
    r = post_csrf(f"/agente-ia/{cenario['conv_advogado_id']}/mensagem",
                   {"mensagem": "qual a receita pendente?"}, get_url="/agente-ia/")
    assert r.status_code == 403
