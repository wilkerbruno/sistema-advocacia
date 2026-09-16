"""
Autenticação em duas etapas obrigatória (TOTP — PENDENCIAS.md, seção
-104): "toda vez que o cliente for logar deve pedir o autenticador".
Cobre: (1) conta nova cai direto na tela de configurar o QR code; (2)
conta existente sem autenticador confirmado loga normal e é direcionada
pra configuração, bloqueada de qualquer outra tela até isso ser feito
(app/__init__.py::exigir_autenticador_configurado); (3) conta com
autenticador confirmado passa a exigir o código a cada login
(app/routes/auth.py::verificar_totp); (4) reconfigurar (exige senha
atual) e reset por um admin (perda de celular).

Nenhum código de autenticador é mockado — usa `pyotp` de verdade nos
testes, gerando o código a partir do MESMO segredo que a rota gerou (via
`app/utils/totp.py`, decifrado com a chave de teste).
"""
import re

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models import Usuario
from app.utils import totp as totp_utils


def _csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


@pytest.fixture()
def totp_ativo(app):
    """Liga a funcionalidade inteira para a duração do teste — sem isso,
    `totp_disponivel()` é False e nada do fluxo de 2FA é exigido (ver
    docstring de app/utils/totp.py)."""
    chave_original = app.config.get("TOTP_CIFRA_KEY")
    app.config["TOTP_CIFRA_KEY"] = Fernet.generate_key().decode()
    yield
    app.config["TOTP_CIFRA_KEY"] = chave_original


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "duofator@teste.com", papel="advogado", nome="Advogado 2FA")
    admin_id = criar_usuario(unidade_id, "admin2fa@teste.com", papel="admin", nome="Admin 2FA")
    return dict(unidade_id=unidade_id, usuario_id=usuario_id, usuario_email="duofator@teste.com",
                admin_id=admin_id, admin_email="admin2fa@teste.com")


def _configurar_totp_direto(app, usuario_id, secret=None):
    """Configura o autenticador de um usuário direto no banco (bypassa a
    tela) — usado quando o teste não é sobre o FLUXO de configuração em
    si, só precisa de um usuário que já passou por ele. Roda dentro do
    MESMO contexto de aplicação que a fixture `app` já deixou ativo (nunca
    abre um `with app.app_context()` aninhado — isso criaria uma sessão
    do SQLAlchemy separada, escopada por um contexto diferente, que não
    enxergaria as linhas ainda não commitadas pela fixture `criar_usuario`)."""
    usuario = db.session.get(Usuario, usuario_id)
    secret = secret or totp_utils.gerar_secret()
    totp_utils.salvar_secret_pendente(usuario, secret)
    totp_utils.confirmar(usuario, secret)
    db.session.commit()
    return secret


def _login_senha(client, email, senha="senha123"):
    r = client.get("/login")
    token = _csrf(r.data.decode("utf-8"))
    return client.post("/login", data={"email": email, "senha": senha, "csrf_token": token},
                        follow_redirects=True)


def _login_completo_com_totp(client, email, secret, senha="senha123"):
    r = _login_senha(client, email, senha)
    token = _csrf(r.data.decode("utf-8"))
    codigo = pyotp.TOTP(secret).now()
    return client.post("/verificar-autenticador", data={"codigo": codigo, "csrf_token": token},
                        follow_redirects=True)


# ---------------------- Interruptor: funcionalidade desligada ----------------------

def test_login_sem_totp_cifra_key_funciona_como_antes(client, cenario):
    r = _login_senha(client, cenario["usuario_email"])
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Autenticador" not in html or "Configure o autenticador" not in html
    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.totp_configurado is False


# ---------------------- Conta existente sem autenticador ----------------------

def test_login_sem_totp_configurado_direciona_para_tela_de_configuracao(client, cenario, totp_ativo):
    r = _login_senha(client, cenario["usuario_email"])
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Configure seu autenticador" in html or "Pendente" in html
    assert "QR code" in html or "qrcode" in html.lower()


def test_gate_bloqueia_qualquer_outra_tela_ate_configurar(client, cenario, totp_ativo):
    _login_senha(client, cenario["usuario_email"])
    r = client.get("/minha-conta/preferencias", follow_redirects=True)
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # bounced de volta pra tela de configuração, nunca chega em "preferências"
    assert "Configure seu autenticador" in html or "Pendente" in html


def test_gate_nao_bloqueia_logout(client, cenario, totp_ativo):
    _login_senha(client, cenario["usuario_email"])
    r = client.get("/logout", follow_redirects=True)
    assert r.status_code == 200
    assert "Sessão encerrada" in r.data.decode("utf-8")


