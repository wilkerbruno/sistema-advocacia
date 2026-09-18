"""
Terceira rodada de simplificação do menu (mesma sessão de
tests/test_menu_simplificacao.py e tests/test_submenus_segundo_nivel.py)
— CORREÇÃO explícita do usuário depois de uma primeira tentativa errada
(acordeão de 2º nível): "não eu pedi para fazer igual foi feito em
entrada de processos que incluiu 'por numero CNJ' e 'por OAB'".

O padrão certo é HUB COM ABAS numa tela só (uma rota Flask, várias
tab-pane — mesma mecânica de app/routes/governanca.py::entrada_processos),
aplicado aqui a mais dois lugares:

  - "Operação > Tarefas/Agenda/Horas" → app/routes/rotina.py (rotina.index),
    template app/templates/rotina/index.html.
  - "Configurações > Minha conta" → app/routes/conta.py (conta.hub),
    template app/templates/conta/hub.html.

Em ambos os casos as telas/rotas ANTIGAS (tarefas.listar, agenda.index,
timesheet.listar, conta.preferencias, conta.configurar_totp,
agente_local.meu_agente) continuam existindo e funcionando sozinhas —
só o link do menu lateral passou a apontar pro hub. Este arquivo cobre:

  1. Os dois hubs renderizam as abas certas, com o conteúdo das telas
     antigas.
  2. As rotas antigas continuam acessíveis diretamente.
  3. As ações de POST feitas a partir de dentro de um hub (concluir
     tarefa, excluir apontamento, parear agente local) voltam pro HUB
     (não sempre pra tela solta) — via `redirect(request.referrer or
     ...)`, mesmo padrão já usado em app/routes/leads.py.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, Tarefa, Apontamento
from tests.conftest import extrair_csrf


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Hub Teste", dono_da_plataforma=dono_da_plataforma)
    db.session.add(empresa)
    db.session.flush()
    if not dono_da_plataforma:
        db.session.add(Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                                data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade = Unidade(nome="Matriz", codigo="M1", empresa_id=empresa.id)
    db.session.add(unidade)
    db.session.flush()
    return empresa, unidade


def _criar_usuario_e_logar(unidade_id, email, papel, login):
    u = Usuario(email=email, unidade_id=unidade_id, papel=papel, nome=email.split("@")[0])
    u.set_senha("senha123")
    db.session.add(u)
    db.session.commit()
    login(email)
    return u


# ---------------------- Hub "Rotina" ----------------------

def test_rotina_renderiza_as_tres_abas_com_conteudo_das_telas_antigas(client, login, app):
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv@hubteste.com", "advogado", login)
    tarefa = Tarefa(titulo="Protocolar recurso", unidade_id=unidade.id, responsavel_id=usuario.id,
                     criado_por_id=usuario.id)
    db.session.add(tarefa)
    db.session.commit()

    r = client.get("/rotina/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert "Protocolar recurso" in html  # conteúdo de tarefas/listar.html
    assert 'action="/tarefas/%d/status"' % tarefa.id in html
    assert "Agenda" in html  # aba de agenda
    assert "Horas (timesheet)" in html  # aba de timesheet


def test_rotas_antigas_de_rotina_continuam_acessiveis_diretamente(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv2@hubteste.com", "advogado", login)

    assert client.get("/tarefas/").status_code == 200
    assert client.get("/agenda/").status_code == 200
    assert client.get("/timesheet/").status_code == 200


def test_rotina_aba_horas_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv3@hubteste.com", "advogado", login)

    r = client.get("/rotina/?tab=horas")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-horas"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


def test_concluir_tarefa_a_partir_do_hub_volta_para_o_hub(client, login, app):
    """POST com Referer=/rotina/ tem que voltar pro hub, não pra
    tarefas.listar — mesmo padrão de app/routes/leads.py."""
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv4@hubteste.com", "advogado", login)
    tarefa = Tarefa(titulo="Enviar petição", unidade_id=unidade.id, responsavel_id=usuario.id,
                     criado_por_id=usuario.id)
    db.session.add(tarefa)
    db.session.commit()

    r_get = client.get("/rotina/")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/tarefas/{tarefa.id}/status",
        data={"status": "concluida", "csrf_token": token},
        headers={"Referer": "http://localhost/rotina/?tab=tarefas"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/rotina/?tab=tarefas")


def test_excluir_apontamento_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    usuario = _criar_usuario_e_logar(unidade.id, "adv5@hubteste.com", "advogado", login)
    apontamento = Apontamento(usuario_id=usuario.id, unidade_id=unidade.id, data=date.today(),
                               horas=1, descricao="Reunião com cliente")
    db.session.add(apontamento)
    db.session.commit()

    r_get = client.get("/rotina/?tab=horas")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        f"/timesheet/{apontamento.id}/excluir",
        data={"csrf_token": token},
        headers={"Referer": "http://localhost/rotina/?tab=horas"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/rotina/?tab=horas")


# ---------------------- Hub "Minha conta" ----------------------

def test_minha_conta_renderiza_as_abas_com_conteudo_das_telas_antigas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv6@hubteste.com", "advogado", login)

    r = client.get("/minha-conta/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert "Preferências do menu" in html
    assert "Meu agente local" in html
    assert "Rever o tutorial guiado" in html
    assert 'action="/minha-conta/preferencias/favorito"' in html


def test_rotas_antigas_de_minha_conta_continuam_acessiveis_diretamente(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv7@hubteste.com", "advogado", login)

    assert client.get("/minha-conta/preferencias").status_code == 200
    assert client.get("/agente-local").status_code == 200


def test_parear_agente_local_a_partir_do_hub_volta_para_o_hub(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv8@hubteste.com", "advogado", login)

    r_get = client.get("/minha-conta/?tab=agente-local")
    token = extrair_csrf(r_get.data.decode("utf-8"))

    r = client.post(
        "/agente-local/parear",
        data={"apelido": "Notebook do escritório", "csrf_token": token},
        headers={"Referer": "http://localhost/minha-conta/?tab=agente-local"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/minha-conta/?tab=agente-local")

    # o token novo continua aparecendo (uma única vez) na página seguinte,
    # mesmo tendo vindo de um redirect em vez de um render direto:
    html_depois = client.get(r.headers["Location"]).data.decode("utf-8")
    assert "Copie agora" in html_depois

    # e não aparece mais numa segunda visita (session.pop já consumiu):
    html_de_novo = client.get("/minha-conta/?tab=agente-local").data.decode("utf-8")
    assert "Copie agora" not in html_de_novo
