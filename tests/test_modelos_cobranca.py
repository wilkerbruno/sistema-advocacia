"""
Testa os modelos de cobrança do financeiro: honorário de êxito (percentual
+ valor-base), fixo (ignora campos de êxito mesmo se vierem preenchidos),
retainer (duplicação pro mês seguinte com vencimento +1 mês) e a emissão
de recibo em PDF (só pra lançamento já pago).
"""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Lancamento


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@teste.com", papel="admin", nome="Admin Teste")

    cliente = Cliente(nome="Cliente Exito", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(numero_processo="0000077-11.2026.8.26.0100", cliente_id=cliente.id,
                         unidade_id=unidade_id, area_direito="Cível", valor_causa=Decimal("100000.00"),
                         responsavel_id=admin_id, criado_por_id=admin_id)
    db.session.add(processo)
    db.session.commit()

    return dict(admin_id=admin_id, unidade_id=unidade_id, cliente_id=cliente.id, processo_id=processo.id)


def test_formulario_mostra_valor_causa_para_js(client, login, cenario):
    login("admin@teste.com")
    r_form = client.get("/financeiro/novo")
    assert r_form.status_code == 200
    html_form = r_form.data.decode("utf-8")
    pid = cenario["processo_id"]
    assert f'"{pid}": "100000.00"' in html_form or f'"{pid}": "100000.0"' in html_form, \
        "valor_causa do processo não apareceu no JSON pro JS"
    assert 'id="modelo_cobranca"' in html_form and 'id="bloco-exito"' in html_form


def test_lancamento_modelo_exito_salva_percentual_e_valor_base(client, login, post_csrf, cenario):
    login("admin@teste.com")
    r = post_csrf("/financeiro/novo", {
        "descricao": "Honorario de exito - acordo", "valor": "20000.00", "natureza": "receita",
        "modelo_cobranca": "exito", "percentual_exito": "20", "valor_base_exito": "100000.00",
        "unidade_id": str(cenario["unidade_id"]), "processo_id": str(cenario["processo_id"]),
        "cliente_id": str(cenario["cliente_id"]),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Honorario de exito - acordo").first()
    assert lanc.modelo_cobranca == "exito"
    assert lanc.percentual_exito == Decimal("20.00")
    assert lanc.valor_base_exito == Decimal("100000.00")
    assert lanc.valor == Decimal("20000.00")


def test_modelo_fixo_ignora_campos_de_exito(client, login, post_csrf, cenario):
    login("admin@teste.com")
    r = post_csrf("/financeiro/novo", {
        "descricao": "Honorario fixo simples", "valor": "500.00", "natureza": "receita",
        "modelo_cobranca": "fixo", "percentual_exito": "999", "valor_base_exito": "999999",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Honorario fixo simples").first()
    assert lanc.modelo_cobranca == "fixo"
    assert lanc.percentual_exito is None, "percentual_exito deveria ficar None fora do modelo exito"
    assert lanc.valor_base_exito is None


def test_duplicar_retainer_cria_cobranca_do_mes_seguinte(client, login, post_csrf, cenario):
    login("admin@teste.com")
    post_csrf("/financeiro/novo", {
        "descricao": "Retainer mensal - assessoria", "valor": "3000.00", "natureza": "receita",
        "modelo_cobranca": "retainer", "status": "pago", "data_vencimento": "2026-08-05",
        "unidade_id": str(cenario["unidade_id"]), "cliente_id": str(cenario["cliente_id"]),
    }, get_url="/financeiro/novo")
    retainer = Lancamento.query.filter_by(descricao="Retainer mensal - assessoria").first()

    r_dup = post_csrf(f"/financeiro/{retainer.id}/duplicar-retainer", {}, get_url="/financeiro/novo")
    assert r_dup.status_code == 200

    novos = Lancamento.query.filter_by(descricao="Retainer mensal - assessoria").order_by(Lancamento.id).all()
    assert len(novos) == 2, f"esperava 2 lançamentos (original + duplicado), veio {len(novos)}"
    duplicado = novos[1]
    assert duplicado.status == "pendente"
    assert duplicado.modelo_cobranca == "retainer"
    assert duplicado.data_vencimento == date(2026, 9, 5), f"esperava 2026-09-05, veio {duplicado.data_vencimento}"
    assert duplicado.valor == Decimal("3000.00")


def test_duplicar_retainer_rejeita_lancamento_que_nao_e_retainer(client, login, post_csrf, cenario):
    login("admin@teste.com")
    post_csrf("/financeiro/novo", {
        "descricao": "Honorario fixo simples", "valor": "500.00", "natureza": "receita",
        "modelo_cobranca": "fixo", "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    lanc_fixo = Lancamento.query.filter_by(descricao="Honorario fixo simples").first()

    r_dup_invalido = post_csrf(f"/financeiro/{lanc_fixo.id}/duplicar-retainer", {}, get_url="/financeiro/novo")
    assert r_dup_invalido.status_code == 400, \
        f"esperava 400 pra lançamento não-retainer, veio {r_dup_invalido.status_code}"


def test_recibo_recusa_lancamento_pendente_e_gera_pdf_para_pago(client, login, cenario):
    login("admin@teste.com")

    lanc_pendente = Lancamento(descricao="Honorario ainda pendente", tipo="honorario", natureza="receita",
                                valor=Decimal("800.00"), status="pendente", unidade_id=cenario["unidade_id"],
                                cliente_id=cenario["cliente_id"], criado_por_id=cenario["admin_id"])
    db.session.add(lanc_pendente)
    db.session.commit()

    r_recibo_pendente = client.get(f"/financeiro/{lanc_pendente.id}/recibo")
    assert r_recibo_pendente.status_code == 302, "deveria redirecionar (não gerar recibo) pra lançamento pendente"

    lanc_pago = Lancamento(descricao="Honorario ja pago pra recibo", tipo="honorario", natureza="receita",
                            valor=Decimal("1500.00"), status="pago", data_pagamento=date.today(),
                            forma_pagamento="Pix", unidade_id=cenario["unidade_id"], cliente_id=cenario["cliente_id"],
                            processo_id=cenario["processo_id"], criado_por_id=cenario["admin_id"])
    db.session.add(lanc_pago)
    db.session.commit()

    r_recibo_ok = client.get(f"/financeiro/{lanc_pago.id}/recibo")
    assert r_recibo_ok.status_code == 200
    assert r_recibo_ok.mimetype == "application/pdf"
    conteudo_pdf = r_recibo_ok.data
    assert conteudo_pdf[:4] == b"%PDF", "resposta não começa com o cabeçalho de um PDF válido"
    assert len(conteudo_pdf) > 500, "PDF gerado parece vazio demais"
