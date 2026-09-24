"""
Autenticador (2FA) pra abrir a tela de Configuração do Agente Local
(pedido do usuário — ver app/models/agente_local.py para a explicação das
três camadas de credencial e PENDENCIAS.md, seção mais recente).

Cobre os dois endpoints novos em app/routes/agente_local_api.py:
  - GET  /api/agente-local/status-autenticador — o agente consulta isso
    ANTES de pedir um código, pra saber se vale a pena pedir algo.
  - POST /api/agente-local/verificar-autenticador — confere o código
    contra o MESMO segredo TOTP da conta web do usuário (nunca um
    segredo separado), com trava de tentativas por agente.

Não mockamos nenhum código de autenticador — gera com `pyotp` de verdade
a partir do segredo real do usuário, mesmo padrão de
tests/test_autenticador_dois_fatores.py.
"""
from datetime import datetime, timedelta

import pyotp
import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models import AgenteLocalPareado, Usuario
from app.utils import totp as totp_utils


@pytest.fixture()
def totp_ativo(app):
    """Liga a funcionalidade de autenticador inteira pra duração do teste
    — sem isso, `totp_disponivel()` é False e nenhum código é exigido
    (mesmo princípio de tests/test_autenticador_dois_fatores.py)."""
    chave_original = app.config.get("TOTP_CIFRA_KEY")
    app.config["TOTP_CIFRA_KEY"] = Fernet.generate_key().decode()
    yield
    app.config["TOTP_CIFRA_KEY"] = chave_original


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "advagente2fa@teste.com", papel="advogado", nome="Advogado Agente 2FA")
    return dict(adv_id=adv_id, unidade_id=unidade_id)


def _configurar_totp_direto(usuario_id, secret=None):
    usuario = db.session.get(Usuario, usuario_id)
    secret = secret or totp_utils.gerar_secret()
    totp_utils.salvar_secret_pendente(usuario, secret)
    totp_utils.confirmar(usuario, secret)
    db.session.commit()
    return secret


@pytest.fixture()
def agente_autenticado(app, cenario):
    usuario = db.session.get(Usuario, cenario["adv_id"])
    registro, valor_puro = AgenteLocalPareado.emitir_para(usuario, "Notebook de teste 2FA")
    db.session.commit()
    return dict(token=valor_puro, agente_id=registro.id,
                headers={"Authorization": f"Bearer {valor_puro}"})


# ---------------------------------------------------------------------------
# status-autenticador
# ---------------------------------------------------------------------------

def test_status_nao_exige_quando_totp_desligado_no_sistema(client, cenario, agente_autenticado):
    # totp_ativo NÃO aplicada — TOTP_CIFRA_KEY não configurada neste teste
    r = client.get("/api/agente-local/status-autenticador", headers=agente_autenticado["headers"])
    assert r.status_code == 200
    assert r.get_json()["exigido"] is False


def test_status_nao_exige_quando_usuario_nao_confirmou_totp(app, client, cenario, agente_autenticado, totp_ativo):
    r = client.get("/api/agente-local/status-autenticador", headers=agente_autenticado["headers"])
    assert r.status_code == 200
    assert r.get_json()["exigido"] is False


def test_status_exige_quando_totp_ligado_e_confirmado(app, client, cenario, agente_autenticado, totp_ativo):
    _configurar_totp_direto(cenario["adv_id"])
    r = client.get("/api/agente-local/status-autenticador", headers=agente_autenticado["headers"])
    assert r.status_code == 200
    assert r.get_json()["exigido"] is True


def test_status_exige_token_valido(client, cenario):
    r = client.get("/api/agente-local/status-autenticador")
    assert r.status_code == 401
    r2 = client.get("/api/agente-local/status-autenticador", headers={"Authorization": "Bearer invalido"})
    assert r2.status_code == 401


# ---------------------------------------------------------------------------
# verificar-autenticador
# ---------------------------------------------------------------------------

def test_verificar_ok_direto_quando_nao_exigido(client, cenario, agente_autenticado):
    r = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                     json={"codigo": ""})
    assert r.status_code == 200
    corpo = r.get_json()
    assert corpo["ok"] is True
    assert corpo["exigido"] is False


