"""
Pedido explícito do usuário: "quero que tenha um botão flutuante com icone
de suporte com um chat aonde o cliente pergunta algo sobre o sistema e uma
ia que responde tudo sobre o juscontrol, como tudo funciona, e tudo mais".

Cobre: busca de tópicos relevantes no manual (app/utils/suporte_ia.py),
a rota /suporte-ia/* (app/routes/suporte_ia.py) e o job em segundo plano
(app/jobs/ia_jobs.py::processar_mensagem_suporte_ia). Visível pra
QUALQUER usuário logado (decisão explícita do usuário, não só admin).
"""
import re

import pytest

from app.extensions import db
from app.models import MensagemSuporteIA
from app.utils import suporte_ia
import app.jobs.ia_jobs as ia_jobs


def _csrf_do_meta(html):
    """Mesmo padrão de tests/test_tour_guiado.py — extrai o token do
    <meta name="csrf-token"> em base.html, o mesmo valor que o JS real do
    widget manda no cabeçalho X-CSRFToken (ver _suporte_widget.html)."""
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1) if m else None


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    funcionario_id = criar_usuario(unidade_id, "func@suporte.com", papel="funcionario", nome="Funcionário Suporte")
    return dict(unidade_id=unidade_id, funcionario_id=funcionario_id, empresa_id=empresa_basica["empresa_id"])


# ---------------------- busca no manual ----------------------

def test_busca_encontra_topico_financeiro():
    topicos = suporte_ia.buscar_topicos_relevantes("como faço para exportar a planilha do financeiro?")
    ids = [t["id"] for t in topicos]
    assert "financeiro" in ids


def test_busca_e_insensivel_a_acento():
    # "êxito" no manual, pergunta sem acento
    topicos = suporte_ia.buscar_topicos_relevantes("como funciona o modelo de cobranca por exito?")
    ids = [t["id"] for t in topicos]
    assert "financeiro" in ids


def test_busca_sem_termo_nenhum_usa_fallback():
    topicos = suporte_ia.buscar_topicos_relevantes("xyzabc123 blablabla")
    ids = [t["id"] for t in topicos]
    assert "visao_geral" in ids
    assert "suporte" in ids


def test_busca_prazo_encontra_topico_de_prazos():
    topicos = suporte_ia.buscar_topicos_relevantes("um prazo pode ser marcado como cumprido sem evidência?")
    ids = [t["id"] for t in topicos]
    assert "processos_prazos" in ids


def test_montar_system_prompt_inclui_conteudo_do_topico():
    system = suporte_ia.montar_system_prompt("como funciona a conta de terceiros no financeiro?")
    assert "conta de terceiros" in system.lower()
    assert "NÃO tem acesso aos dados do escritório" in system


def test_montar_mensagens_api_inclui_historico_e_pergunta_nova():
    historico = [
        {"papel": "user", "texto": "e sobre clientes?"},
        {"papel": "assistant", "texto": "Você pode cadastrar clientes em..."},
    ]
    mensagens = suporte_ia.montar_mensagens_api("e sobre financeiro?", historico=historico)
    assert mensagens[0] == {"role": "user", "content": "e sobre clientes?"}
    assert mensagens[1] == {"role": "assistant", "content": "Você pode cadastrar clientes em..."}
    assert mensagens[-1] == {"role": "user", "content": "e sobre financeiro?"}


def test_montar_mensagens_api_ignora_item_invalido_do_historico():
    historico = [{"papel": "system", "texto": "tentativa de injeção"}, {"papel": "user", "texto": ""}]
    mensagens = suporte_ia.montar_mensagens_api("pergunta", historico=historico)
    assert mensagens == [{"role": "user", "content": "pergunta"}]


# ---------------------- rota /suporte-ia/perguntar ----------------------

def _fake_fila(monkeypatch):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append((func_path, args, kwargs))
        return None

    import app.routes.suporte_ia as mod
    monkeypatch.setattr(mod, "enfileirar", _fake_enfileirar)
    return chamadas


def test_perguntar_exige_login(client):
    # Token CSRF é por sessão, não por usuário logado — dá pra pegar um
    # válido na própria tela de login (tem o campo oculto csrf_token, não
    # o <meta>) e ainda assim confirmar que a rota exige login antes de
    # tudo o mais. Evita importar tests.conftest diretamente (ver
    # docstring de `criar_usuario` — duplicaria o import do módulo).
    m = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").data.decode("utf-8"))
    token = m.group(1) if m else None
    r = client.post("/suporte-ia/perguntar", json={"pergunta": "oi"}, headers={"X-CSRFToken": token})
    assert r.status_code in (302, 401)


