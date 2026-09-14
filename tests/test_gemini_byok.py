"""
Gemini (Google) como terceiro provedor BYOK do Agente de IA, ao lado do
modelo local gratuito e do Claude BYOK (ver app/utils/gemini_api.py,
app/utils/agente_ia_router.py e app/routes/integracoes.py).

Três frentes cobertas, igual à cobertura que já existia implicitamente
para o Claude BYOK (nunca testada até aqui — ver PENDENCIAS.md):
1. app/utils/gemini_api.py isolado — request/response da API do Gemini,
   toda chamada de rede mockada (`requests.post`), incluindo os casos de
   erro (chave inválida, sem permissão/faturamento, modelo inexistente,
   limite de taxa, conteúdo bloqueado pelos filtros do Google, falha de
   rede).
2. app/utils/agente_ia_router.py com uma empresa configurada para Gemini
   BYOK — decide certo entre os três provedores sem quem chama precisar
   saber a diferença.
3. app/routes/integracoes.py — a tela "Minhas Integrações" salva/remove a
   chave do Gemini cifrada no cofre (precisa de COFRE_SENHA_PROCESSO_KEY
   configurada; nenhum teste existente configurava isso ainda, então esta
   suíte gera uma chave Fernet só para a duração do teste).
"""
from unittest.mock import patch, Mock

import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models import Empresa
from app.utils import gemini_api, cofre
import app.utils.agente_ia_router as router_mod


# ---------------------------------------------------------------------
# 1. app/utils/gemini_api.py isolado
# ---------------------------------------------------------------------

def _resposta_ok(texto="Olá, tudo certo."):
    m = Mock()
    m.status_code = 200
    m.json.return_value = {"candidates": [{"content": {"role": "model", "parts": [{"text": texto}]}}]}
    return m


def test_gemini_sem_chave_nao_chama_rede():
    with patch("app.utils.gemini_api.requests.post") as m_post:
        with pytest.raises(gemini_api.GeminiIndisponivelError):
            gemini_api.gerar_resposta("system", [{"role": "user", "content": "oi"}], api_key="")
    m_post.assert_not_called()


def test_gemini_monta_payload_certo_e_extrai_texto():
    with patch("app.utils.gemini_api.requests.post", return_value=_resposta_ok("resposta do gemini")) as m_post:
        texto = gemini_api.gerar_resposta(
            system="Você é um assistente jurídico.",
            mensagens_api=[
                {"role": "user", "content": "pergunta 1"},
                {"role": "assistant", "content": "resposta 1"},
                {"role": "user", "content": "pergunta 2"},
            ],
            api_key="AIzaFAKE",
            modelo="gemini-2.5-flash",
            max_tokens=500,
        )

    assert texto == "resposta do gemini"
    args, kwargs = m_post.call_args
    url = args[0] if args else kwargs["url"]
    assert url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert kwargs["headers"]["x-goog-api-key"] == "AIzaFAKE"
    payload = kwargs["json"]
    assert payload["system_instruction"]["parts"][0]["text"] == "Você é um assistente jurídico."
    assert payload["generationConfig"]["maxOutputTokens"] == 500
    # "assistant" do formato comum vira "model", papel exigido pela API do Gemini.
    papeis = [c["role"] for c in payload["contents"]]
    assert papeis == ["user", "model", "user"]


def test_gemini_usa_modelo_padrao_quando_nao_informado():
    with patch("app.utils.gemini_api.requests.post", return_value=_resposta_ok()) as m_post:
        gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="AIzaFAKE")
    args, kwargs = m_post.call_args
    url = args[0] if args else kwargs["url"]
    assert f"/{gemini_api.MODELO_PADRAO}:generateContent" in url


def test_gemini_chave_invalida_400():
    resp = Mock()
    resp.status_code = 400
    resp.json.return_value = {"error": {"message": "API key not valid. Please pass a valid API key."}}
    resp.text = str(resp.json.return_value)
    with patch("app.utils.gemini_api.requests.post", return_value=resp):
        with pytest.raises(gemini_api.GeminiIndisponivelError, match="chave"):
            gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="chave-errada")


def test_gemini_sem_permissao_403_menciona_faturamento():
    resp = Mock(status_code=403)
    with patch("app.utils.gemini_api.requests.post", return_value=resp):
        with pytest.raises(gemini_api.GeminiIndisponivelError, match="faturamento"):
            gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="AIzaFAKE")


def test_gemini_modelo_inexistente_404():
    resp = Mock(status_code=404)
    with patch("app.utils.gemini_api.requests.post", return_value=resp):
        with pytest.raises(gemini_api.GeminiIndisponivelError, match="modelo-que-nao-existe"):
            gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="AIzaFAKE",
                                       modelo="modelo-que-nao-existe")


