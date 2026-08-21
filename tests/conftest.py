"""
Fixtures compartilhadas da suíte de testes automatizados (PENDENCIAS.md,
seção -48) — item "Testes automatizados / CI" da tabela de prioridades
do relatório de 20/08/2026.

Todo teste roda contra um banco SQLite descartável (nunca o MySQL de
produção) e nunca depende de credencial externa nenhuma (SMTP, WhatsApp,
API do Claude, DataJud) — os módulos que usam essas integrações já
degradam graciosamente sem elas (mesmo padrão usado em todo o projeto,
ver app/utils/email.py, app/utils/whatsapp.py etc.), então os testes só
confirmam esse comportamento honesto, nunca fingem que uma mensagem saiu
de verdade.

IMPORTANTE sobre a ordem de import: `config.py` lê `DATABASE_URL` (e
`UPLOAD_FOLDER`) como atributo de classe, avaliado uma única vez na
primeira vez que o módulo é importado — por isso as variáveis de
ambiente abaixo têm que ser setadas ANTES de qualquer `from app import
...`, neste arquivo ou em qualquer teste. Isso é o motivo de este bloco
estar no topo do conftest.py (que o pytest sempre carrega antes de
qualquer teste) em vez de dentro de uma fixture.
"""
import os
import re
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
sys.path.insert(0, REPO_ROOT)

_DB_PATH = os.path.join(TESTS_DIR, "_suite.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("UPLOAD_FOLDER", os.path.join(TESTS_DIR, "_uploads"))
for _var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "WHATSAPP_BRIDGE_URL", "WHATSAPP_BRIDGE_TOKEN"):
    os.environ.pop(_var, None)

if os.path.exists(_DB_PATH):
    os.remove(_DB_PATH)

import pytest  # noqa: E402
from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402

SENHA_PADRAO = "senha123"

_flask_app = create_app()
_flask_app.config["WTF_CSRF_ENABLED"] = True  # explícito: queremos testar COM csrf, como em produção


@pytest.fixture()
def app():
    """
    Uma tabela nova (vazia) por teste — cada teste começa do zero e não
    vê dado de nenhum outro teste, mesmo rodando todos no mesmo processo
    pytest. `db.create_all()`/`drop_all()` reaproveitam a mesma conexão
    SQLite (não dá pra trocar DATABASE_URL no meio da suíte — ver
    docstring do módulo acima).
    """
    with _flask_app.app_context():
        _db.create_all()
        yield _flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def extrair_csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


@pytest.fixture()
def login(client):
    """login(email, senha=SENHA_PADRAO) -> Response já logado (segue redirect)."""
    def _login(email, senha=SENHA_PADRAO):
        r = client.get("/login")
        token = extrair_csrf(r.data.decode("utf-8"))
        return client.post("/login", data={"email": email, "senha": senha, "csrf_token": token},
                            follow_redirects=True)
    return _login


@pytest.fixture()
def post_csrf(client):
    """
    post_csrf(post_url, data, get_url=None) -> Response. Faz um GET
    primeiro (em `get_url`, ou em `post_url` se não informado) só pra
    extrair um csrf_token válido da sessão atual — a proteção CSRF do
    projeto é real (ver PENDENCIAS.md seções -38/-39), então todo teste
    de POST precisa de um token de verdade. `get_url` existe pros casos
    em que o formulário fica numa página diferente da URL de destino do
    POST (ex: o formulário "Nova conversa" fica em `/agente-ia/`, mas
    envia pra `/agente-ia/nova`).
    """
    def _post(post_url, data=None, get_url=None):
        r = client.get(get_url or post_url)
        token = extrair_csrf(r.data.decode("utf-8"))
        payload = dict(data or {})
        payload["csrf_token"] = token
        return client.post(post_url, data=payload, follow_redirects=True)
    return _post


@pytest.fixture()
def empresa_basica(app):
    """
    A base mínima que praticamente todo teste precisa: uma Empresa com
    Licença ATIVA (sem isso o middleware de licenciamento devolve 402 pra
    toda rota, ver app/__init__.py) e uma Unidade. Devolve só os IDs
    (nunca os objetos ORM em si) de propósito — um objeto carregado numa
    `app_context` que já fechou vira `DetachedInstanceError` se acessado
    depois; IDs são sempre seguros de guardar e reconsultar.
    """
    from datetime import date, timedelta
    from app.models import Empresa, Licenca, Unidade

    empresa = Empresa(nome="Escritório Teste")
    _db.session.add(empresa)
    _db.session.flush()
    licenca = Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                       data_inicio=date.today(), data_fim=date.today() + timedelta(days=30))
    _db.session.add(licenca)
    unidade = Unidade(nome="Matriz", codigo="M1", empresa_id=empresa.id)
    _db.session.add(unidade)
    _db.session.flush()
    _db.session.commit()
    return {"empresa_id": empresa.id, "unidade_id": unidade.id}


@pytest.fixture()
def criar_usuario(app):
    """
    criar_usuario(unidade_id, email, papel="advogado", senha=SENHA_PADRAO, **extra) -> usuario_id

    Fixture-fábrica (não um helper importável de propósito — importar
    `tests.conftest` de dentro de um arquivo de teste faz o pytest
    carregar o módulo DUAS VEZES sob nomes diferentes ["conftest" e
    "tests.conftest"], o que reexecuta a limpeza do banco no meio da
    suíte e corrompe os outros testes; usar só fixture evita essa
    armadilha por construção).
    """
    from app.models import Usuario

    def _criar(unidade_id, email, papel="advogado", senha=SENHA_PADRAO, **extra):
        usuario = Usuario(email=email, unidade_id=unidade_id, papel=papel,
                           nome=extra.pop("nome", email.split("@")[0]), **extra)
        usuario.set_senha(senha)
        _db.session.add(usuario)
        _db.session.flush()
        return usuario.id

    return _criar
