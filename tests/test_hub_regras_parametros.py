"""
Quinta rodada de simplificação do menu (mesma sessão de
test_hub_plataforma.py e das anteriores) — pedido explícito do usuário:
"faça o mesmo com os itens de [...] 'regras e parametros' [...]".

"Governança de carteira > Regras e parâmetros" reunia 5 itens soltos num
acordeão de 2º nível (Regras de próxima ação, Mapa de estado (TPU),
Verificação de conflitos, Modelos de peças, Tabela de custas — ver
PENDENCIAS.md). Viraram abas de uma tela só (governanca.py::
regras_e_parametros, template governanca/regras_e_parametros_hub.html),
mesmo padrão dos outros hubs desta sessão. As cinco rotas/telas antigas
continuam existindo e funcionando sozinhas.

Único nível de permissão (admin) — nenhuma aba varia por papel dentro do
hub.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, RegraProximaAcao, MapaEstadoTPU, ModeloPeca, TabelaCustas
from tests.conftest import extrair_csrf


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Hub Regras Parametros", dono_da_plataforma=dono_da_plataforma)
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

def test_admin_ve_as_cinco_abas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin@hubregras.com", "admin", login)

    r = client.get("/governanca/regras-e-parametros")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    for rotulo in ("Regras de próxima ação", "Mapa de estado (TPU)", "Verificação de conflitos",
                   "Modelos de peças", "Tabela de custas"):
        assert rotulo in html
    for tab_id in ("tab-btn-regras", "tab-btn-mapa_estado", "tab-btn-conflitos",
                   "tab-btn-modelos_peca", "tab-btn-tabela_custas"):
        assert f'id="{tab_id}"' in html


def test_advogado_comum_nao_acessa_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv@hubregras.com", "advogado", login)

    r = client.get("/governanca/regras-e-parametros")
    assert r.status_code == 403

    html_menu = client.get("/").data.decode("utf-8")
    assert 'href="/governanca/regras-e-parametros"' not in html_menu


# ---------------------- Menu lateral ----------------------

def test_menu_mostra_um_link_so_para_regras_parametros(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin2@hubregras.com", "admin", login)

    html = client.get("/").data.decode("utf-8")
    nav = html[html.index('<nav class="sidebar-nav">'):html.index("</nav>")]

    assert 'href="/governanca/regras-e-parametros"' in nav
    assert "Regras e parâmetros" in nav
    # os links antigos que existiam soltos no acordeão não aparecem mais no menu:
    assert 'href="/governanca/regras-proxima-acao"' not in nav
    assert 'href="/governanca/mapa-estado-tpu"' not in nav
    assert 'href="/governanca/conflitos"' not in nav
    assert 'href="/governanca/modelos-peca"' not in nav
    assert 'href="/governanca/tabela-custas"' not in nav


def test_link_regras_parametros_fica_ativo_dentro_de_qualquer_tela_do_grupo(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin3@hubregras.com", "admin", login)

    html = client.get("/governanca/mapa-estado-tpu").data.decode("utf-8")
    idx = html.index('href="/governanca/regras-e-parametros"')
    trecho = html[max(0, idx - 60):idx]
    assert "active" in trecho


# ---------------------- Conteúdo das abas + rotas antigas ----------------------

def test_rotas_antigas_de_regras_parametros_continuam_acessiveis_diretamente(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin4@hubregras.com", "admin", login)

    assert client.get("/governanca/regras-proxima-acao").status_code == 200
    assert client.get("/governanca/mapa-estado-tpu").status_code == 200
    assert client.get("/governanca/conflitos").status_code == 200
    assert client.get("/governanca/modelos-peca").status_code == 200
    assert client.get("/governanca/tabela-custas").status_code == 200


def test_aba_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin5@hubregras.com", "admin", login)

    r = client.get("/governanca/regras-e-parametros?tab=tabela_custas")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-tabela_custas"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


# ---------------------- Ações de POST feitas de dentro do hub voltam pro hub ----------------------

def test_alternar_regra_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin6@hubregras.com", "admin", login)

    regra = RegraProximaAcao(ato_capturado="Teste hub", acao_exigida="Fazer algo", ativo=True)
    db.session.add(regra)
    db.session.commit()

    r_get = client.get("/governanca/regras-e-parametros?tab=regras")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/governanca/regras-proxima-acao/{regra.id}/alternar-ativo",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/governanca/regras-e-parametros?tab=regras"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/governanca/regras-e-parametros?tab=regras")


def test_alternar_mapa_estado_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin7@hubregras.com", "admin", login)

    item = MapaEstadoTPU(codigo_tpu="9999", estado_negocio="Em instrução", ativo=True)
    db.session.add(item)
    db.session.commit()

    r_get = client.get("/governanca/regras-e-parametros?tab=mapa_estado")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/governanca/mapa-estado-tpu/{item.id}/alternar-ativo",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/governanca/regras-e-parametros?tab=mapa_estado"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/governanca/regras-e-parametros?tab=mapa_estado")


def test_excluir_modelo_peca_a_partir_do_hub_volta_para_o_hub(client, login, app):
    empresa, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin8@hubregras.com", "admin", login)

    modelo = ModeloPeca(empresa_id=empresa.id, nome="Modelo teste", tipo_peca="contestacao",
                         conteudo="conteúdo qualquer", ativo=True)
    db.session.add(modelo)
    db.session.commit()

    r_get = client.get("/governanca/regras-e-parametros?tab=modelos_peca")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/governanca/modelos-peca/{modelo.id}/excluir",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/governanca/regras-e-parametros?tab=modelos_peca"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/governanca/regras-e-parametros?tab=modelos_peca")


def test_carregar_tabela_padrao_tjsp_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin9@hubregras.com", "admin", login)

    r_get = client.get("/governanca/regras-e-parametros?tab=tabela_custas")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        "/governanca/tabela-custas/carregar-padrao-tjsp",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/governanca/regras-e-parametros?tab=tabela_custas"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/governanca/regras-e-parametros?tab=tabela_custas")


# ---------------------- Bug corrigido: varredura de conflitos não pode rodar em toda carga do hub ----------------------
# (usuário relatou 500 em ambiente real ao acessar o hub — a varredura de
# conflitos, que a própria varrer_conflitos_da_empresa() avisa que só deve
# rodar sob demanda, estava sendo recalculada em TODA aba, sempre. Ver
# comentário em governanca.py::regras_e_parametros().)

def test_carregar_o_hub_sem_pedir_a_aba_conflitos_nao_roda_a_varredura_pesada(client, login, app, monkeypatch):
    """Acessar qualquer aba que não seja 'conflitos' (inclusive a aba
    padrão) não pode chamar varrer_conflitos_da_empresa() — é essa
    varredura incondicional que causava o 500 relatado em produção."""
    import app.routes.governanca as governanca_mod

    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin10@hubregras.com", "admin", login)

    chamadas = []
    monkeypatch.setattr(
        governanca_mod,
        "varrer_conflitos_da_empresa",
        lambda *a, **k: chamadas.append((a, k)) or [],
    )

    for url in ("/governanca/regras-e-parametros",
                "/governanca/regras-e-parametros?tab=regras",
                "/governanca/regras-e-parametros?tab=mapa_estado",
                "/governanca/regras-e-parametros?tab=modelos_peca",
                "/governanca/regras-e-parametros?tab=tabela_custas"):
        r = client.get(url)
        assert r.status_code == 200

    assert chamadas == []


def test_aba_conflitos_via_query_string_roda_a_varredura_e_mostra_o_resultado(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "admin11@hubregras.com", "admin", login)

    r = client.get("/governanca/regras-e-parametros?tab=conflitos")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    idx_botao = html.index('id="tab-btn-conflitos"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho
    # sem cliente/processo cadastrado, cai no estado vazio de "sem conflito":
    assert "Nenhum conflito encontrado" in html


def test_aba_conflitos_para_admin_desenvolvedor_pede_pra_escolher_empresa(client, login, app):
    """Admin desenvolvedor não tem empresa fixa — a aba de conflitos pede
    pra escolher uma antes de rodar a varredura (mesmo comportamento de
    sempre da tela solteira /governanca/conflitos)."""
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@hubregras.com", "admin", login)

    r = client.get("/governanca/regras-e-parametros?tab=conflitos")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Escolha uma empresa acima" in html