# ---------------------- Tela de configuração (QR code) ----------------------

def test_configurar_totp_gera_segredo_pendente_e_e_idempotente(app, client, cenario, totp_ativo):
    _login_senha(client, cenario["usuario_email"])

    r1 = client.get("/minha-conta/autenticador")
    r2 = client.get("/minha-conta/autenticador")
    secret1 = re.search(r"<code[^>]*>([^<]+)</code>", r1.data.decode("utf-8")).group(1).replace(" ", "")
    secret2 = re.search(r"<code[^>]*>([^<]+)</code>", r2.data.decode("utf-8")).group(1).replace(" ", "")
    assert secret1 == secret2  # mesmo segredo entre visitas — não troca o QR sozinho

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.totp_secret_cifrado is not None
    assert usuario.totp_configurado is False  # gerado, mas ainda não confirmado


def test_confirmar_totp_codigo_errado_mantem_pendente(app, client, cenario, totp_ativo):
    _login_senha(client, cenario["usuario_email"])
    r = client.get("/minha-conta/autenticador")
    secret = re.search(r"<code[^>]*>([^<]+)</code>", r.data.decode("utf-8")).group(1).replace(" ", "")
    token = _csrf(r.data.decode("utf-8"))

    codigo_certo = pyotp.TOTP(secret).now()
    codigo_errado = f"{(int(codigo_certo) + 1) % 1_000_000:06d}"

    resp = client.post("/minha-conta/autenticador/confirmar",
                        data={"codigo": codigo_errado, "csrf_token": token}, follow_redirects=True)
    assert "Código inválido" in resp.data.decode("utf-8")

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.totp_configurado is False


def test_confirmar_totp_codigo_correto_ativa_e_libera_o_resto_do_sistema(app, client, cenario, totp_ativo):
    _login_senha(client, cenario["usuario_email"])
    r = client.get("/minha-conta/autenticador")
    secret = re.search(r"<code[^>]*>([^<]+)</code>", r.data.decode("utf-8")).group(1).replace(" ", "")
    token = _csrf(r.data.decode("utf-8"))
    codigo = pyotp.TOTP(secret).now()

    resp = client.post("/minha-conta/autenticador/confirmar",
                        data={"codigo": codigo, "csrf_token": token}, follow_redirects=True)
    assert resp.status_code == 200
    assert "configurado com sucesso" in resp.data.decode("utf-8").lower()

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.totp_configurado is True

    # agora navega livremente — o gate não bloqueia mais
    r2 = client.get("/minha-conta/preferencias")
    assert r2.status_code == 200
    assert "Preferências do menu" in r2.data.decode("utf-8")


# ---------------------- Login com autenticador já confirmado ----------------------

def test_login_com_totp_configurado_exige_segundo_fator(app, client, cenario, totp_ativo):
    secret = _configurar_totp_direto(app, cenario["usuario_id"])
    r = _login_senha(client, cenario["usuario_email"])
    html = r.data.decode("utf-8")
    assert "Verificar e entrar" in html
    assert cenario["usuario_email"] in html

    # ainda não está autenticado de verdade (Flask-Login) — uma tela
    # protegida qualquer ainda manda pro login, não deixa passar.
    r_protegida = client.get("/minha-conta/preferencias", follow_redirects=True)
    assert "E-mail" in r_protegida.data.decode("utf-8")  # caiu na tela de login


def test_login_com_totp_codigo_correto_autentica(app, client, cenario, totp_ativo):
    secret = _configurar_totp_direto(app, cenario["usuario_id"])
    resp = _login_completo_com_totp(client, cenario["usuario_email"], secret)
    assert resp.status_code == 200

    r = client.get("/minha-conta/preferencias")
    assert r.status_code == 200
    assert "Preferências do menu" in r.data.decode("utf-8")

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.ultimo_login is not None


def test_login_com_totp_codigo_errado_nao_autentica(app, client, cenario, totp_ativo):
    secret = _configurar_totp_direto(app, cenario["usuario_id"])
    r = _login_senha(client, cenario["usuario_email"])
    token = _csrf(r.data.decode("utf-8"))

    codigo_certo = pyotp.TOTP(secret).now()
    codigo_errado = f"{(int(codigo_certo) + 1) % 1_000_000:06d}"
    resp = client.post("/verificar-autenticador", data={"codigo": codigo_errado, "csrf_token": token},
                        follow_redirects=True)
    assert "Código inválido" in resp.data.decode("utf-8")

    r2 = client.get("/minha-conta/preferencias", follow_redirects=True)
    assert "E-mail" in r2.data.decode("utf-8")  # continua não autenticado


