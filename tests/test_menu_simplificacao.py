"""
Simplificação de menu (PENDENCIAS.md) — pedido explícito do usuário: "vou
[...] ver se consigo simplificar um pouco o sistema, principalmente o
menu" — juntando itens relacionados numa tela só, com abas, e movendo
itens periféricos pra dentro de onde fazem mais sentido:

  1. "Cadastro por CNJ" + "Captação por OAB" viraram um item só, "Entrada
     de processos" (governanca.entrada_processos), com abas — mesmo
     mecanismo de abas já usado na ficha do processo. Os formulários de
     cada aba continuam enviando para as MESMAS rotas de sempre
     (governanca.novo_por_cnj via POST e captacao_oab.nova) — nenhuma
     lógica de negócio mudou, só a navegação. As telas antigas
     (governanca.novo_por_cnj GET e captacao_oab.index) continuam
     existindo e funcionando pra quem chegar direto por um link salvo.
  2. "Captação" (funil de leads) saiu do menu principal "Operação" — vira
     um botão "Ver funil de captação" dentro da tela de Clientes (ver
     tests/test_leads.py, que cobre esta parte).
  3. O grupo "Governança de carteira" ganhou um subtítulo interno ("Painel
     e filas") — mesmo padrão de subgrupo já usado em "Configurações".
  4. "Meu agente local" saiu de "Operação" e foi para "Configurações >
     Minha conta".
  5. "Métricas" + "Relatório semanal (preview)" viraram um item só,
     "Métricas e relatório semanal" (governanca.painel_metricas), também
     com duas abas — mesmo mecanismo, mesmas rotas antigas reaproveitadas
     por baixo (governanca.metricas e governanca.relatorio_semanal_preview
     continuam existindo e funcionando sozinhas).

CORREÇÃO (terceira rodada, mesma sessão, pedido explícito do usuário: "não
eu pedi para fazer igual foi feito em entrada de processos [...] inclusive
em 'entrada de processos', poderia incluir o importar em lotes lá dentro
também"): o mesmo padrão de hub-com-abas de "Entrada de processos" se
estendeu para mais dois lugares —

  6. "Entrada de processos" ganhou uma TERCEIRA aba, "Importar em lote
     (CSV)" — antes um item solto (G5) no menu, agora reaproveita o mesmo
     formulário de sempre (POST pra governanca.importar_lote), só que
     dentro do hub. A tela antiga (GET governanca.importar_lote) continua
     existindo e funcionando pra quem chegar direto por um link salvo.
  7. "Operação > Tarefas/Agenda/Horas" viraram um hub só, "Rotina"
     (rotina.index), com três abas — mesma ideia. "Configurações > Minha
     conta" também: Preferências do menu, Autenticador (2FA, condicional)
     e Meu agente local viraram abas do hub conta.hub, com "Rever
     tutorial" como link à parte (não é uma configuração, é uma ação).
     Nos dois casos as rotas/telas antigas soltas continuam existindo e
     funcionando; só o link do menu lateral mudou pra apontar pro hub. Ver
     tests/test_hub_rotina_e_minha_conta.py para a cobertura detalhada
     desses dois hubs.

Estes testes cobrem só a ESTRUTURA do menu e das novas telas-hub — as
regras de negócio de cadastro por CNJ, captação por OAB e métricas já têm
cobertura própria em outros arquivos (test_captura_completa_no_cadastro.py,
test_captacao_oab.py, test_isolamento_multi_tenant_prazos.py etc.) e não
foram tocadas aqui.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Menu Simplificado", dono_da_plataforma=dono_da_plataforma)
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


# ---------------------- Hub "Entrada de processos" (CNJ + OAB) ----------------------

def test_entrada_processos_renderiza_as_tres_abas_com_formularios_intactos(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv@menusimpl.com", "advogado", login)

    r = client.get("/governanca/processos/entrada")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert "Por número CNJ" in html
    assert "Por OAB (monitoramento contínuo)" in html
    assert "Importar em lote (CSV)" in html
    # os formulários de cada aba continuam enviando para as rotas de sempre:
    assert 'action="/governanca/processos/novo-por-cnj"' in html
    assert 'action="/captacao-oab/nova"' in html
    assert 'action="/governanca/processos/importar-lote"' in html


def test_entrada_processos_aba_lote_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv2b@menusimpl.com", "advogado", login)

    r = client.get("/governanca/processos/entrada?tab=lote")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-lote"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho
    idx_painel = html.index('id="aba-lote"')
    trecho_painel = html[max(0, idx_painel - 30):idx_painel + 30]
    assert "show active" in trecho_painel


def test_rota_antiga_de_importar_lote_continua_acessivel_diretamente(client, login, app):
    """A tela antiga (fora do menu agora) continua funcionando pra quem
    chegar direto por um link salvo — nenhuma rota foi removida."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv2c@menusimpl.com", "advogado", login)

    assert client.get("/governanca/processos/importar-lote").status_code == 200


