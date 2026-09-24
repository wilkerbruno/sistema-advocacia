"""
Testes dos métodos novos de cliente_api.py usados por autenticador_local.py
— status_autenticador() e verificar_autenticador() — com `requests`
sempre mockado (nunca bate em servidor nenhum de verdade).
"""
from unittest.mock import patch, MagicMock

import pytest
import requests

from cliente_api import ClienteJusControl, ErroApiJusControl, ErroTokenInvalido, ErroAutenticadorBloqueado


def _resp(status=200, json_body=None, content_type="application/json"):
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": content_type}
    r.json.return_value = json_body or {}
    r.text = str(json_body or {})
    return r


@pytest.fixture()
def cliente():
    return ClienteJusControl("https://jc.exemplo.com", "token-de-teste")


# ---------------------------------------------------------------------------
# status_autenticador
# ---------------------------------------------------------------------------

def test_status_autenticador_exigido_true(cliente):
    with patch("cliente_api.requests.get", return_value=_resp(200, {"exigido": True})):
        assert cliente.status_autenticador() == {"exigido": True}


def test_status_autenticador_exigido_false(cliente):
    with patch("cliente_api.requests.get", return_value=_resp(200, {"exigido": False})):
        assert cliente.status_autenticador() == {"exigido": False}


def test_status_autenticador_token_invalido(cliente):
    with patch("cliente_api.requests.get", return_value=_resp(401, {"erro": "Token de agente local inválido..."})):
        with pytest.raises(ErroTokenInvalido):
            cliente.status_autenticador()


def test_status_autenticador_sem_conexao(cliente):
    with patch("cliente_api.requests.get", side_effect=requests.ConnectionError("sem rede")):
        with pytest.raises(ErroApiJusControl):
            cliente.status_autenticador()


def test_status_autenticador_erro_generico_servidor(cliente):
    with patch("cliente_api.requests.get", return_value=_resp(500, {})):
        with pytest.raises(ErroApiJusControl):
            cliente.status_autenticador()


# ---------------------------------------------------------------------------
# verificar_autenticador
# ---------------------------------------------------------------------------

def test_verificar_autenticador_codigo_correto(cliente):
    with patch("cliente_api.requests.post", return_value=_resp(200, {"ok": True})):
        assert cliente.verificar_autenticador("123456") is True


def test_verificar_autenticador_codigo_errado_nao_eh_token_invalido(cliente):
    """401 com corpo contendo "ok": False é CÓDIGO errado, não token
    inválido — não deve levantar ErroTokenInvalido (ver docstring do
    método, distinção pela presença da chave "ok")."""
    with patch("cliente_api.requests.post", return_value=_resp(401, {"ok": False, "erro": "Código inválido"})):
        assert cliente.verificar_autenticador("000000") is False


def test_verificar_autenticador_token_invalido(cliente):
    """401 SEM a chave "ok" no corpo — veio de `exige_agente`, é o token
    de pareamento em si que é ruim."""
    with patch("cliente_api.requests.post",
               return_value=_resp(401, {"erro": "Token de agente local inválido, ausente ou revogado."})):
        with pytest.raises(ErroTokenInvalido):
            cliente.verificar_autenticador("123456")


def test_verificar_autenticador_bloqueado(cliente):
    with patch("cliente_api.requests.post",
               return_value=_resp(429, {"ok": False, "erro": "Muitas tentativas erradas — tente de novo em 300 segundo(s)."})):
        with pytest.raises(ErroAutenticadorBloqueado, match="Muitas tentativas"):
            cliente.verificar_autenticador("123456")


def test_verificar_autenticador_sem_conexao(cliente):
    with patch("cliente_api.requests.post", side_effect=requests.ConnectionError("sem rede")):
        with pytest.raises(ErroApiJusControl):
            cliente.verificar_autenticador("123456")


def test_verificar_autenticador_erro_generico_servidor(cliente):
    with patch("cliente_api.requests.post", return_value=_resp(500, {})):
        with pytest.raises(ErroApiJusControl):
            cliente.verificar_autenticador("123456")
