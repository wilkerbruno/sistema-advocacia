"""
Testa a alçada de aprovação em múltiplos níveis para despesas
(PENDENCIAS.md, seção -50): desligada por padrão (sem impacto em quem
não configurar nada), nível 1 exige 1 aprovação, nível 2 exige 2
aprovações de usuários DISTINTOS, quem lançou a despesa nunca aprova a
própria, o mesmo aprovador nunca conta duas vezes, e o bloqueio real
acontece só na hora de marcar como PAGO — nunca no cadastro em si nem em
receita (só despesa).
"""
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Cliente, Lancamento, AprovacaoLancamento


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    empresa_id = empresa_basica["empresa_id"]

    admin_id = criar_usuario(unidade_id, "admin@alcada.com", papel="admin", nome="Admin")
    gestor_id = criar_usuario(unidade_id, "gestor@alcada.com", papel="gestor", nome="Gestor")
    gestor2_id = criar_usuario(unidade_id, "gestor2@alcada.com", papel="gestor", nome="Gestor Dois")
    advogado_id = criar_usuario(unidade_id, "adv@alcada.com", papel="advogado", nome="Advogado")

    cliente = Cliente(nome="Cliente Alçada", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.commit()

    from app.models import Empresa
    empresa = db.session.get(Empresa, empresa_id)

    return dict(admin_id=admin_id, gestor_id=gestor_id, gestor2_id=gestor2_id,
                advogado_id=advogado_id, unidade_id=unidade_id, empresa=empresa, cliente_id=cliente.id)


def _criar_despesa(unidade_id, valor, criado_por_id, descricao="Despesa teste"):
    lanc = Lancamento(descricao=descricao, tipo="despesa", natureza="despesa",
                       valor=Decimal(str(valor)), status="pendente",
                       unidade_id=unidade_id, criado_por_id=criado_por_id)
    db.session.add(lanc)
    db.session.commit()
    return lanc


def test_sem_alcada_configurada_marca_pago_direto(client, login, post_csrf, cenario):
    # empresa nunca configurou alcada_nivel1_valor (None) — comportamento
    # de sempre, nenhuma despesa precisa de aprovação, não importa o valor.
    login("admin@alcada.com")
    lanc = _criar_despesa(cenario["unidade_id"], "50000.00", cenario["admin_id"])

    r = post_csrf(f"/financeiro/{lanc.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert r.status_code == 200
    assert db.session.get(Lancamento, lanc.id).status == "pago"


def test_receita_nunca_precisa_de_aprovacao(client, login, cenario, post_csrf):
    empresa = cenario["empresa"]
    empresa.alcada_nivel1_valor = Decimal("100.00")
    db.session.commit()

    login("admin@alcada.com")
    receita = Lancamento(descricao="Honorário grande", tipo="honorario", natureza="receita",
                          valor=Decimal("999999.00"), status="pendente",
                          unidade_id=cenario["unidade_id"], criado_por_id=cenario["admin_id"])
    db.session.add(receita)
    db.session.commit()

    r = post_csrf(f"/financeiro/{receita.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert r.status_code == 200
    assert db.session.get(Lancamento, receita.id).status == "pago", \
        "receita nunca deveria ser bloqueada por alçada, só despesa"


def test_despesa_abaixo_do_nivel1_marca_pago_direto(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    db.session.commit()

    login("admin@alcada.com")
    lanc = _criar_despesa(cenario["unidade_id"], "500.00", cenario["admin_id"])

    r = post_csrf(f"/financeiro/{lanc.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert db.session.get(Lancamento, lanc.id).status == "pago"


def test_despesa_acima_do_nivel1_bloqueia_ate_uma_aprovacao(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    db.session.commit()

    login("admin@alcada.com")
    lanc = _criar_despesa(cenario["unidade_id"], "5000.00", cenario["admin_id"])

    # tenta marcar pago sem aprovação nenhuma -> bloqueado
    r = post_csrf(f"/financeiro/{lanc.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert r.status_code == 200
    assert db.session.get(Lancamento, lanc.id).status == "pendente", \
        "não deveria conseguir marcar como pago sem a aprovação de alçada"
    assert "aprovação" in r.data.decode("utf-8").lower()

    # gestor aprova (não é quem lançou) -> libera
    client.get("/logout")
    r_login_gestor = login("gestor@alcada.com")
    r_aprovar = post_csrf(f"/financeiro/{lanc.id}/aprovar", {"comentario": "ok"}, get_url="/financeiro/aprovacoes")
    assert r_aprovar.status_code == 200
    assert AprovacaoLancamento.query.filter_by(lancamento_id=lanc.id).count() == 1

    r_pago = post_csrf(f"/financeiro/{lanc.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert db.session.get(Lancamento, lanc.id).status == "pago"


def test_despesa_acima_do_nivel2_exige_duas_aprovacoes_de_usuarios_distintos(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    cenario["empresa"].alcada_nivel2_valor = Decimal("10000.00")
    db.session.commit()

    lanc = _criar_despesa(cenario["unidade_id"], "50000.00", cenario["admin_id"])

    login("gestor@alcada.com")
    post_csrf(f"/financeiro/{lanc.id}/aprovar", {}, get_url="/financeiro/aprovacoes")
    assert AprovacaoLancamento.query.filter_by(lancamento_id=lanc.id).count() == 1

    # com só 1 aprovação, ainda não pode marcar como pago
    client.get("/logout")
    login("admin@alcada.com")
    r = post_csrf(f"/financeiro/{lanc.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert db.session.get(Lancamento, lanc.id).status == "pendente"

    # segunda aprovação, de um usuário DIFERENTE
    client.get("/logout")
    login("gestor2@alcada.com")
    post_csrf(f"/financeiro/{lanc.id}/aprovar", {}, get_url="/financeiro/aprovacoes")
    assert AprovacaoLancamento.query.filter_by(lancamento_id=lanc.id).count() == 2

    client.get("/logout")
    login("admin@alcada.com")
    r_pago = post_csrf(f"/financeiro/{lanc.id}/status", {"status": "pago"}, get_url="/financeiro/novo")
    assert db.session.get(Lancamento, lanc.id).status == "pago"


def test_mesmo_usuario_nao_conta_duas_vezes(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    cenario["empresa"].alcada_nivel2_valor = Decimal("10000.00")
    db.session.commit()

    lanc = _criar_despesa(cenario["unidade_id"], "50000.00", cenario["admin_id"])

    login("gestor@alcada.com")
    post_csrf(f"/financeiro/{lanc.id}/aprovar", {}, get_url="/financeiro/aprovacoes")
    # tenta aprovar de novo, mesmo usuário
    r2 = post_csrf(f"/financeiro/{lanc.id}/aprovar", {}, get_url="/financeiro/aprovacoes")
    assert r2.status_code == 200
    assert AprovacaoLancamento.query.filter_by(lancamento_id=lanc.id).count() == 1, \
        "o mesmo usuário aprovando duas vezes não deveria contar como 2 aprovações distintas"
    assert "já aprovou" in r2.data.decode("utf-8").lower()


def test_quem_lancou_nao_pode_aprovar_a_propria_despesa(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    db.session.commit()

    # gestor lança a própria despesa
    login("gestor@alcada.com")
    lanc = _criar_despesa(cenario["unidade_id"], "5000.00", cenario["gestor_id"])

    r = post_csrf(f"/financeiro/{lanc.id}/aprovar", {}, get_url="/financeiro/aprovacoes")
    assert r.status_code == 200
    assert AprovacaoLancamento.query.filter_by(lancamento_id=lanc.id).count() == 0, \
        "quem lançou a despesa não deveria conseguir aprovar a própria alçada"
    assert "não pode aprovar" in r.data.decode("utf-8").lower()


def test_advogado_comum_nao_pode_aprovar(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    db.session.commit()

    lanc = _criar_despesa(cenario["unidade_id"], "5000.00", cenario["admin_id"])

    login("adv@alcada.com")
    r = post_csrf(f"/financeiro/{lanc.id}/aprovar", {}, get_url="/financeiro/aprovacoes")
    assert AprovacaoLancamento.query.filter_by(lancamento_id=lanc.id).count() == 0, \
        "advogado comum (sem papel de gestão) não deveria conseguir aprovar alçada"


def test_rejeitar_cancela_o_lancamento(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    db.session.commit()

    lanc = _criar_despesa(cenario["unidade_id"], "5000.00", cenario["admin_id"])

    login("gestor@alcada.com")
    r = post_csrf(f"/financeiro/{lanc.id}/rejeitar-alcada", {"motivo": "fora do orçamento do mês"},
                   get_url="/financeiro/aprovacoes")
    assert r.status_code == 200
    assert db.session.get(Lancamento, lanc.id).status == "cancelado"


def test_configurar_alcada_via_admin(client, login, post_csrf, cenario):
    login("admin@alcada.com")
    r = post_csrf("/admin/alcada-aprovacao", {
        "alcada_nivel1_valor": "1500,00", "alcada_nivel2_valor": "15000,00",
    }, get_url="/admin/alcada-aprovacao")
    assert r.status_code == 200

    from app.models import Empresa
    empresa = db.session.get(Empresa, cenario["empresa"].id)
    assert empresa.alcada_nivel1_valor == Decimal("1500.00")
    assert empresa.alcada_nivel2_valor == Decimal("15000.00")


def test_configurar_alcada_nivel2_sem_nivel1_e_rejeitado(client, login, post_csrf, cenario):
    login("admin@alcada.com")
    r = post_csrf("/admin/alcada-aprovacao", {
        "alcada_nivel2_valor": "15000,00",
    }, get_url="/admin/alcada-aprovacao")
    assert r.status_code == 200

    from app.models import Empresa
    empresa = db.session.get(Empresa, cenario["empresa"].id)
    assert empresa.alcada_nivel1_valor is None
    assert empresa.alcada_nivel2_valor is None, \
        "nível 2 sem nível 1 preenchido não deveria ser salvo"


def test_criar_despesa_acima_da_alcada_avisa_no_cadastro(client, login, post_csrf, cenario):
    cenario["empresa"].alcada_nivel1_valor = Decimal("1000.00")
    db.session.commit()

    login("admin@alcada.com")
    r = post_csrf("/financeiro/novo", {
        "descricao": "Despesa grande", "valor": "5000.00", "natureza": "despesa", "tipo": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200
    assert "alçada" in r.data.decode("utf-8").lower()

    lanc = Lancamento.query.filter_by(descricao="Despesa grande").first()
    assert lanc is not None
    assert lanc.status == "pendente"  # cadastro em si nunca é bloqueado, só o "marcar pago"
