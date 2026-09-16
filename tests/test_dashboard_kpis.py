"""
Painel/Dashboard: cards viraram links (PENDENCIAS.md, seção -100) — pedido
explícito: "ao clicar em 'Processos ativos' vai para a pagina de
processos, clicando em clientes ativos vai para a pagina de clientes, em
prazos em atenção deve direcionar para uma pagina com todos os prazos em
atenção, prazos perdidos deve direcionar para uma pagina de prazos
perdidos [...] e unidades ativas vão para a paginas de unidades [...]
alem disso no card processos ativos deve aparecer tambem o valor total
dos processos em R$".

Também cobre a correção da contagem de "Prazos em atenção": antes o
número do card era só `len()` da lista de prévia, que tinha `.limit(8)` —
com mais de 8 prazos em atenção, o card sempre mostrava "8", escondendo
quantos realmente havia.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, Cliente, Processo, Prazo

SENHA = "senha123"


def _montar_empresa():
    empresa = Empresa(nome="Empresa Painel Teste", dono_da_plataforma=False)
    db.session.add(empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade = Unidade(nome="Matriz", codigo="PT1", empresa_id=empresa.id)
    db.session.add(unidade)
    db.session.flush()
    return empresa, unidade


def _criar_usuario_e_logar(unidade_id, email, papel, login):
    u = Usuario(email=email, unidade_id=unidade_id, papel=papel, nome=email.split("@")[0])
    u.set_senha(SENHA)
    db.session.add(u)
    db.session.commit()
    login(email)
    return u


def test_cards_do_painel_apontam_para_as_paginas_certas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin@painelteste.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'href="/processos/?status=ativo"' in html
    assert 'href="/clientes/?ativo=1"' in html
    assert 'href="/governanca/prazos-em-atencao"' in html
    assert 'href="/governanca/prazos-perdidos"' in html
    assert 'href="/admin/unidades"' in html


def test_card_processos_ativos_mostra_valor_total_em_reais(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin2@painelteste.com", "admin", login)

    cliente = Cliente(nome="Cliente X", unidade_id=unidade.id)
    db.session.add(cliente)
    db.session.flush()
    db.session.add_all([
        Processo(area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id,
                 numero_processo="0000001-11.2024.8.26.0001", status="ativo", valor_causa=10000),
        Processo(area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id,
                 numero_processo="0000002-22.2024.8.26.0001", status="ativo", valor_causa=25000.50),
        # Encerrado não deve entrar na soma nem na contagem.
        Processo(area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id,
                 numero_processo="0000003-33.2024.8.26.0001", status="encerrado", valor_causa=999999),
    ])
    db.session.commit()

    html = client.get("/").data.decode("utf-8")

    assert "R$ 35.000,50" in html


def test_card_prazos_em_atencao_nao_fica_travado_em_8(client, login, app):
    """Regressão: antes, o número do card vinha de `len(prazos_vencendo)`,
    e `prazos_vencendo` tinha `.limit(8)` — com mais de 8 prazos em
    atenção, o card sempre mostrava 8 em vez do total real."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin3@painelteste.com", "admin", login)

    cliente = Cliente(nome="Cliente Y", unidade_id=unidade.id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id,
                         numero_processo="0000004-44.2024.8.26.0001", status="ativo")
    db.session.add(processo)
    db.session.flush()

    hoje = date.today()
    for i in range(11):
        db.session.add(Prazo(
            processo_id=processo.id, descricao=f"Prazo em atenção {i}",
            data_vencimento=hoje + timedelta(days=1), status="pendente",
        ))
    db.session.commit()

    html = client.get("/").data.decode("utf-8")
    inicio = html.index("Prazos em atenção")
    trecho = html[inicio:inicio + 400]
    assert ">11<" in trecho


def test_prazos_em_atencao_e_perdidos_nao_se_sobrepoem(client, login, app):
    """Regressão: antes, `prazos_vencendo` usava só `<= limite_alerta` sem
    piso em `hoje`, então um prazo já vencido contava nos DOIS cards ao
    mesmo tempo."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin4@painelteste.com", "admin", login)

    cliente = Cliente(nome="Cliente Z", unidade_id=unidade.id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id,
                         numero_processo="0000005-55.2024.8.26.0001", status="ativo")
    db.session.add(processo)
    db.session.flush()

    hoje = date.today()
    db.session.add(Prazo(processo_id=processo.id, descricao="Vencido", status="pendente",
                          data_vencimento=hoje - timedelta(days=1)))
    db.session.commit()

    r = client.get("/")
    assert r.status_code == 200
    # Bate certinho com prazos_perdidos_count (1) e prazos_em_atencao_count (0).
    html = r.data.decode("utf-8")
    inicio_atencao = html.index("Prazos em atenção")
    assert ">0<" in html[inicio_atencao:inicio_atencao + 400]
    inicio_perdidos = html.index("Prazos perdidos")
    assert ">1<" in html[inicio_perdidos:inicio_perdidos + 400]


def test_clientes_listar_com_ativo_1_mostra_so_ativos(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin5@painelteste.com", "admin", login)

    db.session.add_all([
        Cliente(nome="Cliente Ativo", unidade_id=unidade.id, ativo=True),
        Cliente(nome="Cliente Inativo", unidade_id=unidade.id, ativo=False),
    ])
    db.session.commit()

    html = client.get("/clientes/?ativo=1").data.decode("utf-8")
    assert "Cliente Ativo" in html
    assert "Cliente Inativo" not in html

    html_todos = client.get("/clientes/").data.decode("utf-8")
    assert "Cliente Ativo" in html_todos
    assert "Cliente Inativo" in html_todos
