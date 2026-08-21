"""
Cobre um conjunto de telas que ganharam proteção CSRF/campos novos numa
rodada anterior (PENDENCIAS.md, seções -38/-39 e afins): confirma que o
token está realmente presente e é exigido (400 sem ele), e testa o fluxo
de "gerar cobrança por horas" do timesheet — sugestão de valor a partir do
valor_hora_padrao do cliente, vínculo apontamento->lançamento pra nunca
faturar duas vezes, e o caso de cliente sem valor_hora_padrao configurado.
Também cobre a separação conta_terceiros (operacional x terceiros) e um
caso de dado "antigo" com conta_terceiros=NULL (simulando um ALTER TABLE
em produção sem backfill).
"""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Usuario, Cliente, Processo, Apontamento, Lancamento


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@teste.com", papel="admin", nome="Admin Teste")

    cliente_com_valor = Cliente(nome="Cliente Com Valor/Hora", unidade_id=unidade_id, valor_hora_padrao=Decimal("250.00"))
    cliente_sem_valor = Cliente(nome="Cliente Sem Valor/Hora", unidade_id=unidade_id)
    db.session.add_all([cliente_com_valor, cliente_sem_valor])
    db.session.flush()

    processo = Processo(numero_processo="0000099-11.2026.8.26.0100", cliente_id=cliente_com_valor.id,
                         unidade_id=unidade_id, area_direito="Cível",
                         responsavel_id=admin_id, criado_por_id=admin_id)
    db.session.add(processo)
    db.session.flush()

    apontamento1 = Apontamento(usuario_id=admin_id, unidade_id=unidade_id, processo_id=processo.id,
                                data=date.today(), horas=Decimal("2.0"), descricao="Peticao inicial", faturavel=True)
    apontamento2 = Apontamento(usuario_id=admin_id, unidade_id=unidade_id, processo_id=processo.id,
                                data=date.today(), horas=Decimal("1.5"), descricao="Audiencia", faturavel=True)
    apontamento_nao_faturavel = Apontamento(usuario_id=admin_id, unidade_id=unidade_id, processo_id=processo.id,
                                             data=date.today(), horas=Decimal("0.5"), descricao="Interno", faturavel=False)
    db.session.add_all([apontamento1, apontamento2, apontamento_nao_faturavel])
    db.session.commit()

    return dict(admin_id=admin_id, unidade_id=unidade_id, cliente_id=cliente_com_valor.id,
                cliente_sem_valor_id=cliente_sem_valor.id, processo_id=processo.id,
                apontamento1_id=apontamento1.id, apontamento2_id=apontamento2.id)


