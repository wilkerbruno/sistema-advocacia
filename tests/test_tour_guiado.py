"""
Tutorial guiado de primeiro acesso (ver app/static/js/tour_guiado.js,
Usuario.tour_concluido_em e app/routes/conta.py::tutorial_concluir).

Como o passeio em si roda inteiramente em JavaScript no navegador (destacar
elementos, mostrar um cartão, avançar/voltar), a suíte automatizada não
consegue exercitar a UI diretamente — em vez disso, cobre exatamente o que
o servidor Flask é responsável por: decidir quando o tour deve começar
sozinho (`tour_deve_iniciar`, injetado pelo context processor), a rota que
marca "já viu" e o HTML que a página do Painel entrega pro JS (a flag de
início automático e o link "Rever tutorial").
"""
import re

from app.extensions import db
from app.models import Usuario


def _csrf_do_meta(html):
    """A página do Painel não tem nenhum <form> (é só cartões de KPI), então
    não dá pra usar o fixture `post_csrf` (que procura um campo oculto
    `csrf_token`) — extrai do <meta name="csrf-token"> em base.html, o
    mesmo valor que o JS de verdade manda no cabeçalho X-CSRFToken."""
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else None


def test_usuario_novo_recebe_flag_de_inicio_automatico_no_painel(client, login, empresa_basica, criar_usuario):
    usuario_id = criar_usuario(empresa_basica["unidade_id"], "novo@escritorio.com")
    login("novo@escritorio.com")

    resp = client.get("/")
    html = resp.data.decode("utf-8")

    assert "window.TOUR_AUTO_INICIAR = true;" in html
    assert 'src="/static/js/tour_guiado.js"' in html
    # Usuário nunca viu o tour ainda -> coluna continua nula no banco.
    usuario = db.session.get(Usuario, usuario_id)
    assert usuario.tour_concluido_em is None


def test_flag_de_inicio_automatico_so_aparece_no_painel(client, login, empresa_basica, criar_usuario):
    criar_usuario(empresa_basica["unidade_id"], "novo2@escritorio.com")
    login("novo2@escritorio.com")

    resp = client.get("/clientes/")
    html = resp.data.decode("utf-8")

    # Fora do Painel, o tour nunca tenta começar sozinho (ver comentário em
    # injetar_globais — evita disputar atenção com o conteúdo de outra
    # tela).
    assert "window.TOUR_AUTO_INICIAR = false;" in html


def test_concluir_tutorial_marca_usuario_e_desliga_inicio_automatico(client, login, empresa_basica, criar_usuario):
    usuario_id = criar_usuario(empresa_basica["unidade_id"], "concluiu@escritorio.com")
    login("concluiu@escritorio.com")

    token = _csrf_do_meta(client.get("/").data.decode("utf-8"))
    resp = client.post("/minha-conta/tutorial/concluir", headers={"X-CSRFToken": token})
    assert resp.status_code == 200
    assert resp.json == {"ok": True}

    usuario = db.session.get(Usuario, usuario_id)
    assert usuario.tour_concluido_em is not None

    # E o Painel para de mandar iniciar sozinho no próximo carregamento.
    html = client.get("/").data.decode("utf-8")
    assert "window.TOUR_AUTO_INICIAR = false;" in html


def test_rever_tutorial_forca_inicio_mesmo_ja_tendo_concluido(client, login, empresa_basica, criar_usuario):
    criar_usuario(empresa_basica["unidade_id"], "revisor@escritorio.com")
    login("revisor@escritorio.com")
    token = _csrf_do_meta(client.get("/").data.decode("utf-8"))
    client.post("/minha-conta/tutorial/concluir", headers={"X-CSRFToken": token})

    # Link "Rever tutorial" (Minha conta) usa ?tutorial=1 no Painel.
    html = client.get("/?tutorial=1").data.decode("utf-8")
    assert "window.TOUR_AUTO_INICIAR = true;" in html


def test_link_rever_tutorial_aparece_no_hub_minha_conta(client, login, empresa_basica, criar_usuario):
    """"Rever tutorial" não é mais um link solto no menu lateral — mora
    dentro do hub "Minha conta" (app/routes/conta.py::hub, menu
    simplificado a pedido explícito do usuário). O menu lateral só tem um
    link pro hub; dentro dele é que aparece "Rever tutorial"."""
    criar_usuario(empresa_basica["unidade_id"], "qualquer@escritorio.com")
    login("qualquer@escritorio.com")

    html_menu = client.get("/").data.decode("utf-8")
    assert 'href="/minha-conta/"' in html_menu

    html_hub = client.get("/minha-conta/").data.decode("utf-8")
    assert 'href="/?tutorial=1"' in html_hub
    assert "Rever tutorial" in html_hub or "Rever o tutorial guiado" in html_hub


def test_rota_concluir_exige_login(client):
    # Um token CSRF válido (da própria tela de login, que tem formulário)
    # não basta sozinho — sem sessão autenticada, @login_required manda
    # pro login antes mesmo de marcar qualquer coisa.
    token_html = client.get("/login").data.decode("utf-8")
    m = re.search(r'name="csrf_token" value="([^"]+)"', token_html)
    resp = client.post("/minha-conta/tutorial/concluir", data={"csrf_token": m.group(1)})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_rota_concluir_exige_csrf(client, login, empresa_basica, criar_usuario):
    criar_usuario(empresa_basica["unidade_id"], "semcsrf@escritorio.com")
    login("semcsrf@escritorio.com")

    resp = client.post("/minha-conta/tutorial/concluir")
    assert resp.status_code == 400
