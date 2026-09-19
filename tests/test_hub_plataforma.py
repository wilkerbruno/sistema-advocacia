"""
Quinta rodada de simplificação do menu (mesma sessão de
test_hub_minha_empresa.py e das anteriores) — pedido explícito do usuário
depois de ver os hubs "Minha empresa"/"Rotina"/"Minha conta": "faça o
mesmo com os itens de 'plataforma', 'regras e parametros' e 'painel e
filas'".

"Configurações > Plataforma" reunia 4 itens soltos num acordeão de 2º
nível (Painel de licenças, Empresas clientes, Catálogo de módulos, Preços
padrão — ver PENDENCIAS.md). Viraram abas de uma tela só
(app/routes/plataforma.py::hub, template app/templates/plataforma/hub.html),
mesmo padrão de "Minha empresa"/"Rotina"/"Minha conta". As quatro
rotas/telas antigas continuam existindo e funcionando sozinhas.

Único nível de permissão (admin desenvolvedor) — ao contrário de "Minha
empresa", nenhuma aba varia por papel dentro do hub.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, Modulo, ConfiguracaoPlataforma
from tests.conftest import extrair_csrf


def _montar_empresa(dono_da_plataforma=False, nome="Empresa Hub Plataforma", codigo_unidade="M1"):
    empresa = Empresa(nome=nome, dono_da_plataforma=dono_da_plataforma)
    db.session.add(empresa)
    db.session.flush()
    if not dono_da_plataforma:
        db.session.add(Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                                data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade = Unidade(nome="Matriz", codigo=codigo_unidade, empresa_id=empresa.id)
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

def test_admin_desenvolvedor_ve_as_quatro_abas(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@hubplataforma.com", "admin", login)

    r = client.get("/plataforma/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    for rotulo in ("Painel de licenças", "Empresas clientes", "Catálogo de módulos", "Preços padrão"):
        assert rotulo in html
    for tab_id in ("tab-btn-licencas", "tab-btn-empresas", "tab-btn-modulos", "tab-btn-planos"):
        assert f'id="{tab_id}"' in html


def test_admin_comum_nao_acessa_o_hub(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "admin@hubplataforma.com", "admin", login)

    r = client.get("/plataforma/")
    assert r.status_code == 403

    html_menu = client.get("/").data.decode("utf-8")
    assert 'href="/plataforma/"' not in html_menu


def test_advogado_comum_nao_acessa_o_hub(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "adv@hubplataforma.com", "advogado", login)

    r = client.get("/plataforma/")
    assert r.status_code == 403


# ---------------------- Menu lateral ----------------------

def test_menu_mostra_um_link_so_para_plataforma(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev2@hubplataforma.com", "admin", login)

    html = client.get("/").data.decode("utf-8")
    nav = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    assert 'href="/plataforma/"' in nav
    assert "Plataforma" in nav
    # os links antigos que existiam soltos no acordeão não aparecem mais no menu:
    assert 'href="/plataforma/licencas"' not in nav
    assert 'href="/plataforma/empresas"' not in nav
    assert 'href="/plataforma/modulos"' not in nav
    assert 'href="/plataforma/planos"' not in nav


def test_link_plataforma_fica_ativo_dentro_de_qualquer_tela_do_grupo(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev3@hubplataforma.com", "admin", login)

    html = client.get("/plataforma/empresas").data.decode("utf-8")
    idx = html.index('href="/plataforma/"')
    trecho = html[max(0, idx - 60):idx]
    assert "active" in trecho


# ---------------------- Conteúdo das abas + rotas antigas ----------------------

def test_rotas_antigas_de_plataforma_continuam_acessiveis_diretamente(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev4@hubplataforma.com", "admin", login)

    assert client.get("/plataforma/licencas").status_code == 200
    assert client.get("/plataforma/empresas").status_code == 200
    assert client.get("/plataforma/modulos").status_code == 200
    assert client.get("/plataforma/planos").status_code == 200


def test_aba_via_query_string(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev5@hubplataforma.com", "admin", login)

    r = client.get("/plataforma/?tab=planos")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-planos"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


# ---------------------- Ações de POST feitas de dentro do hub voltam pro hub ----------------------

def test_atualizar_licenca_rapido_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev6@hubplataforma.com", "admin", login)

    cliente, _ = _montar_empresa(dono_da_plataforma=False, nome="Empresa Cliente Licença", codigo_unidade="M2")

    r_get = client.get("/plataforma/?tab=licencas")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/plataforma/licencas/{cliente.id}/atualizar",
        data={"valor_negociado": "500,00", "plano": "mensal", "csrf_token": token},
        headers={"Referer": "http://localhost/plataforma/?tab=licencas"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/plataforma/?tab=licencas")


def test_alternar_modulo_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev7@hubplataforma.com", "admin", login)

    modulo = Modulo(chave="teste_hub_plataforma", nome="Módulo de teste", ativo=True, obrigatorio=False)
    db.session.add(modulo)
    db.session.commit()

    r_get = client.get("/plataforma/?tab=modulos")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/plataforma/modulos/{modulo.id}/alternar-ativo",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/plataforma/?tab=modulos"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/plataforma/?tab=modulos")


def test_editar_planos_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev8@hubplataforma.com", "admin", login)

    r_get = client.get("/plataforma/?tab=planos")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        "/plataforma/planos",
        data={
            "preco_padrao_mensal": "199,90",
            "preco_padrao_trimestral": "549,90",
            "preco_padrao_anual": "1999,90",
            "csrf_token": token,
        },
        headers={"Referer": "http://localhost/plataforma/?tab=planos"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/plataforma/?tab=planos")
