"""
Testa a entrega do instalador do Agente Local quando o repositório do
JusControl é PRIVADO (PENDENCIAS.md, seção -58): o servidor busca o
instalador na API do GitHub usando um token só de leitura (nunca exposto
ao navegador do advogado) e entrega os bytes direto — sem o advogado
nunca acessar o GitHub.

Dividido em duas partes:
  - `app/utils/instalador_agente_local.py` isolado, com `requests.get`
    trocado por um fake (nunca bate na internet de verdade) — confirma
    que a busca da última Release e o download do asset (inclusive
    seguindo o redirecionamento do GitHub SEM levar o token junto)
    funcionam e tratam cada erro esperado com uma mensagem legível.
  - A rota `/agente-local/baixar` (app/routes/agente_local.py), com o
    módulo acima trocado por um fake — confirma que o modo "repositório
    privado" tem prioridade sobre o link direto (repositório público)
    quando os dois estão configurados, e que os dois modos continuam
    funcionando cada um por si.
"""
from unittest.mock import patch, Mock

import pytest

from app.extensions import db
import app.utils.instalador_agente_local as instalador_mod
from app.utils.instalador_agente_local import (
    localizar_asset_da_ultima_release, baixar_bytes_do_asset, InstaladorIndisponivelError,
)


# ---------------------- instalador_agente_local.py (unitário) ----------------------

def _resposta_fake(status_code, json_data=None, headers=None):
    r = Mock()
    r.status_code = status_code
    r.json.return_value = json_data or {}
    r.headers = headers or {}
    r.content = b"conteudo binario fake do instalador"
    return r


def test_localizar_asset_encontra_pelo_nome():
    resposta = _resposta_fake(200, json_data={
        "assets": [
            {"id": 111, "name": "outro-arquivo.txt"},
            {"id": 222, "name": "JusControlAgente-Setup.exe"},
        ]
    })
    with patch.object(instalador_mod.requests, "get", return_value=resposta) as m:
        asset_id, nome = localizar_asset_da_ultima_release("dono/repo", "token-fake")
    assert asset_id == 222
    assert nome == "JusControlAgente-Setup.exe"
    m.assert_called_once()
    assert m.call_args.kwargs["headers"]["Authorization"] == "Bearer token-fake"


def test_localizar_asset_sem_release_publicada():
    with patch.object(instalador_mod.requests, "get", return_value=_resposta_fake(404)):
        with pytest.raises(InstaladorIndisponivelError, match="Nenhuma Release"):
            localizar_asset_da_ultima_release("dono/repo", "token-fake")


def test_localizar_asset_token_invalido():
    with patch.object(instalador_mod.requests, "get", return_value=_resposta_fake(401)):
        with pytest.raises(InstaladorIndisponivelError, match="rejeitado"):
            localizar_asset_da_ultima_release("dono/repo", "token-ruim")


def test_localizar_asset_sem_permissao():
    with patch.object(instalador_mod.requests, "get", return_value=_resposta_fake(403)):
        with pytest.raises(InstaladorIndisponivelError, match="recusou o acesso"):
            localizar_asset_da_ultima_release("dono/repo", "token-fake")


def test_localizar_asset_release_sem_o_arquivo_esperado():
    resposta = _resposta_fake(200, json_data={"assets": [{"id": 1, "name": "outra-coisa.zip"}]})
    with patch.object(instalador_mod.requests, "get", return_value=resposta):
        with pytest.raises(InstaladorIndisponivelError, match="JusControlAgente-Setup.exe"):
            localizar_asset_da_ultima_release("dono/repo", "token-fake")


def test_baixar_asset_segue_redirecionamento_sem_levar_o_token():
    resposta_redirect = _resposta_fake(302, headers={"Location": "https://objects.githubusercontent.com/arquivo-real"})
    resposta_final = _resposta_fake(200, headers={"Content-Type": "application/octet-stream"})

    chamadas = []

    def _fake_get(url, headers=None, timeout=None, allow_redirects=None):
        chamadas.append((url, headers))
        if url.startswith("https://api.github.com"):
            return resposta_redirect
        return resposta_final

    with patch.object(instalador_mod.requests, "get", side_effect=_fake_get):
        conteudo, content_type = baixar_bytes_do_asset("dono/repo", "token-fake", 222)

    assert conteudo == b"conteudo binario fake do instalador"
    assert content_type == "application/octet-stream"
    # 2 chamadas: a 1ª pro GitHub (com Authorization), a 2ª pro storage real (sem Authorization).
    assert len(chamadas) == 2
    assert chamadas[0][1]["Authorization"] == "Bearer token-fake"
    assert chamadas[1][1] is None  # baixar_bytes_do_asset chama requests.get(localizacao, timeout=60) sem headers