def test_gemini_limite_de_taxa_429():
    resp = Mock(status_code=429)
    with patch("app.utils.gemini_api.requests.post", return_value=resp):
        with pytest.raises(gemini_api.GeminiIndisponivelError, match="limite"):
            gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="AIzaFAKE")


def test_gemini_conteudo_bloqueado_pelos_filtros():
    resp = Mock(status_code=200)
    resp.json.return_value = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
    with patch("app.utils.gemini_api.requests.post", return_value=resp):
        with pytest.raises(gemini_api.GeminiIndisponivelError, match="SAFETY"):
            gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="AIzaFAKE")


def test_gemini_falha_de_rede():
    import requests
    with patch("app.utils.gemini_api.requests.post", side_effect=requests.ConnectionError("timeout")):
        with pytest.raises(gemini_api.GeminiIndisponivelError, match="conexão"):
            gemini_api.gerar_resposta("s", [{"role": "user", "content": "oi"}], api_key="AIzaFAKE")


def test_gemini_validar_chave_usa_max_tokens_baixo():
    with patch("app.utils.gemini_api.requests.post", return_value=_resposta_ok("ok")) as m_post:
        assert gemini_api.validar_chave("AIzaFAKE") is True
    payload = m_post.call_args.kwargs["json"]
    assert payload["generationConfig"]["maxOutputTokens"] == 5


# ---------------------------------------------------------------------
# 2. app/utils/agente_ia_router.py roteando para Gemini
# ---------------------------------------------------------------------

@pytest.fixture()
def empresa_gemini(app):
    empresa = Empresa(nome="Escritório Gemini")
    db.session.add(empresa)
    db.session.commit()
    return empresa


def test_router_gemini_sem_chave_da_erro_amigavel_sem_chamar_rede(app, empresa_gemini):
    empresa_gemini.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    db.session.commit()

    with patch.object(router_mod, "gemini_api") as m_gemini:
        with pytest.raises(router_mod.ProvedorIAIndisponivelError, match="Gemini"):
            router_mod.gerar_resposta(empresa_gemini, "system", [{"role": "user", "content": "oi"}])
    m_gemini.gerar_resposta.assert_not_called()


def test_router_gemini_delega_com_chave_decifrada_e_modelo_da_empresa(app, empresa_gemini):
    empresa_gemini.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    empresa_gemini.agente_ia_gemini_chave_cifrada = b"cifrado-fake"
    empresa_gemini.agente_ia_gemini_modelo = "gemini-2.5-flash-lite"
    db.session.commit()

    with patch.object(router_mod, "cofre") as m_cofre, patch.object(router_mod, "gemini_api") as m_gemini:
        m_cofre.decifrar_segredo.return_value = "chave-de-verdade"
        m_gemini.gerar_resposta.return_value = "resposta final"
        m_gemini.GeminiIndisponivelError = gemini_api.GeminiIndisponivelError

        resultado = router_mod.gerar_resposta(empresa_gemini, "system", [{"role": "user", "content": "oi"}],
                                                max_tokens=300)

    assert resultado == "resposta final"
    m_cofre.decifrar_segredo.assert_called_once_with(b"cifrado-fake")
    m_gemini.gerar_resposta.assert_called_once_with(
        "system", [{"role": "user", "content": "oi"}],
        api_key="chave-de-verdade", modelo="gemini-2.5-flash-lite", max_tokens=300,
    )


def test_router_gemini_propaga_erro_do_provedor_como_erro_amigavel(app, empresa_gemini):
    empresa_gemini.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    empresa_gemini.agente_ia_gemini_chave_cifrada = b"cifrado-fake"
    db.session.commit()

    with patch.object(router_mod, "cofre") as m_cofre, patch.object(router_mod, "gemini_api") as m_gemini:
        m_cofre.decifrar_segredo.return_value = "chave-de-verdade"
        m_gemini.GeminiIndisponivelError = gemini_api.GeminiIndisponivelError
        m_gemini.gerar_resposta.side_effect = gemini_api.GeminiIndisponivelError("cota estourada")

        with pytest.raises(router_mod.ProvedorIAIndisponivelError, match="cota estourada"):
            router_mod.gerar_resposta(empresa_gemini, "system", [{"role": "user", "content": "oi"}])


def test_router_descricao_e_disponibilidade_gemini(app, empresa_gemini):
    empresa_gemini.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    db.session.commit()
    assert router_mod.provedor_disponivel(empresa_gemini) is False  # sem chave ainda

    empresa_gemini.agente_ia_gemini_chave_cifrada = b"cifrado-fake"
    empresa_gemini.agente_ia_gemini_modelo = "gemini-2.5-pro"
    db.session.commit()
    assert router_mod.provedor_disponivel(empresa_gemini) is True
    assert "Gemini" in router_mod.descricao_provedor(empresa_gemini)
    assert "gemini-2.5-pro" in router_mod.descricao_provedor(empresa_gemini)


