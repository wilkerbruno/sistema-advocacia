"""
Item 2 da lista de pipeline de IA jurídica trazida pelo usuário
(PENDENCIAS.md, seção -108): "Download dos autos — Sessão autenticada do
escritório no PJe, eproc, Projudi e ESAJ, com certificado ou credencial
guardada no cofre já existente."

Escopo desta rodada, coberto aqui: SÓ o cofre de credenciais (login/senha
por sistema+tribunal, cifrado, empresa inteira, nunca reexibido em texto
puro) — ver aviso completo em app/models/credencial_tribunal.py sobre por
que nenhum conector de login/download automatizado foi implementado nesta
rodada (CAPTCHA/proteção de cada tribunal tornaria isso frágil e
arriscado nos termos de uso).

Cobre: cadastro cifra a senha (nunca em texto puro no banco), edição com
senha em branco mantém a senha antiga, listagem/edição nunca reexibe a
senha decifrada, isolamento por empresa (mesmo padrão de ModeloPeca —
uma empresa não vê/edita a credencial de outra), admin-only, e alternância
ativo/inativo e exclusão.
"""
from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models import CredencialTribunal, Empresa
from app.utils import cofre


@pytest.fixture()
def cofre_configurado(app):
    """Gera uma chave Fernet válida só para a duração do teste — mesmo
    padrão de tests/test_gemini_byok.py (nenhum teste existente configura
    COFRE_SENHA_PROCESSO_KEY por padrão)."""
    chave_original = app.config.get("COFRE_SENHA_PROCESSO_KEY")
    app.config["COFRE_SENHA_PROCESSO_KEY"] = Fernet.generate_key().decode()
    yield
    app.config["COFRE_SENHA_PROCESSO_KEY"] = chave_original


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    empresa_id = empresa_basica["empresa_id"]
    admin_id = criar_usuario(unidade_id, "admin@credenciais.com", papel="admin", nome="Admin Credenciais")
    adv_id = criar_usuario(unidade_id, "adv@credenciais.com", papel="advogado", nome="Advogado Credenciais")
    return dict(admin_id=admin_id, adv_id=adv_id, unidade_id=unidade_id, empresa_id=empresa_id)


# ---------- cadastro ----------

