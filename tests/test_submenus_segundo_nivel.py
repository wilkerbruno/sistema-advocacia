"""
Segunda rodada de simplificação do menu lateral (PENDENCIAS.md) — pedido
explícito do usuário depois da primeira rodada: "o menu ainda está muito
extenso [...] em Minha conta, dá para deixar ela como um botão e colocar
como se fosse um segundo menu [...] em tarefas, agenda, horas também podem
ficar dentro de um item só do menu [...] verifica se outras opções também
ficariam melhor assim".

Introduz um SEGUNDO NÍVEL de recolhível (".submenu-colapsavel" — ver
app/static/css/estilo.css e o comentário em app/templates/base.html),
reaproveitando a mesma mecânica de abrir/fechar + localStorage dos 3
grupos principais (".grupo-colapsavel"), só que pra um punhado de itens
relacionados DENTRO de um grupo já aberto. Hoje sobram só duas seções
nesse padrão — as duas são administração de PLATAFORMA/configuração
jurídica, não "a mesma tarefa vista de formas diferentes":

  - Governança de carteira → "Regras e parâmetros" (5 itens, admin-only,
    uso pouco frequente no dia a dia, telas de CRUD independentes).
  - Configurações → "Plataforma" (4 itens, só admin desenvolvedor).

Continuam como título fixo (sem recolher), de propósito: "Painel e filas"
(contém Painel e Fila de intimações, telas de uso diário).

CORREÇÃO (terceira rodada, mesma sessão): "Operação → Rotina" (Tarefas +
Agenda + Horas) e "Configurações → Minha conta" (Preferências +
Autenticador + Meu agente local + Rever tutorial) NÃO viraram submenus
recolhíveis — o usuário corrigiu explicitamente o pedido: "não eu pedi
para fazer igual foi feito em entrada de processos que incluiu 'por
numero CNJ' e 'por OAB'", ou seja, o padrão certo é um HUB COM ABAS numa
tela só (uma rota Flask, várias tab-pane), não um acordeão de links.

QUARTA RODADA (mesma sessão, pedido explícito: "otimo, agora quero que
junte todos os itens de 'minha empresa' tambem"): "Configurações > Minha
empresa" (Unidades, Equipe, Relatórios, Auditoria, Alçada de aprovação,
Minha licença, Módulos, Integrações — 8 itens) também deixou de ser um
submenu recolhível e virou hub com abas, mesmo padrão de Rotina/Minha
conta/Entrada de processos.

Os testes desses hubs ficam em test_menu_simplificacao.py (Entrada de
processos, Métricas e relatório semanal) e
test_hub_rotina_e_minha_conta.py (Rotina, Minha conta) e
test_hub_minha_empresa.py (Minha empresa). Este arquivo mantém só os
testes dos submenus que continuam sendo acordeão de verdade (Plataforma,
Regras e parâmetros).
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Submenu Teste", dono_da_plataforma=dono_da_plataforma)
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


def _trecho_do_submenu(html, nome_submenu):
    inicio = html.index(f'data-submenu="{nome_submenu}"')
    # janela generosa o bastante pra cobrir o wrapper + botão + div de itens
    return html[max(0, inicio - 60):inicio + 60]


# ---------------------- Governança: "Regras e parâmetros" ----------------------

def test_regras_parametros_vira_submenu_para_admin(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@submenu.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-submenu="regras-parametros"' in html
    assert "Tabela de custas" in html


def test_regras_parametros_nao_aparece_para_advogado_comum(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv3@submenu.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-submenu="regras-parametros"' not in html


# ---------------------- Configurações: "Plataforma" ----------------------

def test_configuracoes_agrupa_plataforma_em_submenu(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev2@submenu.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-submenu="plataforma"' in html
    # "Minha conta" e "Minha empresa" não são mais submenus recolhíveis —
    # são links só pros respectivos hubs (ver test_menu_simplificacao.py e
    # test_hub_minha_empresa.py) — não deveriam aparecer como acordeão.
    assert 'data-submenu="minha-conta"' not in html
    assert 'data-submenu="minha-empresa"' not in html


def test_advogado_comum_nao_ve_submenu_de_plataforma(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv4@submenu.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-submenu="plataforma"' not in html
