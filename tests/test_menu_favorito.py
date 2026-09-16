"""
"Menu favorito" — pedido explícito do usuário (PENDENCIAS.md): depois de
"Operação" virar um grupo recolhível igual aos outros dois, ele pediu uma
opção em "Configurações" pra escolher uma categoria do menu como favorita
e ela já aparecer aberta ao entrar no sistema — exatamente como
"Operação" sempre funcionou.

A preferência é PESSOAL (por usuário, não por empresa) e fica em
app/routes/conta.py — por isso qualquer papel logado pode acessar, não só
quem gerencia usuários/plataforma.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario


def _montar_empresa():
    empresa = Empresa(nome="Empresa Favorito Teste", dono_da_plataforma=False)
    db.session.add(empresa)
    db.session.flush()
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


def test_tela_preferencias_acessivel_por_qualquer_papel(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv@favteste.com", "advogado", login)

    resp = client.get("/minha-conta/preferencias")

    assert resp.status_code == 200
    assert "Categoria favorita".encode("utf-8") in resp.data


def test_salvar_favorito_valido_persiste_no_usuario(client, login, post_csrf, app):
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv2@favteste.com", "advogado", login)

    resp = post_csrf("/minha-conta/preferencias/favorito", {"grupo_favorito": "governanca"},
                      get_url="/minha-conta/preferencias")

    assert resp.status_code == 200
    usuario_recarregado = db.session.get(Usuario, usuario.id)
    assert usuario_recarregado.menu_grupo_favorito == "governanca"


def test_salvar_favorito_invalido_e_recusado_sem_alterar_nada(client, login, post_csrf, app):
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv3@favteste.com", "advogado", login)
    usuario.menu_grupo_favorito = "config"
    db.session.commit()

    resp = post_csrf("/minha-conta/preferencias/favorito", {"grupo_favorito": "algo-invalido"},
                      get_url="/minha-conta/preferencias")

    assert resp.status_code == 200
    usuario_recarregado = db.session.get(Usuario, usuario.id)
    assert usuario_recarregado.menu_grupo_favorito == "config"


def test_limpar_favorito_envia_valor_vazio(client, login, post_csrf, app):
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv4@favteste.com", "advogado", login)
    usuario.menu_grupo_favorito = "operacao"
    db.session.commit()

    resp = post_csrf("/minha-conta/preferencias/favorito", {"grupo_favorito": ""},
                      get_url="/minha-conta/preferencias")

    assert resp.status_code == 200
    usuario_recarregado = db.session.get(Usuario, usuario.id)
    assert usuario_recarregado.menu_grupo_favorito is None


def test_grupo_favorito_aparece_expandido_mesmo_fora_dele(client, login, app):
    """O ponto central do pedido: marcar "Governança de carteira" como
    favorita faz ela aparecer ABERTA mesmo estando numa página de
    "Operação" (fora dela)."""
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv5@favteste.com", "advogado", login)
    usuario.menu_grupo_favorito = "governanca"
    db.session.commit()

    html = client.get("/").data.decode("utf-8")  # dashboard = dentro de "Operação"

    inicio = html.index('data-grupo="governanca"')
    trecho = html[max(0, inicio - 40):inicio + 200]
    assert "expandido" in trecho


def test_sem_favorito_grupo_fora_da_pagina_atual_comeca_fechado(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv6@favteste.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")  # dashboard = dentro de "Operação"

    inicio = html.index('data-grupo="governanca"')
    trecho = html[max(0, inicio - 40):inicio + 200]
    assert "expandido" not in trecho
    assert "recolhido" in html[inicio:inicio + 400]
