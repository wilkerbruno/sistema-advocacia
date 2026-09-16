"""
"Esqueci minha senha" (PENDENCIAS.md, seção -104): e-mail -> código de 6
dígitos -> nova senha (+ confirmação), com a política de força exigida
(mín. 8 caracteres, 1 maiúscula, 1 minúscula, 1 número, 1 caractere
especial). Cobre também o cuidado contra enumeração de e-mail (mesma
mensagem genérica, exista ou não a conta) e o limite de tentativas
erradas do código.

Nenhum e-mail de verdade é enviado — `app.utils.senha_redefinicao.enviar_email`
é sempre mockado (ver fixture `smtp_ativo`); SMTP "configurado" aqui só
significa que `app.config["SMTP_HOST"/...]` está preenchido o bastante
para `smtp_configurado()` (checagem real, não mockada) devolver True.
"""
import re

import pytest

from app.extensions import db
from app.models import Usuario
import app.utils.senha_redefinicao as senha_redefinicao_mod
from app.utils.senha_politica import validar_forca_senha


def _csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "resetsenha@teste.com", papel="advogado", nome="Advogado Reset")
    return dict(unidade_id=unidade_id, usuario_id=usuario_id, usuario_email="resetsenha@teste.com")


@pytest.fixture()
def smtp_ativo(app, monkeypatch):
    """`smtp_configurado()` de verdade (não mockada) passa a devolver True
    (SMTP_HOST/USER/PASSWORD preenchidos); só o ENVIO de verdade
    (`enviar_email`, que abriria um socket SMTP) é mockado, capturando
    cada e-mail "enviado" nesta lista pro teste inspecionar o código."""
    app.config["SMTP_HOST"] = "smtp.teste.local"
    app.config["SMTP_USER"] = "usuario@teste.local"
    app.config["SMTP_PASSWORD"] = "senha-smtp"

    enviados = []

    def _fake_enviar_email(destinatario, assunto, corpo_texto):
        enviados.append({"destinatario": destinatario, "assunto": assunto, "corpo": corpo_texto})
        return True

    monkeypatch.setattr(senha_redefinicao_mod, "enviar_email", _fake_enviar_email)
    return enviados


def _extrair_codigo(corpo_email):
    m = re.search(r"\b(\d{6})\b", corpo_email)
    return m.group(1) if m else None


def _post(client, url, data, get_url=None):
    r = client.get(get_url or url)
    token = _csrf(r.data.decode("utf-8"))
    payload = dict(data)
    payload["csrf_token"] = token
    return client.post(url, data=payload, follow_redirects=True)


# ---------------------- validar_forca_senha (unidade) ----------------------

def test_validar_forca_senha_aceita_senha_que_cumpre_tudo():
    assert validar_forca_senha("Abcdefg1!") == []


@pytest.mark.parametrize("senha,fragmento_erro", [
    ("Abc1!", "8 caracteres"),
    ("abcdefg1!", "MAIÚSCULA"),
    ("ABCDEFG1!", "minúscula"),
    ("Abcdefgh!", "número"),
    ("Abcdefg12", "especial"),
    ("", "8 caracteres"),
])
def test_validar_forca_senha_recusa_cada_requisito_faltando(senha, fragmento_erro):
    erros = validar_forca_senha(senha)
    assert any(fragmento_erro in e for e in erros)


# ---------------------- Passo 1: informar e-mail ----------------------

def test_esqueci_senha_sem_smtp_configurado_avisa_e_nao_avanca(client, cenario):
    resp = _post(client, "/esqueci-senha", {"email": cenario["usuario_email"]})
    assert resp.status_code == 200
    assert "não está configurado" in resp.data.decode("utf-8")

    # sem sessão de reset iniciada — a etapa 2 manda de volta pra etapa 1
    r2 = client.get("/esqueci-senha/verificar", follow_redirects=True)
    assert "e-mail" in r2.data.decode("utf-8").lower()
    assert "código" not in r2.data.decode("utf-8").lower() or "Informe o e-mail" in r2.data.decode("utf-8")


