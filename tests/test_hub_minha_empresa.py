"""
Quarta rodada de simplificação do menu (mesma sessão de
test_menu_simplificacao.py, test_submenus_segundo_nivel.py e
test_hub_rotina_e_minha_conta.py) — pedido explícito do usuário depois de
ver os hubs "Rotina" e "Minha conta": "otimo, agora quero que junte todos
os itens de 'minha empresa' tambem".

"Configurações > Minha empresa" reunia 8 itens soltos num acordeão de 2º
nível (Unidades, Equipe, Relatórios, Auditoria, Alçada de aprovação,
Minha licença, Módulos, Integrações — ver PENDENCIAS.md, seção -110).
Viraram abas de uma tela só (app/routes/admin.py::minha_empresa, template
app/templates/admin/minha_empresa_hub.html), mesmo padrão de "Rotina" e
"Minha conta". As oito rotas/telas antigas continuam existindo e
funcionando sozinhas.

Particularidade deste hub: nem toda aba aparece pra todo mundo. "Equipe"
é visível pra admin OU GESTOR (mesmo escopo de sempre — é o único item de
"Minha empresa" que não é exclusivo de admin); as outras sete exigem
admin; "Minha licença"/"Módulos" ficam de fora também pro admin
desenvolvedor (empresa dona da plataforma não tem licença).
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario
from tests.conftest import extrair_csrf


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Hub Minha Empresa", dono_da_plataforma=dono_da_plataforma)
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


# ---------------------- Visibilidade por papel ----------------------

def test_admin_de_empresa_cliente_ve_as_oito_abas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin@hubempresa.com", "admin", login)

    r = client.get("/admin/minha-empresa")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    for rotulo in ("Unidades", "Equipe", "Relatórios", "Auditoria", "Alçada de aprovação",
                   "Minha licença", "Módulos", "Integrações"):
        assert rotulo in html


def test_admin_desenvolvedor_nao_ve_licenca_nem_modulos(client, login, app):
    """Empresa dona da plataforma não tem licença — mesma regra de sempre
    (ver app/routes/licenciamento.py)."""
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@hubempresa.com", "admin", login)

    html = client.get("/admin/minha-empresa").data.decode("utf-8")

    assert 'id="tab-btn-unidades"' in html
    assert 'id="tab-btn-integracoes"' in html
    assert 'id="tab-btn-licenca"' not in html
    assert 'id="tab-btn-modulos"' not in html


def test_gestor_so_ve_a_aba_equipe(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "gestor@hubempresa.com", "gestor", login)

    r = client.get("/admin/minha-empresa")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert 'id="aba-equipe"' in html
    assert 'id="tab-btn-unidades"' not in html
    assert 'id="tab-btn-relatorios"' not in html
    assert 'id="tab-btn-auditoria"' not in html
    assert 'id="tab-btn-alcada"' not in html
    assert 'id="tab-btn-licenca"' not in html
    assert 'id="tab-btn-modulos"' not in html
    assert 'id="tab-btn-integracoes"' not in html


def test_advogado_comum_nao_acessa_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv@hubempresa.com", "advogado", login)

    r = client.get("/admin/minha-empresa")
    assert r.status_code == 403

    html_menu = client.get("/").data.decode("utf-8")
    assert 'href="/admin/minha-empresa"' not in html_menu


# ---------------------- Menu lateral ----------------------

def test_menu_mostra_um_link_so_para_minha_empresa(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin2@hubempresa.com", "admin", login)

    html = client.get("/").data.decode("utf-8")
    # Isola o <nav> lateral — o dashboard também tem um card de KPI que
    # linka direto pra "/admin/unidades" (ver app/templates/dashboard/
    # index.html), sem relação nenhuma com o menu; não faz parte do que
    # esta rodada mudou, então fica de fora da checagem abaixo.
    nav = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    assert 'href="/admin/minha-empresa"' in nav
    assert "Minha empresa" in nav
    # os links antigos que existiam soltos no acordeão não aparecem mais no menu:
    assert 'href="/admin/unidades"' not in nav
    assert 'href="/admin/usuarios"' not in nav
    assert 'href="/admin/relatorios"' not in nav
    assert 'href="/admin/auditoria"' not in nav
    assert 'href="/admin/alcada-aprovacao"' not in nav
    assert 'href="/minha-licenca"' not in nav
    assert 'href="/modulos"' not in nav
    assert 'href="/minhas-integracoes"' not in nav


def test_link_minha_empresa_fica_ativo_dentro_de_qualquer_tela_do_grupo(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin3@hubempresa.com", "admin", login)

    html = client.get("/admin/usuarios").data.decode("utf-8")
    idx = html.index('href="/admin/minha-empresa"')
    trecho = html[max(0, idx - 60):idx]
    assert "active" in trecho


# ---------------------- Conteúdo das abas + rotas antigas ----------------------

def test_rotas_antigas_de_minha_empresa_continuam_acessiveis_diretamente(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin4@hubempresa.com", "admin", login)

    assert client.get("/admin/unidades").status_code == 200
    assert client.get("/admin/usuarios").status_code == 200
    assert client.get("/admin/relatorios").status_code == 200
    assert client.get("/admin/auditoria").status_code == 200
    assert client.get("/admin/alcada-aprovacao").status_code == 200
    assert client.get("/minha-licenca").status_code == 200
    assert client.get("/modulos").status_code == 200
    assert client.get("/minhas-integracoes").status_code == 200


def test_aba_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin5@hubempresa.com", "admin", login)

    r = client.get("/admin/minha-empresa?tab=integracoes")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-integracoes"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


# ---------------------- Ações de POST feitas de dentro do hub voltam pro hub ----------------------

def test_salvar_alcada_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin6@hubempresa.com", "admin", login)

    r_get = client.get("/admin/minha-empresa?tab=alcada")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        "/admin/alcada-aprovacao",
        data={"alcada_nivel1_valor": "1000,00", "csrf_token": token},
        headers={"Referer": "http://localhost/admin/minha-empresa?tab=alcada"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/minha-empresa?tab=alcada")


def test_solicitar_modulo_a_partir_do_hub_volta_para_o_hub(client, login, app):
    from app.models import Modulo

    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin7@hubempresa.com", "admin", login)

    modulo = Modulo(chave="teste_hub_modulo", nome="Módulo de teste", ativo=True, obrigatorio=False)
    db.session.add(modulo)
    db.session.commit()

    r_get = client.get("/admin/minha-empresa?tab=modulos")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/modulos/{modulo.id}/solicitar",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/admin/minha-empresa?tab=modulos"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/minha-empresa?tab=modulos")


def test_remover_timbrado_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin8@hubempresa.com", "admin", login)

    r_get = client.get("/admin/minha-empresa?tab=integracoes")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        "/minhas-integracoes/timbrado/remover",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/admin/minha-empresa?tab=integracoes"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/minha-empresa?tab=integracoes")