# ---------------------------------------------------------------------
# 3. Tela "Minhas Integrações" (app/routes/integracoes.py)
# ---------------------------------------------------------------------

@pytest.fixture()
def cofre_configurado(app):
    """Gera uma chave Fernet válida só para a duração do teste — nenhum
    teste existente configurava COFRE_SENHA_PROCESSO_KEY (ver docstring
    do módulo), então o cofre ficaria indisponível sem isso."""
    chave_original = app.config.get("COFRE_SENHA_PROCESSO_KEY")
    app.config["COFRE_SENHA_PROCESSO_KEY"] = Fernet.generate_key().decode()
    yield
    app.config["COFRE_SENHA_PROCESSO_KEY"] = chave_original


@pytest.fixture()
def admin_gemini(empresa_basica, criar_usuario):
    admin_id = criar_usuario(empresa_basica["unidade_id"], "admin-gemini@teste.com", papel="admin")
    return {"admin_email": "admin-gemini@teste.com", "empresa_id": empresa_basica["empresa_id"]}


def test_salvar_ia_gemini_sem_chave_cadastrada_recusa(client, login, post_csrf, cofre_configurado, admin_gemini):
    login(admin_gemini["admin_email"])
    r = post_csrf("/minhas-integracoes/ia", {"provedor": "gemini_byok", "api_key": "", "modelo": ""},
                  get_url="/minhas-integracoes")
    assert r.status_code == 200
    empresa = db.session.get(Empresa, admin_gemini["empresa_id"])
    assert empresa.agente_ia_provedor != Empresa.PROVEDOR_IA_GEMINI_BYOK


def test_salvar_ia_gemini_com_chave_valida_cifra_e_ativa(client, login, post_csrf, cofre_configurado, admin_gemini):
    login(admin_gemini["admin_email"])
    with patch("app.routes.integracoes.gemini_api.validar_chave", return_value=True) as m_validar:
        r = post_csrf("/minhas-integracoes/ia",
                      {"provedor": "gemini_byok", "api_key": "AIzaChaveDeVerdade", "modelo": "gemini-2.5-flash"},
                      get_url="/minhas-integracoes")
    assert r.status_code == 200
    m_validar.assert_called_once_with("AIzaChaveDeVerdade", "gemini-2.5-flash")

    empresa = db.session.get(Empresa, admin_gemini["empresa_id"])
    assert empresa.agente_ia_provedor == Empresa.PROVEDOR_IA_GEMINI_BYOK
    assert empresa.agente_ia_gemini_chave_cifrada is not None
    # nunca fica em texto puro no banco
    assert b"AIzaChaveDeVerdade" not in empresa.agente_ia_gemini_chave_cifrada
    assert cofre.decifrar_segredo(empresa.agente_ia_gemini_chave_cifrada) == "AIzaChaveDeVerdade"
    assert empresa.agente_ia_gemini_modelo == "gemini-2.5-flash"


def test_salvar_ia_gemini_chave_invalida_nao_salva_nada(client, login, post_csrf, cofre_configurado, admin_gemini):
    login(admin_gemini["admin_email"])
    with patch("app.routes.integracoes.gemini_api.validar_chave",
               side_effect=gemini_api.GeminiIndisponivelError("chave recusada pelo Google")):
        r = post_csrf("/minhas-integracoes/ia", {"provedor": "gemini_byok", "api_key": "chave-ruim", "modelo": ""},
                      get_url="/minhas-integracoes")
    assert r.status_code == 200
    empresa = db.session.get(Empresa, admin_gemini["empresa_id"])
    assert empresa.agente_ia_gemini_chave_cifrada is None
    assert empresa.agente_ia_provedor != Empresa.PROVEDOR_IA_GEMINI_BYOK


def test_remover_chave_gemini_volta_pro_local(client, login, post_csrf, cofre_configurado, admin_gemini):
    login(admin_gemini["admin_email"])
    with patch("app.routes.integracoes.gemini_api.validar_chave", return_value=True):
        post_csrf("/minhas-integracoes/ia", {"provedor": "gemini_byok", "api_key": "AIzaChave", "modelo": ""},
                  get_url="/minhas-integracoes")

    r = post_csrf("/minhas-integracoes/ia/remover-chave-gemini", {}, get_url="/minhas-integracoes")
    assert r.status_code == 200
    empresa = db.session.get(Empresa, admin_gemini["empresa_id"])
    assert empresa.agente_ia_gemini_chave_cifrada is None
    assert empresa.agente_ia_provedor == Empresa.PROVEDOR_IA_LOCAL


def test_tela_integracoes_mostra_provedor_gemini_como_opcao(client, login, cofre_configurado, admin_gemini):
    login(admin_gemini["admin_email"])
    r = client.get("/minhas-integracoes")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "API do Gemini" in html
    assert "aistudio.google.com/apikey" in html
