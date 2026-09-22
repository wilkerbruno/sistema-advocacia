"""
Bug relatado pelo usuário depois de testar a aba "Conta de terceiros" do
Financeiro: uma DESPESA marcada como conta de terceiros (ex: repasse já
pago a um cliente) não aparecia em NENHUM dos três cards de resumo — os
três (`total_a_receber`, `total_recebido_mes`, `total_atrasado`) sempre
filtravam só `natureza == "receita"`, mesmo na aba de terceiros (onde
dinheiro circula nas DUAS direções: recebido em nome de terceiro E
repassado/pago em nome de terceiro).

Correção: três totais NOVOS, espelhando os três de sempre mas do lado
despesa (`total_a_repassar`, `total_repassado_mes`, `total_repasse_atrasado`),
mostrados como três cards A MAIS só na aba "Conta de terceiros" — a aba
"Caixa do escritório" continua com só os três originais (cards de
receita/AR, como sempre foi, não é o que o usuário reportou).
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Lancamento


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@terceiros.com", papel="admin", nome="Admin Terceiros")
    return dict(admin_id=admin_id, unidade_id=unidade_id)


def _criar_lancamento(unidade_id, admin_id, **kwargs):
    dados = dict(
        descricao="Lançamento de teste", tipo="outro", natureza="despesa", valor=Decimal("100.00"),
        status="pendente", unidade_id=unidade_id, criado_por_id=admin_id, conta_terceiros=True,
    )
    dados.update(kwargs)
    lanc = Lancamento(**dados)
    db.session.add(lanc)
    db.session.commit()
    return lanc


def test_despesa_terceiros_pendente_aparece_em_a_repassar(client, login, cenario):
    _criar_lancamento(cenario["unidade_id"], cenario["admin_id"],
                       descricao="Repasse a fazer", valor=Decimal("1289.02"), status="pendente")

    login("admin@terceiros.com")
    html = client.get("/financeiro/?conta=terceiros").data.decode("utf-8")
    assert "A repassar (pendente)" in html
    assert "1.289,02" in html


def test_despesa_terceiros_paga_este_mes_aparece_em_repassado_este_mes(client, login, cenario):
    _criar_lancamento(cenario["unidade_id"], cenario["admin_id"],
                       descricao="Repasse já feito", valor=Decimal("1289.02"), status="pago",
                       data_pagamento=date.today())

    login("admin@terceiros.com")
    html = client.get("/financeiro/?conta=terceiros").data.decode("utf-8")
    assert "Repassado este mês" in html
    assert "1.289,02" in html


def test_despesa_terceiros_pendente_vencida_aparece_em_repasse_em_atraso(client, login, cenario):
    _criar_lancamento(cenario["unidade_id"], cenario["admin_id"],
                       descricao="Repasse atrasado", valor=Decimal("50.00"), status="pendente",
                       data_vencimento=date.today() - timedelta(days=5))

    login("admin@terceiros.com")
    html = client.get("/financeiro/?conta=terceiros").data.decode("utf-8")
    assert "Repasse em atraso" in html
    assert "50,00" in html


def test_cards_de_despesa_nao_aparecem_na_aba_operacional(client, login, cenario):
    """Os três cards novos são específicos da aba "Conta de terceiros" —
    a "Caixa do escritório" continua só com os três de receita/AR, exatamente
    como sempre foi (não é o que o usuário reportou)."""
    _criar_lancamento(cenario["unidade_id"], cenario["admin_id"],
                       descricao="Repasse pendente", valor=Decimal("77.00"), status="pendente")

    login("admin@terceiros.com")
    html = client.get("/financeiro/?conta=operacional").data.decode("utf-8")
    assert "A repassar (pendente)" not in html
    assert "Repassado este mês" not in html
    assert "Repasse em atraso" not in html


def test_despesa_terceiros_nao_entra_nos_totais_de_receita(client, login, cenario):
    """Uma despesa de conta de terceiros não pode inflar os totais de
    receita/AR (total_a_receber etc.) — natureza errada não deveria contar
    pra nenhum dos dois lados."""
    _criar_lancamento(cenario["unidade_id"], cenario["admin_id"],
                       descricao="Despesa não deveria contar como receita", valor=Decimal("999.00"),
                       status="pendente")

    login("admin@terceiros.com")
    html = client.get("/financeiro/?conta=terceiros").data.decode("utf-8")
    # "999,00" só deve aparecer associado ao card de despesa (A repassar),
    # nunca "vazando" pro card de receita (A receber de terceiros).
    idx_receber = html.index("A receber de terceiros (pendente)")
    trecho_receber = html[idx_receber:idx_receber + 200]
    assert "999,00" not in trecho_receber


def test_receita_terceiros_continua_no_card_a_receber(client, login, cenario):
    """Não pode ser uma regressão pro outro lado: receita de terceiros
    continua aparecendo normalmente em "A receber de terceiros"."""
    _criar_lancamento(cenario["unidade_id"], cenario["admin_id"],
                       descricao="Depósito judicial recebido", natureza="receita",
                       valor=Decimal("2500.00"), status="pendente")

    login("admin@terceiros.com")
    html = client.get("/financeiro/?conta=terceiros").data.decode("utf-8")
    assert "A receber de terceiros (pendente)" in html
    assert "2.500,00" in html


def test_select_natureza_tem_largura_maior_pra_nao_sobrepor_a_seta(client, login, cenario):
    login("admin@terceiros.com")
    html = client.get("/financeiro/").data.decode("utf-8")
    idx = html.index('name="natureza"')
    trecho = html[idx:idx + 120]
    assert "width: 190px" in trecho