def test_cadastra_credencial_cifra_a_senha(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    r = post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "eproc", "tribunal": "trf4", "usuario_login": "12345678900",
        "senha": "minhaSenhaSecreta123",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    assert r.status_code == 200

    credencial = CredencialTribunal.query.filter_by(empresa_id=cenario["empresa_id"]).first()
    assert credencial is not None
    assert credencial.sistema == "eproc"
    assert credencial.tribunal == "TRF4"  # normalizado pra maiúsculo
    assert credencial.usuario_login == "12345678900"
    assert credencial.senha_cifrada is not None
    assert b"minhaSenhaSecreta123" not in credencial.senha_cifrada
    assert cofre.decifrar_segredo(credencial.senha_cifrada) == "minhaSenhaSecreta123"
    assert credencial.criado_por_id == cenario["admin_id"]


def test_cadastro_sem_senha_permite_salvar_sem_senha_cifrada(app, client, login, post_csrf, cofre_configurado,
                                                               cenario):
    login("admin@credenciais.com")
    r = post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "projudi", "tribunal": "TJPR", "usuario_login": "usuario.projudi",
        "senha": "",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    assert r.status_code == 200
    credencial = CredencialTribunal.query.filter_by(empresa_id=cenario["empresa_id"]).first()
    assert credencial is not None
    assert credencial.senha_cifrada is None


def test_cadastro_sistema_invalido_e_recusado(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    r = post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "sistema_inventado", "tribunal": "TJSP", "usuario_login": "user", "senha": "x",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    assert r.status_code == 200
    assert CredencialTribunal.query.count() == 0


def test_cadastro_sem_tribunal_ou_login_e_recusado(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    r = post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "esaj", "tribunal": "", "usuario_login": "", "senha": "x",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    assert r.status_code == 200
    assert CredencialTribunal.query.count() == 0


# ---------- listagem nunca reexibe a senha ----------

def test_listagem_nunca_mostra_senha_em_texto_puro(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "pje", "tribunal": "TRT2", "usuario_login": "adv123",
        "senha": "SenhaSuperSecreta!",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")

    r = client.get("/minhas-integracoes/credenciais-tribunal")
    assert r.status_code == 200
    texto = r.data.decode("utf-8")
    assert "SenhaSuperSecreta!" not in texto
    assert "cadastrada" in texto  # só o indicador booleano, nunca o valor


def test_form_de_edicao_nunca_preenche_campo_senha(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "pje", "tribunal": "TRT2", "usuario_login": "adv123", "senha": "SenhaSuperSecreta!",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    credencial = CredencialTribunal.query.first()

    r = client.get(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/editar")
    assert r.status_code == 200
    assert "SenhaSuperSecreta!" not in r.data.decode("utf-8")


# ---------- edição: senha em branco mantém a senha antiga ----------

def test_editar_com_senha_em_branco_mantem_senha_antiga(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "eproc", "tribunal": "TRF4", "usuario_login": "user1", "senha": "SenhaOriginal",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    credencial = CredencialTribunal.query.first()
    senha_cifrada_original = credencial.senha_cifrada

    r = post_csrf(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/editar", {
        "sistema": "eproc", "tribunal": "TRF4", "usuario_login": "user1_novo", "senha": "",
    }, get_url=f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/editar")
    assert r.status_code == 200

    credencial = db.session.get(CredencialTribunal, credencial.id)
    assert credencial.usuario_login == "user1_novo"
    assert credencial.senha_cifrada == senha_cifrada_original
    assert cofre.decifrar_segredo(credencial.senha_cifrada) == "SenhaOriginal"


def test_editar_com_senha_nova_substitui(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "eproc", "tribunal": "TRF4", "usuario_login": "user1", "senha": "SenhaOriginal",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    credencial = CredencialTribunal.query.first()

    post_csrf(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/editar", {
        "sistema": "eproc", "tribunal": "TRF4", "usuario_login": "user1", "senha": "SenhaNova456",
    }, get_url=f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/editar")

    credencial = db.session.get(CredencialTribunal, credencial.id)
    assert cofre.decifrar_segredo(credencial.senha_cifrada) == "SenhaNova456"


# ---------- alternar ativo / excluir ----------

def test_alternar_ativo_e_excluir(app, client, login, post_csrf, cofre_configurado, cenario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "esaj", "tribunal": "TJSP", "usuario_login": "user1", "senha": "x",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    credencial = CredencialTribunal.query.first()
    assert credencial.ativo is True

    post_csrf(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/alternar-ativo", {},
              get_url="/minhas-integracoes/credenciais-tribunal")
    assert db.session.get(CredencialTribunal, credencial.id).ativo is False

    post_csrf(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/excluir", {},
              get_url="/minhas-integracoes/credenciais-tribunal")
    assert db.session.get(CredencialTribunal, credencial.id) is None


# ---------- admin-only ----------

def test_advogado_comum_nao_acessa_credenciais_tribunal(app, client, login, cenario):
    login("adv@credenciais.com")
    r = client.get("/minhas-integracoes/credenciais-tribunal")
    assert r.status_code == 403


# ---------- isolamento por empresa ----------

def test_admin_de_outra_empresa_nao_ve_nem_edita_credencial_alheia(app, client, login, post_csrf,
                                                                     cofre_configurado, cenario, criar_usuario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "esaj", "tribunal": "TJSP", "usuario_login": "user1", "senha": "x",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")
    credencial = CredencialTribunal.query.first()

    outra_empresa = Empresa(nome="Outro Escritório Credenciais")
    db.session.add(outra_empresa)
    db.session.flush()
    from app.models import Licenca, Unidade
    db.session.add(Licenca(empresa_id=outra_empresa.id, plano="mensal", valor_negociado=100,
                            status="ativa", data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    outra_unidade = Unidade(nome="Outra Matriz Credenciais", codigo="OMCR1", empresa_id=outra_empresa.id)
    db.session.add(outra_unidade)
    db.session.commit()
    criar_usuario(outra_unidade.id, "admin_outro@credenciais.com", papel="admin")

    client.get("/logout")
    login("admin_outro@credenciais.com")
    r = client.get("/minhas-integracoes/credenciais-tribunal")
    assert r.status_code == 200
    assert "TJSP" not in r.data.decode("utf-8")

    r = client.get(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/editar")
    assert r.status_code == 404

    # get_url aponta pro formulário de cadastro (sempre tem um <form> com
    # csrf_token) em vez da lista — a lista de admin_outro está vazia
    # (não vê a credencial alheia), então não teria nenhum form/token pra
    # extrair, o que quebraria só a extração do teste, não o app em si.
    r = post_csrf(f"/minhas-integracoes/credenciais-tribunal/{credencial.id}/excluir", {},
                  get_url="/minhas-integracoes/credenciais-tribunal/nova")
    assert r.status_code == 404
    assert db.session.get(CredencialTribunal, credencial.id) is not None, \
        "credencial de outra empresa nunca pode ser excluída"


# ---------- tela "Minhas Integrações" mostra a contagem ----------

def test_minhas_integracoes_mostra_contagem_de_credenciais(app, client, login, post_csrf, cofre_configurado,
                                                              cenario):
    login("admin@credenciais.com")
    post_csrf("/minhas-integracoes/credenciais-tribunal/nova", {
        "sistema": "esaj", "tribunal": "TJSP", "usuario_login": "user1", "senha": "x",
    }, get_url="/minhas-integracoes/credenciais-tribunal/nova")

    r = client.get("/minhas-integracoes")
    assert r.status_code == 200
    assert "1 credencial" in r.data.decode("utf-8")