def test_entrada_processos_prefill_numero_cnj_e_aba_inicial_por_query_string(client, login, app):
    """Cross-link crítico: a tela de triagem por OAB linka pra cá com
    ?tab=cnj&numero_cnj=... pra já abrir na aba certa com o número
    preenchido (ver app/templates/captacao_oab/triagem_detalhe.html)."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv2@menusimpl.com", "advogado", login)

    r = client.get("/governanca/processos/entrada?tab=cnj&numero_cnj=0001234-56.2024.8.26.0100")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'value="0001234-56.2024.8.26.0100"' in html

    r2 = client.get("/governanca/processos/entrada?tab=oab")
    assert r2.status_code == 200
    html2 = r2.data.decode("utf-8")
    idx_botao_oab = html2.index('id="tab-btn-oab"')
    trecho = html2[max(0, idx_botao_oab - 30):idx_botao_oab + 30]
    assert "active" in trecho


def test_rotas_antigas_de_cnj_e_oab_continuam_acessiveis_diretamente(client, login, app):
    """As telas antigas (fora do menu agora) continuam funcionando pra
    quem chegar direto por um link salvo — nenhuma rota foi removida."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv3@menusimpl.com", "advogado", login)

    assert client.get("/governanca/processos/novo-por-cnj").status_code == 200
    assert client.get("/captacao-oab/").status_code == 200


# ---------------------- Hub "Métricas e relatório semanal" ----------------------

def test_painel_metricas_renderiza_as_duas_abas_com_conteudo_das_telas_antigas(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv4@menusimpl.com", "advogado", login)

    r = client.get("/governanca/metricas-e-relatorio")
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    assert "Taxa de cumprimento de prazo" in html  # conteúdo de governanca/metricas.html
    assert "Prazos desta semana" in html  # conteúdo de governanca/relatorio_semanal_preview.html


def test_painel_metricas_aba_relatorio_via_query_string(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv5@menusimpl.com", "advogado", login)

    r = client.get("/governanca/metricas-e-relatorio?tab=relatorio")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    idx_botao = html.index('id="tab-btn-relatorio"')
    trecho = html[max(0, idx_botao - 30):idx_botao + 30]
    assert "active" in trecho


def test_rotas_antigas_de_metricas_e_relatorio_continuam_acessiveis(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv6@menusimpl.com", "advogado", login)

    assert client.get("/governanca/metricas").status_code == 200
    assert client.get("/governanca/relatorio-semanal/preview").status_code == 200


# ---------------------- Estrutura do menu lateral ----------------------

def test_menu_nao_mostra_mais_itens_antigos_soltos(client, login, app):
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv7@menusimpl.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    assert "Entrada de processos" in html
    assert "Métricas e relatório semanal" in html
    assert "Rotina" in html
    assert "Minha conta" in html
    # os links diretos que existiam soltos no menu não aparecem mais lá:
    assert 'href="/governanca/processos/novo-por-cnj"' not in html
    assert 'href="/captacao-oab/"' not in html
    assert 'href="/governanca/relatorio-semanal/preview"' not in html
    assert 'href="/governanca/processos/importar-lote"' not in html
    assert 'href="/tarefas/"' not in html
    assert 'href="/agenda/"' not in html
    assert 'href="/timesheet/"' not in html
    assert 'href="/agente-local"' not in html
    assert 'href="/minha-conta/preferencias"' not in html
    # os links novos que substituem os itens soltos aparecem no lugar:
    assert 'href="/rotina/"' in html
    assert 'href="/minha-conta/"' in html


def test_governanca_tem_subgrupos(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@menusimpl.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'subgrupo-titulo">Painel e filas' in html
    # "Regras e parâmetros" (5 itens, uso raro) virou um submenu recolhível
    # de 2º nível em vez de título fixo — ver test_submenus_segundo_nivel.py
    assert 'data-submenu="regras-parametros"' in html
    assert "Regras e parâmetros" in html


def test_meu_agente_local_saiu_de_operacao_e_foi_para_configuracoes(client, login, app):
    """"Meu agente local" não é mais um link solto em lugar nenhum do menu
    — mora dentro do hub "Minha conta" (uma aba, ver
    tests/test_hub_rotina_e_minha_conta.py). O que este teste garante é
    que o link pro HUB está em Configurações, não em Operação (onde
    "Meu agente local" ficava antes da rodada anterior)."""
    _, unidade = _montar_empresa()
    _criar_usuario_e_logar(unidade.id, "adv8@menusimpl.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    assert 'href="/minha-conta/"' in html
    idx_operacao = html.index('data-grupo="operacao"')
    idx_config = html.index('data-grupo="config"')
    idx_minha_conta = html.index('href="/minha-conta/"')
    # o link tem que estar dentro do bloco de Configurações, não no de Operação:
    assert idx_config < idx_minha_conta
    assert not (idx_operacao < idx_minha_conta < idx_config)

    # e a própria tela do hub, quando visitada, ainda mostra "Meu agente local":
    assert "Meu agente local" in client.get("/minha-conta/").data.decode("utf-8")