def test_login_com_totp_muitas_tentativas_erradas_cancela_pendencia(app, client, cenario, totp_ativo):
    secret = _configurar_totp_direto(app, cenario["usuario_id"])
    r = _login_senha(client, cenario["usuario_email"])
    token = _csrf(r.data.decode("utf-8"))

    for _ in range(5):
        resp = client.post("/verificar-autenticador", data={"codigo": "000001", "csrf_token": token},
                            follow_redirects=True)

    assert "Muitas tentativas" in resp.data.decode("utf-8")
    # a sessão pendente foi limpa — tentar de novo cai direto no login
    r2 = client.get("/verificar-autenticador", follow_redirects=True)
    assert "E-mail" in r2.data.decode("utf-8")


def test_cancelar_verificacao_totp_volta_pro_login(app, client, cenario, totp_ativo):
    secret = _configurar_totp_direto(app, cenario["usuario_id"])
    _login_senha(client, cenario["usuario_email"])

    r = client.get("/verificar-autenticador/cancelar", follow_redirects=True)
    assert "E-mail" in r.data.decode("utf-8")

    r2 = client.get("/verificar-autenticador", follow_redirects=True)
    assert "E-mail" in r2.data.decode("utf-8")  # pendência realmente foi limpa


# ---------------------- Reconfigurar (perdeu o celular, trocou) ----------------------

def test_reconfigurar_totp_exige_senha_atual_correta(app, client, cenario, totp_ativo):
    secret = _configurar_totp_direto(app, cenario["usuario_id"])
    _login_completo_com_totp(client, cenario["usuario_email"], secret)

    r = client.get("/minha-conta/autenticador")
    token = _csrf(r.data.decode("utf-8"))

    resp_errada = client.post("/minha-conta/autenticador/reconfigurar",
                               data={"senha_atual": "senha-errada", "csrf_token": token}, follow_redirects=True)
    assert "incorreta" in resp_errada.data.decode("utf-8").lower()
    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.totp_configurado is True  # não mexeu em nada

    resp_certa = client.post("/minha-conta/autenticador/reconfigurar",
                              data={"senha_atual": "senha123", "csrf_token": token}, follow_redirects=True)
    assert resp_certa.status_code == 200
    db.session.expire_all()
    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.totp_configurado is False  # precisa configurar de novo (novo QR)


# ---------------------- Admin reseta o de outro usuário (perdeu o celular) ----------------------

def test_admin_reseta_autenticador_de_outro_usuario(app, client, cenario, totp_ativo):
    secret_alvo = _configurar_totp_direto(app, cenario["usuario_id"])
    secret_admin = _configurar_totp_direto(app, cenario["admin_id"])
    _login_completo_com_totp(client, cenario["admin_email"], secret_admin)

    r = client.get(f"/admin/usuarios/{cenario['usuario_id']}/editar")
    token = _csrf(r.data.decode("utf-8"))

    resp = client.post(f"/admin/usuarios/{cenario['usuario_id']}/editar", data={
        "csrf_token": token, "nome": "Advogado 2FA", "papel": "advogado",
        "unidade_id": str(cenario["unidade_id"]), "ativo": "on", "resetar_totp": "on",
    }, follow_redirects=True)
    assert resp.status_code == 200

    alvo = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert alvo.totp_configurado is False
    assert alvo.totp_secret_cifrado is None

    # o próprio admin não foi afetado
    admin = Usuario.query.filter_by(email=cenario["admin_email"]).first()
    assert admin.totp_configurado is True


# ---------------------- Cadastro de empresa nova ----------------------

def test_cadastro_empresa_com_totp_ligado_direciona_direto_para_configuracao(client, totp_ativo):
    r = client.get("/cadastrar-empresa")
    token = _csrf(r.data.decode("utf-8"))

    resp = client.post("/cadastrar-empresa", data={
        "csrf_token": token, "empresa_nome": "Escritório Novo 2FA", "admin_nome": "Sócio Fundador",
        "admin_email": "fundador2fa@teste.com", "admin_senha": "senha123", "plano": "mensal",
    }, follow_redirects=True)

    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "Configure seu autenticador" in html or "Pendente" in html

    usuario = Usuario.query.filter_by(email="fundador2fa@teste.com").first()
    assert usuario is not None
    assert usuario.totp_configurado is False
