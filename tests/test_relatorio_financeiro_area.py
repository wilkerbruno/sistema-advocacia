"""
Testa o relatório financeiro por área do direito: soma só receita PRÓPRIA
do escritório (exclui depósito de conta de terceiros e despesas) e ignora
lançamento avulso sem processo vinculado (não tem área pra entrar).
"""
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Lancamento


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin3@teste.com", papel="admin", nome="Admin")

    cliente = Cliente(nome="Cliente Area", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    p_civel = Processo(numero_processo="0000001-11.2026.8.26.0100", cliente_id=cliente.id,
                        unidade_id=unidade_id, area_direito="Cível", responsavel_id=admin_id, criado_por_id=admin_id)
    p_trab = Processo(numero_processo="0000002-11.2026.8.26.0100", cliente_id=cliente.id,
                       unidade_id=unidade_id, area_direito="Trabalhista", responsavel_id=admin_id, criado_por_id=admin_id)
    db.session.add_all([p_civel, p_trab])
    db.session.flush()

    # Cível: 1000 recebido, 500 pendente (receita própria)
    db.session.add(Lancamento(descricao="Honorario civel pago", tipo="honorario", natureza="receita",
                               valor=Decimal("1000.00"), status="pago", unidade_id=unidade_id,
                               processo_id=p_civel.id, criado_por_id=admin_id))
    db.session.add(Lancamento(descricao="Honorario civel pendente", tipo="honorario", natureza="receita",
                               valor=Decimal("500.00"), status="pendente", unidade_id=unidade_id,
                               processo_id=p_civel.id, criado_por_id=admin_id))
    # Cível: depósito de terceiros de 9999 - NÃO deve entrar na receita própria
    db.session.add(Lancamento(descricao="Deposito judicial civel", tipo="outro", natureza="receita",
                               valor=Decimal("9999.00"), status="pago", unidade_id=unidade_id,
                               processo_id=p_civel.id, conta_terceiros=True, criado_por_id=admin_id))
    # Trabalhista: 300 recebido
    db.session.add(Lancamento(descricao="Honorario trabalhista pago", tipo="honorario", natureza="receita",
                               valor=Decimal("300.00"), status="pago", unidade_id=unidade_id,
                               processo_id=p_trab.id, criado_por_id=admin_id))
    # Despesa (natureza=despesa) vinculada ao cível - NÃO deve contar como receita
    db.session.add(Lancamento(descricao="Despesa civel", tipo="despesa", natureza="despesa",
                               valor=Decimal("50.00"), status="pago", unidade_id=unidade_id,
                               processo_id=p_civel.id, criado_por_id=admin_id))
    # Lançamento sem processo (avulso) - não deve aparecer em nenhuma área
    db.session.add(Lancamento(descricao="Lancamento avulso sem processo", tipo="outro", natureza="receita",
                               valor=Decimal("777.00"), status="pago", unidade_id=unidade_id,
                               criado_por_id=admin_id))
    db.session.commit()

    return dict(admin_id=admin_id)


def test_relatorio_por_area_segrega_receita_propria(client, login, cenario):
    login("admin3@teste.com")
    r = client.get("/admin/relatorios")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert "1.000,00" in html, "receita recebida do cível não apareceu"
    assert "500,00" in html
    assert "300,00" in html
    assert html.count("9.999,00") == 0, "depósito de terceiros vazou pro relatório por área"
    assert "777,00" not in html, "lançamento avulso sem processo não deveria aparecer em nenhuma área"