def test_verificar_codigo_correto(app, client, cenario, agente_autenticado, totp_ativo):
    secret = _configurar_totp_direto(cenario["adv_id"])
    codigo = pyotp.TOTP(secret).now()

    r = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                     json={"codigo": codigo})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_verificar_codigo_errado(app, client, cenario, agente_autenticado, totp_ativo):
    _configurar_totp_direto(cenario["adv_id"])
    r = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                     json={"codigo": "000000"})
    assert r.status_code == 401
    assert r.get_json()["ok"] is False

    registro = db.session.get(AgenteLocalPareado, agente_autenticado["agente_id"])
    assert registro.totp_falhas_consecutivas == 1


def test_verificar_bloqueia_apos_5_tentativas_erradas(app, client, cenario, agente_autenticado, totp_ativo):
    _configurar_totp_direto(cenario["adv_id"])

    for _ in range(5):
        r = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                         json={"codigo": "000000"})
        assert r.status_code == 401

    registro = db.session.get(AgenteLocalPareado, agente_autenticado["agente_id"])
    assert registro.totp_falhas_consecutivas == 0  # zerada ao bloquear
    assert registro.totp_bloqueado_ate is not None
    assert registro.totp_bloqueado_ate > datetime.utcnow()

    # mesmo com o código CERTO, continua bloqueado até o tempo passar
    secret = totp_utils.obter_secret_pendente_ou_confirmado(db.session.get(Usuario, cenario["adv_id"]))
    codigo_certo = pyotp.TOTP(secret).now()
    r_bloqueado = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                               json={"codigo": codigo_certo})
    assert r_bloqueado.status_code == 429


def test_verificar_codigo_certo_reseta_falhas_anteriores(app, client, cenario, agente_autenticado, totp_ativo):
    secret = _configurar_totp_direto(cenario["adv_id"])

    client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                json={"codigo": "000000"})
    registro = db.session.get(AgenteLocalPareado, agente_autenticado["agente_id"])
    assert registro.totp_falhas_consecutivas == 1

    codigo_certo = pyotp.TOTP(secret).now()
    r = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                     json={"codigo": codigo_certo})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    db.session.refresh(registro)
    assert registro.totp_falhas_consecutivas == 0
    assert registro.totp_bloqueado_ate is None


def test_verificar_desbloqueia_sozinho_depois_do_tempo_passar(app, client, cenario, agente_autenticado, totp_ativo):
    secret = _configurar_totp_direto(cenario["adv_id"])
    registro = db.session.get(AgenteLocalPareado, agente_autenticado["agente_id"])
    registro.totp_bloqueado_ate = datetime.utcnow() - timedelta(seconds=1)  # bloqueio já expirado
    db.session.commit()

    codigo_certo = pyotp.TOTP(secret).now()
    r = client.post("/api/agente-local/verificar-autenticador", headers=agente_autenticado["headers"],
                     json={"codigo": codigo_certo})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_verificar_exige_token_valido(client, cenario):
    r = client.post("/api/agente-local/verificar-autenticador", json={"codigo": "123456"})
    assert r.status_code == 401


def test_verificar_nunca_mistura_bloqueio_entre_agentes_diferentes(app, client, cenario, totp_ativo):
    """Dois notebooks pareados do MESMO advogado — a trava de tentativas é
    por AGENTE, não por usuário (ver docstring de
    AgenteLocalPareado.registrar_falha_totp)."""
    usuario = db.session.get(Usuario, cenario["adv_id"])
    secret = _configurar_totp_direto(cenario["adv_id"])
    reg_a, token_a = AgenteLocalPareado.emitir_para(usuario, "Notebook A")
    reg_b, token_b = AgenteLocalPareado.emitir_para(usuario, "Notebook B")
    db.session.commit()

    for _ in range(5):
        client.post("/api/agente-local/verificar-autenticador",
                     headers={"Authorization": f"Bearer {token_a}"}, json={"codigo": "000000"})

    reg_a_atualizado = db.session.get(AgenteLocalPareado, reg_a.id)
    assert reg_a_atualizado.totp_bloqueado() is True

    # Notebook B, mesmo advogado, mesmo segredo — continua livre
    codigo_certo = pyotp.TOTP(secret).now()
    r_b = client.post("/api/agente-local/verificar-autenticador",
                       headers={"Authorization": f"Bearer {token_b}"}, json={"codigo": codigo_certo})
    assert r_b.status_code == 200
    assert r_b.get_json()["ok"] is True
