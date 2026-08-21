"""
Testa exportação (portabilidade) e anonimização de dados de cliente sob a
LGPD: qualquer usuário do escopo pode exportar, só admin pode anonimizar,
a anonimização exige confirmação explícita, apaga o dado pessoal mas
preserva processo/lançamento/apontamento vinculados, e nunca roda duas
vezes no mesmo cliente.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Lancamento, Apontamento


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]

    admin_id = criar_usuario(unidade_id, "adminlgpd@teste.com", papel="admin")
    func_id = criar_usuario(unidade_id, "funclgpd@teste.com", papel="funcionario")

    cliente = Cliente(nome="Maria da Silva", cpf_cnpj="123.456.789-00", email="maria@example.com",
                       telefone="(11) 99999-0000", endereco="Rua Teste, 100", cidade="Sao Paulo", estado="SP",
                       unidade_id=unidade_id, criado_por_id=admin_id,
                       base_legal_tratamento="contrato")
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(numero_processo="0000090-11.2026.8.26.0100", cliente_id=cliente.id,
                         unidade_id=unidade_id, area_direito="Cível", status="encerrado",
                         responsavel_id=admin_id, criado_por_id=admin_id)
    db.session.add(processo)
    db.session.flush()

    lanc = Lancamento(descricao="Honorario Maria", tipo="honorario", natureza="receita",
                       valor=Decimal("1000.00"), status="pago", unidade_id=unidade_id,
                       cliente_id=cliente.id, processo_id=processo.id, criado_por_id=admin_id)
    db.session.add(lanc)

    apont = Apontamento(usuario_id=admin_id, unidade_id=unidade_id, processo_id=processo.id,
                         data=date.today(), horas=Decimal("2.0"), descricao="Trabalho no caso", faturavel=True)
    db.session.add(apont)
    db.session.commit()

    return dict(admin_id=admin_id, func_id=func_id, cliente_id=cliente.id, processo_id=processo.id)


def test_formulario_mostra_e_salva_campos_lgpd(client, login, post_csrf, cenario):
    login("adminlgpd@teste.com")
    r_form = client.get(f"/clientes/{cenario['cliente_id']}/editar")
    assert r_form.status_code == 200
    html_form = r_form.data.decode("utf-8")
    assert 'name="base_legal_tratamento"' in html_form
    assert 'name="consentimento_obtido_em"' in html_form

    r = post_csrf(f"/clientes/{cenario['cliente_id']}/editar", {
        "nome": "Maria da Silva", "tipo_pessoa": "PF", "cpf_cnpj": "123.456.789-00",
        "email": "maria@example.com", "telefone": "(11) 99999-0000", "endereco": "Rua Teste, 100",
        "cidade": "Sao Paulo", "estado": "SP", "base_legal_tratamento": "consentimento",
        "consentimento_obtido_em": "2026-01-15",
        "consentimento_observacoes": "assinado no contrato de honorarios",
    })
    assert r.status_code == 200

    c = db.session.get(Cliente, cenario["cliente_id"])
    assert c.base_legal_tratamento == "consentimento"
    assert c.consentimento_obtido_em == date(2026, 1, 15)
    assert c.consentimento_observacoes == "assinado no contrato de honorarios"

    r_detalhe = client.get(f"/clientes/{cenario['cliente_id']}")
    assert "Consentimento" in r_detalhe.data.decode("utf-8")


def test_exportacao_dados_lgpd(client, login, cenario):
    login("adminlgpd@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/exportar-dados-lgpd")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    dados = json.loads(r.data.decode("utf-8"))
    assert dados["cliente"]["nome"] == "Maria da Silva"
    assert dados["cliente"]["cpf_cnpj"] == "123.456.789-00"
    assert len(dados["processos"]) == 1
    assert dados["processos"][0]["numero_processo"] == "0000090-11.2026.8.26.0100"
    assert len(dados["lancamentos_financeiros"]) == 1
    assert dados["lancamentos_financeiros"][0]["valor"] == 1000.0
    assert len(dados["apontamentos_horas"]) == 1
    assert dados["apontamentos_horas"][0]["descricao"] == "Trabalho no caso"


def test_usuario_comum_exporta_mas_nao_anonimiza(client, login, cenario):
    login("funclgpd@teste.com")
    r_anon = client.get(f"/clientes/{cenario['cliente_id']}/anonimizar")
    assert r_anon.status_code == 403
    r_export = client.get(f"/clientes/{cenario['cliente_id']}/exportar-dados-lgpd")
    assert r_export.status_code == 200


def test_anonimizacao_exige_confirmacao_e_preserva_vinculos(client, login, post_csrf, cenario):
    login("adminlgpd@teste.com")
    r_confirmar = client.get(f"/clientes/{cenario['cliente_id']}/anonimizar")
    assert r_confirmar.status_code == 200
    assert "irreversível" in r_confirmar.data.decode("utf-8")

    # sem marcar o checkbox "confirmar" -> nada deve acontecer
    r_sem_confirmar = post_csrf(f"/clientes/{cenario['cliente_id']}/anonimizar", {})
    assert r_sem_confirmar.status_code == 200
    c_ainda_nao = db.session.get(Cliente, cenario["cliente_id"])
    assert c_ainda_nao.nome == "Maria da Silva", "não deveria ter anonimizado sem o checkbox marcado"

    # agora com o checkbox marcado
    r_anon_ok = post_csrf(f"/clientes/{cenario['cliente_id']}/anonimizar", {"confirmar": "1"})
    assert r_anon_ok.status_code == 200

    c_anon = db.session.get(Cliente, cenario["cliente_id"])
    assert c_anon.nome == f"Cliente anonimizado #{cenario['cliente_id']}"
    assert c_anon.cpf_cnpj is None
    assert c_anon.email is None
    assert c_anon.telefone is None
    assert c_anon.endereco is None
    assert c_anon.anonimizado_em is not None
    assert c_anon.anonimizado_por_id is not None

    # processo e lançamento vinculados continuam intactos
    p_check = db.session.get(Processo, cenario["processo_id"])
    assert p_check is not None
    assert p_check.numero_processo == "0000090-11.2026.8.26.0100"


def test_nao_deixa_anonimizar_de_novo(client, login, post_csrf, cenario):
    login("adminlgpd@teste.com")
    post_csrf(f"/clientes/{cenario['cliente_id']}/anonimizar", {"confirmar": "1"})

    r_de_novo = client.get(f"/clientes/{cenario['cliente_id']}/anonimizar", follow_redirects=True)
    assert "já foi anonimizado" in r_de_novo.data.decode("utf-8")


def test_banner_de_anonimizado_aparece_no_detalhe(client, login, post_csrf, cenario):
    login("adminlgpd@teste.com")
    post_csrf(f"/clientes/{cenario['cliente_id']}/anonimizar", {"confirmar": "1"})

    r_detalhe = client.get(f"/clientes/{cenario['cliente_id']}")
    assert "anonimizados" in r_detalhe.data.decode("utf-8").lower()
