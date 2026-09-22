"""
Pedido explícito do usuário: "em novo lançamento deve ter a opção do
usuário colocar uma multa sendo em valor em real ou porcentagem e também
um juros por dia/mes de atraso, o usuário escolhe" — campos opcionais no
formulário "Novo lançamento" (tanto receita quanto despesa, tanto "Caixa
do escritório" quanto "Conta de terceiros" — pedido explícito do usuário:
"isso deve acontecer tanto em caixa do escritório quanto em conta de
terceiros"), guardados em Lancamento.multa_tipo/multa_valor/juros_tipo/
juros_valor e usados por Lancamento.calcular_valor_atualizado() pra saber
quanto está devido hoje num lançamento vencido — nunca aplicados sozinhos,
sempre calculados sob demanda (ver comentário no modelo).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import Lancamento
from tests.conftest import extrair_csrf


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@multajuros.com", papel="admin", nome="Admin Multa Juros")
    return dict(admin_id=admin_id, unidade_id=unidade_id)


def _post(client, data):
    r = client.get("/financeiro/novo")
    token = extrair_csrf(r.data.decode("utf-8"))
    payload = dict(data)
    payload["csrf_token"] = token
    return client.post("/financeiro/novo", data=payload, follow_redirects=True)


# ---------------------- Formulário "Novo lançamento" ----------------------

def test_form_novo_lancamento_tem_campos_de_multa_e_juros(client, login, cenario):
    login("admin@multajuros.com")
    html = client.get("/financeiro/novo").data.decode("utf-8")
    assert 'name="multa_tipo"' in html
    assert 'name="multa_valor"' in html
    assert 'name="juros_tipo"' in html
    assert 'name="juros_valor"' in html


def test_criar_lancamento_com_multa_em_valor_fixo(client, login, cenario):
    login("admin@multajuros.com")
    r = _post(client, {
        "descricao": "Custas com multa fixa", "valor": "1000.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]), "multa_tipo": "valor", "multa_valor": "80.00",
    })
    assert r.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Custas com multa fixa").first()
    assert lanc.multa_tipo == "valor"
    assert lanc.multa_valor == Decimal("80.00")
    assert lanc.juros_tipo is None
    assert lanc.juros_valor is None


def test_criar_lancamento_com_multa_percentual(client, login, cenario):
    login("admin@multajuros.com")
    r = _post(client, {
        "descricao": "Honorário com multa percentual", "valor": "2000.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]), "multa_tipo": "percentual", "multa_valor": "2",
    })
    assert r.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Honorário com multa percentual").first()
    assert lanc.multa_tipo == "percentual"
    assert lanc.multa_valor == Decimal("2")


def test_criar_lancamento_com_juros_ao_dia(client, login, cenario):
    login("admin@multajuros.com")
    r = _post(client, {
        "descricao": "Com juros ao dia", "valor": "500.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]), "juros_tipo": "dia", "juros_valor": "0.033",
    })
    assert r.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Com juros ao dia").first()
    assert lanc.juros_tipo == "dia"
    assert lanc.juros_valor == Decimal("0.033")


def test_criar_lancamento_com_juros_ao_mes(client, login, cenario):
    login("admin@multajuros.com")
    r = _post(client, {
        "descricao": "Com juros ao mês", "valor": "500.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]), "juros_tipo": "mes", "juros_valor": "1",
    })
    assert r.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Com juros ao mês").first()
    assert lanc.juros_tipo == "mes"
    assert lanc.juros_valor == Decimal("1")


def test_criar_lancamento_sem_escolher_tipo_ignora_valor_avulso(client, login, cenario):
    """Se o usuário digitar um valor mas deixar o select em 'Nenhuma'/
    'Nenhum' (ex: mudou de ideia), o valor avulso não é salvo — evita
    multa/juros "fantasma" sem tipo definido."""
    login("admin@multajuros.com")
    r = _post(client, {
        "descricao": "Sem tipo escolhido", "valor": "300.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]), "multa_valor": "99.00", "juros_valor": "5",
    })
    assert r.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Sem tipo escolhido").first()
    assert lanc.multa_tipo is None
    assert lanc.multa_valor is None
    assert lanc.juros_tipo is None
    assert lanc.juros_valor is None


def test_multa_e_juros_funcionam_em_conta_de_terceiros_tambem(client, login, cenario):
    login("admin@multajuros.com")
    r = _post(client, {
        "descricao": "Repasse de terceiro com encargos", "valor": "1200.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]), "conta_terceiros": "1",
        "multa_tipo": "percentual", "multa_valor": "10", "juros_tipo": "dia", "juros_valor": "0.05",
    })
    assert r.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Repasse de terceiro com encargos").first()
    assert lanc.conta_terceiros is True
    assert lanc.multa_tipo == "percentual"
    assert lanc.juros_tipo == "dia"


# ---------------------- Lancamento.calcular_valor_atualizado() ----------------------

def _lancamento(**overrides):
    dados = dict(descricao="teste", valor=Decimal("1000.00"), status="pendente",
                 data_vencimento=date.today() - timedelta(days=10), unidade_id=1)
    dados.update(overrides)
    return Lancamento(**dados)


def test_valor_atualizado_sem_encargos_e_sem_atraso_e_igual_ao_valor():
    l = _lancamento(data_vencimento=date.today() + timedelta(days=5))
    assert l.calcular_valor_atualizado() == Decimal("1000.00")
    assert l.esta_vencido() is False


def test_valor_atualizado_com_multa_fixa():
    l = _lancamento(multa_tipo="valor", multa_valor=Decimal("50.00"))
    assert l.esta_vencido() is True
    assert l.calcular_valor_atualizado() == Decimal("1050.00")


def test_valor_atualizado_com_multa_percentual():
    l = _lancamento(multa_tipo="percentual", multa_valor=Decimal("10"))
    assert l.calcular_valor_atualizado() == Decimal("1100.00")


def test_valor_atualizado_com_juros_ao_dia():
    # 10 dias de atraso, 0.1% ao dia sobre 1000 = 1.00/dia * 10 = 10.00
    l = _lancamento(juros_tipo="dia", juros_valor=Decimal("0.1"))
    assert l.calcular_valor_atualizado() == Decimal("1010.000")


def test_valor_atualizado_com_juros_ao_mes():
    l = _lancamento(juros_tipo="mes", juros_valor=Decimal("3"))
    # 10 dias / 30 = 1/3 de mês, 3% ao mês sobre 1000 = 30 * (1/3) = 10.00
    esperado = Decimal("1000.00") + Decimal("1000.00") * Decimal("3") / Decimal("100") * (Decimal("10") / Decimal("30"))
    assert l.calcular_valor_atualizado() == esperado


def test_valor_atualizado_soma_multa_e_juros_juntos():
    l = _lancamento(multa_tipo="valor", multa_valor=Decimal("50.00"),
                     juros_tipo="dia", juros_valor=Decimal("0.1"))
    assert l.calcular_valor_atualizado() == Decimal("1060.000")


def test_lancamento_pago_nunca_esta_vencido_mesmo_com_data_passada():
    l = _lancamento(status="pago", multa_tipo="valor", multa_valor=Decimal("50.00"))
    assert l.esta_vencido() is False
    assert l.calcular_valor_atualizado() == Decimal("1000.00")


def test_lancamento_cancelado_nunca_esta_vencido():
    l = _lancamento(status="cancelado", multa_tipo="valor", multa_valor=Decimal("50.00"))
    assert l.esta_vencido() is False


def test_lancamento_status_atrasado_explicito_tambem_conta_como_vencido():
    l = _lancamento(status="atrasado", multa_tipo="valor", multa_valor=Decimal("50.00"))
    assert l.esta_vencido() is True


def test_lancamento_sem_data_vencimento_nunca_esta_vencido():
    l = _lancamento(data_vencimento=None, multa_tipo="valor", multa_valor=Decimal("50.00"))
    assert l.esta_vencido() is False


def test_tem_encargos_configurados():
    assert _lancamento().tem_encargos_configurados is False
    assert _lancamento(multa_tipo="valor", multa_valor=Decimal("1")).tem_encargos_configurados is True
    assert _lancamento(juros_tipo="dia", juros_valor=Decimal("1")).tem_encargos_configurados is True


# ---------------------- Listagem: coluna "Data de pagamento" antes de "Status" ----------------------

def test_listagem_tem_coluna_data_de_pagamento_antes_do_status(client, login, cenario):
    login("admin@multajuros.com")
    _post(client, {
        "descricao": "Lançamento qualquer para a tabela aparecer", "valor": "10.00",
        "natureza": "receita", "unidade_id": str(cenario["unidade_id"]),
    })
    html = client.get("/financeiro/").data.decode("utf-8")
    idx_data_pagamento = html.index("Data de pagamento")
    idx_status = html.index("<th>Status</th>")
    assert idx_data_pagamento < idx_status


def test_listagem_mostra_valor_atualizado_para_vencido_com_encargos(client, login, cenario):
    login("admin@multajuros.com")
    _post(client, {
        "descricao": "Vencido com encargos na listagem", "valor": "1000.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]), "data_vencimento": "2020-01-01",
        "multa_tipo": "valor", "multa_valor": "50.00",
    })
    html = client.get("/financeiro/").data.decode("utf-8")
    assert "atualizado:" in html
