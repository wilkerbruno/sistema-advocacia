"""
Quinta rodada de simplificação do menu (mesma sessão de
test_hub_plataforma.py e test_hub_regras_parametros.py) — pedido
explícito do usuário: "faça o mesmo com os itens de [...] 'painel e
filas'".

"Governança de carteira > Painel e filas" reunia 5 itens soltos sob um
título fixo (Painel de governança, Fila de intimações, Métricas e
relatório semanal, Produtividade da equipe, Contingenciamento — ver
PENDENCIAS.md). Viraram abas de uma tela só (governanca.py::
painel_e_filas, template governanca/painel_e_filas_hub.html), mesmo
padrão dos outros hubs desta sessão. As cinco telas antigas continuam
existindo e funcionando sozinhas.

Único nível de permissão (@login_required — nenhuma dessas telas era
admin-only) — nenhuma aba fica de fora por papel, mas o CONTEÚDO da aba
Painel varia (distribuição por unidade só pra admin), igual sempre foi.

"Métricas e relatório semanal" já era, desde a 1ª rodada, um hub de 2
abas próprio — aqui vira uma aba "aninhada" (ids prefixados com "pf-").
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Hub Painel e Filas", dono_da_plataforma=dono_da_plataforma)
    db.session.add(empresa)
    db.session.flush()
    if not dono_da_plataforma:
        db.session.add(Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                                data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade = Unidade(nome="Matriz", codigo="M1", empresa_id=empresa.id)
    db.session.add(unidade)
    db.session.flush()
    return empresa, unidade


def _criar_usuario_e_logar(unidade_id, email, papel, login):
    u = Usuario(email=email, unidade_id=unidade_id, papel=papel, nome=email.split("@")[0])
    u.set_senha("senha123")
    db.session.add(u)
    db.session.commit()
    login(email)
    return u


# ---------------------- Visibilidade / permissão ----------------------

def test_qualquer_usuario_autenticado_ve_as_cinco_abas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv@hubpainel.com", "advogado", login)

    r = client.get("/governanca/painel-e-filas")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    for rotulo in ("Painel de governança", "Fila de intimações", "Métricas e relatório semanal",
                   "Produtividade da equipe", "Contingenciamento"):
        assert rotulo in html
    for tab_id in ("tab-btn-painel", "tab-btn-fila", "tab-btn-metricas_relatorio",
                   "tab-btn-produtividade", "tab-btn-contingenciamento"):
        assert f'id="{tab_id}"' in html


# ---------------------- Menu lateral ----------------------

def test_menu_mostra_um_link_so_para_painel_e_filas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv2@hubpainel.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")
    nav = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    assert 'href="/governanca/painel-e-filas"' in nav
    assert "Painel e filas" in nav
    # os links antigos que existiam soltos não aparecem mais no menu:
    assert 'href="/governanca/painel"' not in nav
    assert 'href="/governanca/fila-intimacoes"' not in nav
    assert 'href="/governanca/metricas-e-relatorio"' not in nav
    assert 'href="/governanca/produtividade"' not in nav
    assert 'href="/governanca/contingenciamento"' not in nav


def test_link_painel_e_filas_fica_ativo_dentro_de_qualquer_tela_do_grupo(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv3@hubpainel.com", "advogado", login)

    html = client.get("/governanca/produtividade").data.decode("utf-8")
    idx = html.index('href="/governanca/painel-e-filas"')
    trecho = html[max(0, idx - 60):idx]
    assert "active" in trecho


# ---------------------- Conteúdo das abas + rotas antigas ----------------------

def test_rotas_antigas_de_painel_e_filas_continuam_acessiveis_diretamente(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv4@hubpainel.com", "advogado", login)

    assert client.get("/governanca/painel").status_code == 200
    assert client.get("/governanca/fila-intimacoes").status_code == 200
    assert client.get("/governanca/metricas").status_code == 200
    assert client.get("/governanca/relatorio-semanal/preview").status_code == 200
    assert client.get("/governanca/metricas-e-relatorio").status_code == 200
    assert client.get("/governanca/produtividade").status_code == 200
    assert client.get("/governanca/contingenciamento").status_code == 200


def test_aba_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv5@hubpainel.com", "advogado", login)

    r = client.get("/governanca/painel-e-filas?tab=contingenciamento")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-contingenciamento"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


def test_admin_ve_distribuicao_por_unidade_na_aba_painel(client, login, app):
    """"Distribuição por unidade" só renderiza quando a consulta traz
    alguma linha (mesma condição `{% if distribuicao_unidade %}` de
    sempre) — precisa de pelo menos um processo cadastrado."""
    from app.models import Cliente, Processo

    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@hubpainel.com", "admin", login)

    cliente = Cliente(tipo_pessoa="PF", nome="Cliente Teste", unidade_id=unidade.id)
    db.session.add(cliente)
    db.session.flush()
    db.session.add(Processo(area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id))
    db.session.commit()

    html = client.get("/governanca/painel-e-filas").data.decode("utf-8")
    assert "Distribuição por unidade" in html


def test_advogado_nao_ve_distribuicao_por_unidade_na_aba_painel(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv6@hubpainel.com", "advogado", login)

    html = client.get("/governanca/painel-e-filas").data.decode("utf-8")
    assert "Distribuição por unidade" not in html


# ---------------------- Aba aninhada "Métricas e relatório semanal" ----------------------

def test_aba_metricas_relatorio_aninhada_mostra_as_duas_sub_abas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv7@hubpainel.com", "advogado", login)

    r = client.get("/governanca/painel-e-filas?tab=metricas_relatorio")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert 'id="pf-tab-btn-metricas"' in html
    assert 'id="pf-tab-btn-relatorio"' in html
    assert "Taxa de cumprimento de prazo" in html  # conteúdo de governanca/metricas.html
    assert "Prazos desta semana" in html  # conteúdo de governanca/relatorio_semanal_preview.html


def test_aba_metricas_relatorio_subaba_relatorio_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv8@hubpainel.com", "advogado", login)

    r = client.get("/governanca/painel-e-filas?tab=metricas_relatorio&subtab=relatorio")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="pf-tab-btn-relatorio"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


def test_rotas_antigas_de_metricas_e_relatorio_nao_colidem_com_a_aba_aninhada(client, login, app):
    """A tela solteira governanca.painel_metricas continua usando os
    mesmos ids sem prefixo de sempre (ela reaproveita o mesmo partial
    _corpo_painel_metricas.html com id_prefixo="")."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv9@hubpainel.com", "advogado", login)

    html = client.get("/governanca/metricas-e-relatorio").data.decode("utf-8")
    assert 'id="tab-btn-metricas"' in html
    assert 'id="pf-tab-btn-metricas"' not in html