def test_financeiro_novo_csrf_e_conta_terceiros(client, login, post_csrf, cenario):
    login("admin@teste.com")
    r = client.get("/financeiro/novo")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'name="conta_terceiros"' in html, "checkbox conta_terceiros ausente no form"

    # sem csrf_token deve ser rejeitado (confirma que a proteção está realmente ativa aqui)
    r_sem = client.post("/financeiro/novo", data={"descricao": "Teste sem token", "valor": "100.00"})
    assert r_sem.status_code == 400, f"esperava 400 sem csrf_token, veio {r_sem.status_code}"

    # com csrf_token + conta_terceiros marcado
    r_ok = post_csrf("/financeiro/novo", {
        "descricao": "Deposito judicial", "valor": "500.00", "natureza": "receita",
        "conta_terceiros": "1", "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    assert r_ok.status_code == 200

    lanc_terceiros = Lancamento.query.filter_by(descricao="Deposito judicial").first()
    assert lanc_terceiros is not None
    assert lanc_terceiros.conta_terceiros is True, "conta_terceiros deveria ser True"

    # lançamento normal (sem marcar) deve ficar conta_terceiros=False
    r_normal = post_csrf("/financeiro/novo", {
        "descricao": "Honorario normal", "valor": "300.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    assert r_normal.status_code == 200
    lanc_normal = Lancamento.query.filter_by(descricao="Honorario normal").first()
    assert lanc_normal.conta_terceiros is False

    # /financeiro/?conta=terceiros mostra só o de terceiros, /financeiro/ operacional não mistura
    r_listar_op = client.get("/financeiro/")
    assert "Deposito judicial" not in r_listar_op.data.decode("utf-8"), \
        "lançamento de terceiros vazou pro caixa operacional"
    assert "Honorario normal" in r_listar_op.data.decode("utf-8")
    r_listar_terc = client.get("/financeiro/?conta=terceiros")
    assert "Deposito judicial" in r_listar_terc.data.decode("utf-8")
    assert "Honorario normal" not in r_listar_terc.data.decode("utf-8")


def test_clientes_inativar_csrf(client, login, cenario):
    login("admin@teste.com")
    r_det = client.get(f"/clientes/{cenario['cliente_id']}")
    assert r_det.status_code == 200
    html_det = r_det.data.decode("utf-8")
    assert f"/clientes/{cenario['cliente_id']}/inativar" in html_det

    r_sem = client.post(f"/clientes/{cenario['cliente_id']}/inativar", data={})
    assert r_sem.status_code == 400, f"esperava 400, veio {r_sem.status_code}"

    import re
    tok = re.search(r'name="csrf_token" value="([^"]+)"', html_det).group(1)
    r_ok = client.post(f"/clientes/{cenario['cliente_id']}/inativar", data={"csrf_token": tok}, follow_redirects=False)
    assert r_ok.status_code == 302, f"esperava 302, veio {r_ok.status_code}"

    c_check = db.session.get(Cliente, cenario["cliente_id"])
    assert c_check.ativo is False, "cliente deveria estar inativo após o POST"


def test_importar_lote_tem_csrf(client, login, cenario):
    login("admin@teste.com")
    r = client.get("/governanca/processos/importar-lote")
    assert r.status_code == 200
    assert "csrf_token" in r.data.decode("utf-8")


def test_cliente_form_com_valor_hora_padrao(client, login, post_csrf, cenario):
    login("admin@teste.com")
    r_novo = client.get("/clientes/novo")
    assert 'name="valor_hora_padrao"' in r_novo.data.decode("utf-8")

    r_criar = post_csrf("/clientes/novo", {
        "nome": "Cliente Novo Via Form", "tipo_pessoa": "PF",
        "valor_hora_padrao": "180,50", "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/clientes/novo")
    assert r_criar.status_code == 200

    novo_cli = Cliente.query.filter_by(nome="Cliente Novo Via Form").first()
    assert novo_cli.valor_hora_padrao == Decimal("180.50")


def test_gerar_cobranca_horas_sugere_valor_e_vincula(client, login, post_csrf, cenario):
    login("admin@teste.com")
    r_get = client.get(f"/financeiro/gerar-cobranca-horas?processo_id={cenario['processo_id']}")
    assert r_get.status_code == 200
    html_gerar = r_get.data.decode("utf-8")
    assert "Peticao inicial" in html_gerar and "Audiencia" in html_gerar
    assert "Interno" not in html_gerar, "apontamento não faturável não deveria aparecer"
    assert "875" in html_gerar or "875,00" in html_gerar or "875.00" in html_gerar, \
        "sugestão de valor (3.5h x 250 = 875) não encontrada no HTML"

    r_post = post_csrf("/financeiro/gerar-cobranca-horas", {
        "processo_id": str(cenario["processo_id"]),
        "apontamento_ids": [str(cenario["apontamento1_id"]), str(cenario["apontamento2_id"])],
        "valor": "875.00", "descricao": "Cobranca de horas teste",
    }, get_url=f"/financeiro/gerar-cobranca-horas?processo_id={cenario['processo_id']}")
    assert r_post.status_code == 200

    a1 = db.session.get(Apontamento, cenario["apontamento1_id"])
    a2 = db.session.get(Apontamento, cenario["apontamento2_id"])
    assert a1.lancamento_id is not None and a2.lancamento_id is not None
    assert a1.lancamento_id == a2.lancamento_id
    lanc = db.session.get(Lancamento, a1.lancamento_id)
    assert lanc.valor == Decimal("875.00")
    assert lanc.cliente_id == cenario["cliente_id"]

    # reabrindo a tela pro mesmo processo, os apontamentos já faturados NÃO aparecem mais
    r_get2 = client.get(f"/financeiro/gerar-cobranca-horas?processo_id={cenario['processo_id']}")
    html_gerar2 = r_get2.data.decode("utf-8")
    assert "Peticao inicial" not in html_gerar2 and "Audiencia" not in html_gerar2
    assert "Nenhum apontamento pendente" in html_gerar2

    # timesheet/listar.html mostra status "faturado" para os que já foram cobrados
    r_ts = client.get("/timesheet/")
    assert r_ts.status_code == 200
    assert "faturado" in r_ts.data.decode("utf-8").lower()


def test_cliente_sem_valor_hora_padrao_nao_trava_tela(client, login, cenario):
    login("admin@teste.com")
    admin_db = Usuario.query.filter_by(email="admin@teste.com").first()
    processo2 = Processo(numero_processo="0000098-11.2026.8.26.0100", cliente_id=cenario["cliente_sem_valor_id"],
                          unidade_id=cenario["unidade_id"], area_direito="Cível",
                          responsavel_id=admin_db.id, criado_por_id=admin_db.id)
    db.session.add(processo2)
    db.session.flush()
    apontamento3 = Apontamento(usuario_id=admin_db.id, unidade_id=cenario["unidade_id"], processo_id=processo2.id,
                                data=date.today(), horas=Decimal("1.0"), descricao="Sem valor padrao", faturavel=True)
    db.session.add(apontamento3)
    db.session.commit()

    r = client.get(f"/financeiro/gerar-cobranca-horas?processo_id={processo2.id}")
    assert r.status_code == 200
    assert "informe o valor manualmente" in r.data.decode("utf-8").lower()


def test_lancamento_antigo_com_conta_terceiros_null_continua_operacional(client, login, cenario):
    login("admin@teste.com")
    lanc_antigo = Lancamento(descricao="Lancamento antigo pre-migracao", tipo="honorario", natureza="receita",
                              valor=Decimal("50.00"), status="pendente", unidade_id=cenario["unidade_id"],
                              conta_terceiros=None)
    db.session.add(lanc_antigo)
    db.session.commit()

    r_listar_op = client.get("/financeiro/")
    html_op = r_listar_op.data.decode("utf-8")
    assert "Lancamento antigo pre-migracao" in html_op, \
        "lançamento antigo com conta_terceiros NULL deveria aparecer no operacional"
    r_listar_terc = client.get("/financeiro/?conta=terceiros")
    assert "Lancamento antigo pre-migracao" not in r_listar_terc.data.decode("utf-8")
