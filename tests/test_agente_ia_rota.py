"""
Testa a integração da rota app/routes/agente_ia.py::enviar_mensagem com o
tool-calling novo: precisa passar o `usuario_id` de quem perguntou pro job
em segundo plano (app/jobs/ia_jobs.py), já que o worker não tem
`current_user`/sessão nenhuma (ver docstring de app/utils/agente_ia_ferramentas.py)
— sem isso, nenhuma ferramenta funcionaria (extrair_chamada_ferramenta só
roda quando há usuário carregado). Também confirma que o system prompt
mandado pro job inclui as instruções de ferramenta.
"""
import pytest

from app.extensions import db
from app.models import ConversaAgenteIA


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "rota@teste.com", papel="advogado", nome="Advogado Rota")
    return dict(unidade_id=unidade_id, usuario_id=usuario_id)


def _fake_fila(monkeypatch):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append((func_path, args, kwargs))
        return None

    import app.routes.agente_ia as mod
    monkeypatch.setattr(mod, "enfileirar", _fake_enfileirar)
    return chamadas


def test_enviar_mensagem_passa_usuario_id_para_o_job(client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("rota@teste.com")

    r = post_csrf("/agente-ia/nova", {"persona": "operacao"}, get_url="/agente-ia/")
    conversa = ConversaAgenteIA.query.filter_by(usuario_id=cenario["usuario_id"]).first()

    r2 = post_csrf(f"/agente-ia/{conversa.id}/mensagem", {"mensagem": "quais meus prazos?"},
                    get_url=f"/agente-ia/{conversa.id}")
    assert r2.status_code == 200
    assert len(chamadas) == 1

    func_path, args, kwargs = chamadas[0]
    assert func_path == "app.jobs.ia_jobs.processar_mensagem_agente_ia"
    # args: (mensagem_id, empresa_id, usuario_id, system, mensagens_api)
    usuario_id_enviado = args[2]
    system_enviado = args[3]
    assert usuario_id_enviado == cenario["usuario_id"]
    assert "ferramenta" in system_enviado.lower()
    assert "buscar_processos" in system_enviado
    assert kwargs.get("job_timeout") == 900
