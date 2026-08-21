"""
Testa o monitoramento de erros via Sentry (PENDENCIAS.md, seção -49):
sem SENTRY_DSN configurado o SDK nunca é inicializado (mesmo padrão
"degrada honestamente sem credencial" do resto do projeto — ver
app/utils/email.py, app/utils/whatsapp.py), com SENTRY_DSN configurado o
SDK é inicializado de verdade, e a raspagem de campo sensível
(_before_send) nunca deixa senha/token/csrf passar mesmo que acabem indo
parar no contexto de um evento.

Cuidado deliberado nestes testes: nunca chama sentry_sdk.capture_*
(dispararia uma tentativa real de rede pro Sentry) — só verifica se o SDK
foi inicializado (`sentry_sdk.is_initialized()`) e sempre desfaz a
inicialização no fim de cada teste que a liga, pra não vazar estado
global (o cliente do Sentry é global no processo) para os outros testes
da suíte.
"""
import sentry_sdk
from sentry_sdk import Scope
from sentry_sdk.client import NonRecordingClient

from app.utils.monitoramento import inicializar_sentry, _remover_campos_sensiveis, _before_send


def _resetar_sentry():
    """Desliga o SDK e devolve o cliente global ao estado 'nunca inicializado'
    — chamado no fim de todo teste que liga o Sentry, pra isolar os outros
    testes da suíte do estado global do sentry_sdk."""
    sentry_sdk.get_client().close(timeout=0.1)
    Scope.get_global_scope().set_client(NonRecordingClient())


def test_sem_dsn_nao_inicializa(app):
    assert not sentry_sdk.is_initialized(), \
        "app de teste não define SENTRY_DSN — Sentry não deveria estar ativo"
    inicializar_sentry(app)
    assert not sentry_sdk.is_initialized(), \
        "sem SENTRY_DSN configurado, inicializar_sentry() não deveria chamar sentry_sdk.init()"


def test_com_dsn_inicializa_de_verdade(app):
    app.config["SENTRY_DSN"] = "https://abc123@o0.ingest.sentry.io/0"
    try:
        inicializar_sentry(app)
        assert sentry_sdk.is_initialized(), \
            "com SENTRY_DSN configurado, inicializar_sentry() deveria ativar o SDK"
        client = sentry_sdk.get_client()
        assert client.transport is not None
        assert client.options["send_default_pii"] is False
        assert client.options["traces_sample_rate"] == 0.0
    finally:
        _resetar_sentry()
        app.config["SENTRY_DSN"] = ""


def test_remover_campos_sensiveis_tira_senha_token_csrf():
    dados = {
        "email": "usuario@exemplo.com",
        "senha": "minhasenha123",
        "nova_senha": "outrasenha",
        "csrf_token": "abc.def.ghi",
        "cpf_cnpj": "123.456.789-00",
        "descricao": "isso não é sensível e deveria continuar aparecendo",
    }
    limpo = _remover_campos_sensiveis(dados)
    assert limpo["senha"] == "[removido]"
    assert limpo["nova_senha"] == "[removido]"
    assert limpo["csrf_token"] == "[removido]"
    assert limpo["cpf_cnpj"] == "[removido]"
    assert limpo["email"] == "usuario@exemplo.com"
    assert limpo["descricao"] == "isso não é sensível e deveria continuar aparecendo"


def test_remover_campos_sensiveis_e_recursivo_em_lista_e_dict_aninhado():
    dados = {
        "usuario": {"nome": "Fulano", "senha": "123456"},
        "historico": [{"token": "xyz"}, {"acao": "login"}],
    }
    limpo = _remover_campos_sensiveis(dados)
    assert limpo["usuario"]["senha"] == "[removido]"
    assert limpo["usuario"]["nome"] == "Fulano"
    assert limpo["historico"][0]["token"] == "[removido]"
    assert limpo["historico"][1]["acao"] == "login"


def test_before_send_limpa_request_do_evento():
    event = {
        "request": {
            "data": {"email": "a@b.com", "senha": "supersecreta"},
            "cookies": {"session": "cookie-secreto-de-verdade"},
            "headers": {"Authorization": "Bearer abc123", "User-Agent": "pytest"},
            "query_string": "csrf_token=xyz&pagina=1",
        }
    }
    evento_limpo = _before_send(event, {})
    assert evento_limpo["request"]["data"]["senha"] == "[removido]"
    assert evento_limpo["request"]["data"]["email"] == "a@b.com"
    # cookies são removidos por inteiro (o valor de um cookie de sessão já
    # equivale a uma senha) — não dá pra confiar em bater nome de cookie
    # com termo sensível, o nome do cookie pode ser qualquer coisa.
    assert evento_limpo["request"]["cookies"] == "[removido]"
    assert evento_limpo["request"]["headers"]["Authorization"] == "[removido]"
    assert evento_limpo["request"]["headers"]["User-Agent"] == "pytest"


def test_identificar_usuario_atual_nao_quebra_sem_sentry_ativo(client, login, empresa_basica, criar_usuario):
    # sem SENTRY_DSN configurado (padrão da suíte), o hook before_request
    # que chama identificar_usuario_atual() não deveria lançar erro nenhum
    # nem quebrar uma requisição normal.
    criar_usuario(empresa_basica["unidade_id"], "usuariomonitor@teste.com", papel="advogado")
    login("usuariomonitor@teste.com")
    r = client.get("/")
    assert r.status_code == 200


def test_erro_500_nao_vazado_sem_traceback(client, login, empresa_basica, criar_usuario, monkeypatch, app):
    # Força uma exceção dentro da rota do Painel (chamada por qualquer
    # usuário logado, a primeira tela depois do login) e confirma que o
    # handler de 500 (app/__init__.py) devolve a página de erro amigável
    # do próprio sistema, não o traceback padrão do Flask/Werkzeug — sem
    # isso, um erro real em produção vazaria caminho de arquivo interno e
    # detalhe de implementação pra qualquer usuário que batesse nele.
    criar_usuario(empresa_basica["unidade_id"], "usuarioerro@teste.com", papel="admin")
    login("usuarioerro@teste.com")

    # `app` é o mesmo objeto Flask compartilhado por toda a suíte (ver
    # tests/conftest.py) — muda config aqui, desfaz no final, pra não
    # vazar pros outros testes que rodam depois deste no mesmo processo.
    propagate_original = app.config.get("PROPAGATE_EXCEPTIONS")
    testing_original = app.config.get("TESTING")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["TESTING"] = False  # TESTING=True faria o client relançar a exceção em vez de gerar a resposta 500
    try:
        from app.routes import dashboard as dashboard_mod

        def _quebra(*args, **kwargs):
            raise RuntimeError("erro proposital pra testar o handler de 500")

        # `dashboard.index` importa `aplicar_escopo_unidade` com `from ... import`
        # — o nome vive no namespace do próprio módulo dashboard, então
        # substituir ali (e não no módulo original app.utils.acesso) é o que
        # realmente afeta a chamada feita dentro da view.
        monkeypatch.setattr(dashboard_mod, "aplicar_escopo_unidade", _quebra)

        r = client.get("/")
        assert r.status_code == 500
        body = r.data.decode("utf-8")
        assert "Erro 500" in body
        assert "Ocorreu um erro inesperado" in body
        assert "RuntimeError" not in body, "traceback/detalhe interno não deveria vazar pro usuário"
        assert "Traceback" not in body
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = propagate_original
        app.config["TESTING"] = testing_original