def test_esqueci_senha_email_existente_envia_codigo_e_mostra_mensagem_generica(client, cenario, smtp_ativo):
    resp = _post(client, "/esqueci-senha", {"email": cenario["usuario_email"]})
    assert resp.status_code == 200
    assert "Se este e-mail estiver cadastrado" in resp.data.decode("utf-8")
    assert len(smtp_ativo) == 1
    assert smtp_ativo[0]["destinatario"] == cenario["usuario_email"]
    assert _extrair_codigo(smtp_ativo[0]["corpo"]) is not None

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.reset_senha_codigo_hash is not None
    assert usuario.reset_senha_expira_em is not None
    assert usuario.reset_senha_tentativas == 0


def test_esqueci_senha_email_inexistente_mesma_mensagem_generica_sem_enviar(client, smtp_ativo):
    resp = _post(client, "/esqueci-senha", {"email": "ninguem-existe@teste.com"})
    assert resp.status_code == 200
    assert "Se este e-mail estiver cadastrado" in resp.data.decode("utf-8")
    assert len(smtp_ativo) == 0  # nunca tenta enviar pra e-mail que não existe

    # a etapa seguinte é alcançável do mesmo jeito — indistinguível de fora
    r2 = client.get("/esqueci-senha/verificar")
    assert r2.status_code == 200
    assert "ninguem-existe@teste.com" in r2.data.decode("utf-8")


# ---------------------- Passo 2: verificar código ----------------------

def test_verificar_codigo_correto_avanca_para_nova_senha(client, cenario, smtp_ativo):
    _post(client, "/esqueci-senha", {"email": cenario["usuario_email"]})
    codigo = _extrair_codigo(smtp_ativo[0]["corpo"])

    resp = _post(client, "/esqueci-senha/verificar", {"codigo": codigo}, get_url="/esqueci-senha/verificar")
    assert resp.status_code == 200
    assert "Nova senha" in resp.data.decode("utf-8") or "nova senha" in resp.data.decode("utf-8").lower()


def test_verificar_codigo_errado_mostra_erro_e_nao_avanca(client, cenario, smtp_ativo):
    _post(client, "/esqueci-senha", {"email": cenario["usuario_email"]})
    codigo_certo = _extrair_codigo(smtp_ativo[0]["corpo"])
    codigo_errado = f"{(int(codigo_certo) + 1) % 1_000_000:06d}"

    resp = _post(client, "/esqueci-senha/verificar", {"codigo": codigo_errado}, get_url="/esqueci-senha/verificar")
    assert "Código inválido" in resp.data.decode("utf-8")

    # nunca chega na tela de nova senha sem verificar antes
    r2 = client.get("/esqueci-senha/nova-senha", follow_redirects=True)
    assert "e-mail" in r2.data.decode("utf-8").lower()


def test_verificar_codigo_para_email_que_nunca_existiu_da_erro_generico_sem_derrubar(client, smtp_ativo):
    _post(client, "/esqueci-senha", {"email": "fantasma@teste.com"})
    resp = _post(client, "/esqueci-senha/verificar", {"codigo": "123456"}, get_url="/esqueci-senha/verificar")
    assert resp.status_code == 200
    assert "Nenhum código pendente" in resp.data.decode("utf-8") or "Código inválido" in resp.data.decode("utf-8")


def test_verificar_codigo_muitas_tentativas_bloqueia_mesmo_o_codigo_certo(client, cenario, smtp_ativo):
    _post(client, "/esqueci-senha", {"email": cenario["usuario_email"]})
    codigo_certo = _extrair_codigo(smtp_ativo[0]["corpo"])
    codigo_errado = f"{(int(codigo_certo) + 1) % 1_000_000:06d}"

    for _ in range(5):
        resp = _post(client, "/esqueci-senha/verificar", {"codigo": codigo_errado}, get_url="/esqueci-senha/verificar")

    # depois de 5 tentativas erradas, nem o código CERTO funciona mais
    resp_final = _post(client, "/esqueci-senha/verificar", {"codigo": codigo_certo}, get_url="/esqueci-senha/verificar")
    assert "Muitas tentativas" in resp_final.data.decode("utf-8")