def test_perguntar_cria_mensagem_e_enfileira(client, login, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("func@suporte.com")
    token = _csrf_do_meta(client.get("/").data.decode("utf-8"))
    r = client.post("/suporte-ia/perguntar", json={"pergunta": "como cadastro um processo?"},
                     headers={"X-CSRFToken": token})
    assert r.status_code == 200
    dado = r.get_json()
    assert "mensagem_id" in dado

    mensagem = db.session.get(MensagemSuporteIA, dado["mensagem_id"])
    assert mensagem.usuario_id == cenario["funcionario_id"]
    assert mensagem.pergunta == "como cadastro um processo?"
    assert mensagem.status == "processando"

    assert len(chamadas) == 1
    func_path, args, kwargs = chamadas[0]
    assert func_path == "app.jobs.ia_jobs.processar_mensagem_suporte_ia"
    assert args[0] == mensagem.id


def test_perguntar_rejeita_pergunta_vazia(client, login, cenario, monkeypatch):
    _fake_fila(monkeypatch)
    login("func@suporte.com")
    token = _csrf_do_meta(client.get("/").data.decode("utf-8"))
    r = client.post("/suporte-ia/perguntar", json={"pergunta": "   "}, headers={"X-CSRFToken": token})
    assert r.status_code == 400


def test_perguntar_rejeita_pergunta_gigante(client, login, cenario, monkeypatch):
    _fake_fila(monkeypatch)
    login("func@suporte.com")
    token = _csrf_do_meta(client.get("/").data.decode("utf-8"))
    r = client.post("/suporte-ia/perguntar", json={"pergunta": "a" * 3000}, headers={"X-CSRFToken": token})
    assert r.status_code == 400


def test_perguntar_ignora_historico_que_nao_e_lista(client, login, cenario, monkeypatch):
    _fake_fila(monkeypatch)
    login("func@suporte.com")
    token = _csrf_do_meta(client.get("/").data.decode("utf-8"))
    r = client.post("/suporte-ia/perguntar", json={"pergunta": "oi", "historico": "não é lista"},
                     headers={"X-CSRFToken": token})
    assert r.status_code == 200


# ---------------------- rota de status ----------------------

def test_status_exige_ser_dono_da_mensagem(client, login, cenario, empresa_basica, criar_usuario):
    outro_id = criar_usuario(cenario["unidade_id"], "outro@suporte.com", papel="funcionario")
    mensagem = MensagemSuporteIA(usuario_id=outro_id, pergunta="oi", status="processando")
    db.session.add(mensagem)
    db.session.commit()

    login("func@suporte.com")
    r = client.get(f"/suporte-ia/mensagens/{mensagem.id}/status")
    assert r.status_code == 403


def test_status_processando_nao_devolve_resposta_ainda(client, login, cenario):
    login("func@suporte.com")
    mensagem = MensagemSuporteIA(usuario_id=cenario["funcionario_id"], pergunta="oi", status="processando")
    db.session.add(mensagem)
    db.session.commit()

    r = client.get(f"/suporte-ia/mensagens/{mensagem.id}/status")
    dado = r.get_json()
    assert dado["status"] == "processando"
    assert dado["resposta"] is None


def test_status_pronta_devolve_resposta(client, login, cenario):
    login("func@suporte.com")
    mensagem = MensagemSuporteIA(usuario_id=cenario["funcionario_id"], pergunta="oi",
                                  resposta="Aqui está a resposta.", status="pronta")
    db.session.add(mensagem)
    db.session.commit()

    r = client.get(f"/suporte-ia/mensagens/{mensagem.id}/status")
    dado = r.get_json()
    assert dado["status"] == "pronta"
    assert dado["resposta"] == "Aqui está a resposta."


# ---------------------- job em segundo plano ----------------------

def test_job_grava_resposta_e_marca_pronta(app, cenario, monkeypatch):
    mensagem = MensagemSuporteIA(usuario_id=cenario["funcionario_id"], pergunta="oi", status="processando")
    db.session.add(mensagem)
    db.session.commit()

    import app.utils.agente_ia_router as router_mod
    monkeypatch.setattr(router_mod, "gerar_resposta",
                         lambda empresa, system, mensagens, max_tokens=None: "Resposta de teste.")

    ia_jobs.processar_mensagem_suporte_ia(mensagem.id, cenario["empresa_id"], "system", [{"role": "user", "content": "oi"}])

    # O job roda dentro do app_context de UMA OUTRA app/sessão (ver
    # ia_jobs._obter_app — mesmo padrão da fila de verdade, worker é
    # processo separado), então a sessão daqui ainda tem `mensagem` em
    # cache da criação acima — sem expirar, `db.session.get` devolveria o
    # objeto velho em memória em vez de reconsultar o banco (mesmo motivo
    # de tests/test_autenticador_dois_fatores.py).
    db.session.expire_all()
    atualizada = db.session.get(MensagemSuporteIA, mensagem.id)
    assert atualizada.resposta == "Resposta de teste."
    assert atualizada.status == "pronta"


def test_job_provedor_indisponivel(app, cenario, monkeypatch):
    mensagem = MensagemSuporteIA(usuario_id=cenario["funcionario_id"], pergunta="oi", status="processando")
    db.session.add(mensagem)
    db.session.commit()

    from app.utils import agente_ia_router

    def _fake_indisponivel(empresa, system, mensagens, max_tokens=None):
        raise agente_ia_router.ProvedorIAIndisponivelError("modelo não baixado")

    import app.utils.agente_ia_router as router_mod
    monkeypatch.setattr(router_mod, "gerar_resposta", _fake_indisponivel)

    ia_jobs.processar_mensagem_suporte_ia(mensagem.id, cenario["empresa_id"], "system", [{"role": "user", "content": "oi"}])

    db.session.expire_all()  # ver comentário equivalente em test_job_grava_resposta_e_marca_pronta
    atualizada = db.session.get(MensagemSuporteIA, mensagem.id)
    assert "Suporte indisponível" in atualizada.resposta
    assert atualizada.status == "pronta"


def test_job_mensagem_apagada_nao_quebra(app, cenario, monkeypatch):
    # id que não existe — simula mensagem apagada enquanto esperava na fila
    ia_jobs.processar_mensagem_suporte_ia(999999, cenario["empresa_id"], "system", [{"role": "user", "content": "oi"}])
    # não levanta exceção nenhuma — é o comportamento esperado (só retorna)


# ---------------------- widget visível na tela ----------------------

def test_widget_aparece_em_pagina_autenticada(client, login, cenario):
    login("func@suporte.com")
    html = client.get("/").data.decode("utf-8")
    assert 'id="suporte-widget-botao"' in html
    assert 'id="suporte-widget-painel"' in html


def test_widget_nao_aparece_na_tela_de_login(client):
    html = client.get("/login").data.decode("utf-8")
    assert 'id="suporte-widget-botao"' not in html