def test_baixar_asset_responde_200_direto_sem_redirecionar():
    resposta = _resposta_fake(200, headers={"Content-Type": "application/vnd.octet-stream"})
    with patch.object(instalador_mod.requests, "get", return_value=resposta):
        conteudo, content_type = baixar_bytes_do_asset("dono/repo", "token-fake", 222)
    assert conteudo == b"conteudo binario fake do instalador"


def test_baixar_asset_erro_no_download_final():
    resposta_redirect = _resposta_fake(302, headers={"Location": "https://objects.githubusercontent.com/x"})
    resposta_erro = _resposta_fake(500)

    def _fake_get(url, headers=None, timeout=None, allow_redirects=None):
        return resposta_redirect if url.startswith("https://api.github.com") else resposta_erro

    with patch.object(instalador_mod.requests, "get", side_effect=_fake_get):
        with pytest.raises(InstaladorIndisponivelError, match="Download falhou"):
            baixar_bytes_do_asset("dono/repo", "token-fake", 222)


# ---------------------- Rota /agente-local/baixar (integração) ----------------------

@pytest.fixture()
def usuario_logado(app, empresa_basica, criar_usuario, login):
    unidade_id = empresa_basica["unidade_id"]
    criar_usuario(unidade_id, "advinstalador@teste.com", papel="advogado", nome="Advogado Instalador")
    login("advinstalador@teste.com")
    return unidade_id


def test_rota_baixar_usa_proxy_privado_quando_configurado(client, usuario_logado, app):
    app.config["AGENTE_LOCAL_GITHUB_REPO"] = "dono/repo"
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = "token-fake"
    app.config["AGENTE_LOCAL_INSTALADOR_URL"] = ""  # mesmo com os dois "configuráveis", só o privado está setado

    with patch("app.routes.agente_local.instalador_agente_local.localizar_asset_da_ultima_release",
               return_value=(222, "JusControlAgente-Setup.exe")), \
         patch("app.routes.agente_local.instalador_agente_local.baixar_bytes_do_asset",
               return_value=(b"bytes do exe fake", "application/octet-stream")):
        r = client.get("/agente-local/baixar")

    app.config["AGENTE_LOCAL_GITHUB_REPO"] = ""
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = ""

    assert r.status_code == 200
    assert r.data == b"bytes do exe fake"
    assert "JusControlAgente-Setup.exe" in r.headers["Content-Disposition"]


def test_rota_baixar_prioriza_proxy_privado_sobre_link_publico(client, usuario_logado, app):
    """Quando as DUAS formas estão configuradas ao mesmo tempo, o modo
    repositório privado (mais seguro, não expõe código-fonte) tem
    prioridade — nunca cai pro redirect público por engano."""
    app.config["AGENTE_LOCAL_GITHUB_REPO"] = "dono/repo"
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = "token-fake"
    app.config["AGENTE_LOCAL_INSTALADOR_URL"] = "https://github.com/dono/repo/releases/latest/download/x.exe"

    with patch("app.routes.agente_local.instalador_agente_local.localizar_asset_da_ultima_release",
               return_value=(222, "JusControlAgente-Setup.exe")) as m_localizar, \
         patch("app.routes.agente_local.instalador_agente_local.baixar_bytes_do_asset",
               return_value=(b"bytes do exe fake", "application/octet-stream")):
        r = client.get("/agente-local/baixar", follow_redirects=False)

    app.config["AGENTE_LOCAL_GITHUB_REPO"] = ""
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = ""
    app.config["AGENTE_LOCAL_INSTALADOR_URL"] = ""

    assert r.status_code == 200  # não é um redirect (302) — o proxy privado respondeu direto
    m_localizar.assert_called_once()


def test_rota_baixar_com_erro_do_proxy_avisa_e_redireciona(client, usuario_logado, app):
    app.config["AGENTE_LOCAL_GITHUB_REPO"] = "dono/repo"
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = "token-fake"

    with patch("app.routes.agente_local.instalador_agente_local.localizar_asset_da_ultima_release",
               side_effect=InstaladorIndisponivelError("Nenhuma Release publicada ainda.")):
        r = client.get("/agente-local/baixar", follow_redirects=True)

    app.config["AGENTE_LOCAL_GITHUB_REPO"] = ""
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = ""

    assert r.status_code == 200
    assert "Nenhuma Release publicada ainda." in r.data.decode("utf-8")


def test_botao_instalador_aparece_com_config_de_repo_privado(client, usuario_logado, app):
    app.config["AGENTE_LOCAL_GITHUB_REPO"] = "dono/repo"
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = "token-fake"

    r = client.get("/agente-local")

    app.config["AGENTE_LOCAL_GITHUB_REPO"] = ""
    app.config["AGENTE_LOCAL_GITHUB_TOKEN"] = ""

    assert "Baixar agente local" in r.data.decode("utf-8")