def test_reenviar_codigo_gera_novo_codigo_e_reseta_tentativas(client, cenario, smtp_ativo):
    _post(client, "/esqueci-senha", {"email": cenario["usuario_email"]})
    codigo1 = _extrair_codigo(smtp_ativo[0]["corpo"])

    resp = _post(client, "/esqueci-senha/reenviar", {}, get_url="/esqueci-senha/verificar")
    assert resp.status_code == 200
    assert len(smtp_ativo) == 2
    codigo2 = _extrair_codigo(smtp_ativo[1]["corpo"])

    # o código antigo não vale mais depois do reenvio
    r_codigo_antigo = _post(client, "/esqueci-senha/verificar", {"codigo": codigo1}, get_url="/esqueci-senha/verificar")
    assert "Código inválido" in r_codigo_antigo.data.decode("utf-8")

    r_codigo_novo = _post(client, "/esqueci-senha/verificar", {"codigo": codigo2}, get_url="/esqueci-senha/verificar")
    assert "nova senha" in r_codigo_novo.data.decode("utf-8").lower()


# ---------------------- Passo 3: nova senha ----------------------

def test_nova_senha_exige_verificacao_previa(client, cenario):
    resp = client.get("/esqueci-senha/nova-senha", follow_redirects=True)
    assert resp.status_code == 200
    assert "expirou" in resp.data.decode("utf-8").lower() or "e-mail" in resp.data.decode("utf-8").lower()


def _chegar_na_etapa_nova_senha(client, email, smtp_ativo):
    _post(client, "/esqueci-senha", {"email": email})
    codigo = _extrair_codigo(smtp_ativo[-1]["corpo"])
    _post(client, "/esqueci-senha/verificar", {"codigo": codigo}, get_url="/esqueci-senha/verificar")


def test_nova_senha_recusa_senha_fraca(client, cenario, smtp_ativo):
    _chegar_na_etapa_nova_senha(client, cenario["usuario_email"], smtp_ativo)

    resp = _post(client, "/esqueci-senha/nova-senha",
                 {"nova_senha": "fraca123", "confirmar_senha": "fraca123"},
                 get_url="/esqueci-senha/nova-senha")
    assert "MAIÚSCULA" in resp.data.decode("utf-8")

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.checar_senha("fraca123") is False  # não mudou


def test_nova_senha_recusa_confirmacao_diferente(client, cenario, smtp_ativo):
    _chegar_na_etapa_nova_senha(client, cenario["usuario_email"], smtp_ativo)

    resp = _post(client, "/esqueci-senha/nova-senha",
                 {"nova_senha": "SenhaForte1!", "confirmar_senha": "SenhaForte2!"},
                 get_url="/esqueci-senha/nova-senha")
    assert "confirmação" in resp.data.decode("utf-8").lower()


def test_nova_senha_sucesso_permite_login_com_a_nova_e_nao_mais_com_a_antiga(client, cenario, smtp_ativo):
    _chegar_na_etapa_nova_senha(client, cenario["usuario_email"], smtp_ativo)

    resp = _post(client, "/esqueci-senha/nova-senha",
                 {"nova_senha": "SenhaForte1!", "confirmar_senha": "SenhaForte1!"},
                 get_url="/esqueci-senha/nova-senha")
    assert resp.status_code == 200
    assert "redefinida com sucesso" in resp.data.decode("utf-8").lower()

    usuario = Usuario.query.filter_by(email=cenario["usuario_email"]).first()
    assert usuario.checar_senha("SenhaForte1!") is True
    assert usuario.checar_senha("senha123") is False
    assert usuario.reset_senha_codigo_hash is None  # código consumido, não reutilizável

    # login de verdade com a senha nova funciona
    r = client.get("/login")
    token = _csrf(r.data.decode("utf-8"))
    r_login = client.post("/login", data={"email": cenario["usuario_email"], "senha": "SenhaForte1!",
                                           "csrf_token": token}, follow_redirects=True)
    assert "E-mail ou senha inválidos" not in r_login.data.decode("utf-8")
