"""
Reorganização do menu lateral (PENDENCIAS.md) — pedido explícito do
usuário: "tem muita informação nesse menu, ele está muito longo [...]
quero que tenha uma aba configurações que tenha tudo de gestão e
plataforma dentro desse menu somente [...] e o resto do menu também
quero que fique mais organizado".

O que mudou (só em app/templates/base.html e app/static/css/estilo.css —
nenhuma rota/blueprint foi renomeada ou movida, então nenhum teste
existente de permissão/URL precisou mudar):
  - Os grupos "Gestão" e "Plataforma" viraram um único grupo
    "Configurações", com dois subtítulos internos ("Minha empresa" e
    "Plataforma") — mesmos itens de sempre, só reagrupados visualmente.
  - "Governança de carteira" e "Configurações" agora são grupos
    RECOLHÍVEIS (accordion) — o menu tinha até ~20 links visíveis de uma
    vez pra um admin, o que passava impressão de sistema poluído/confuso
    pro cliente. Cada grupo abre sozinho quando a página atual está
    dentro dele (nunca esconde onde o usuário está); fora isso, o JS do
    lado do cliente lembra a última escolha em localStorage. "Operação"
    continua sempre visível (é o que se usa todo dia).

Estes testes cobrem só a ESTRUTURA do menu renderizado (o que aparece pra
cada papel) — não duplicam os testes de permissão de cada rota
individual, que já existem em outros arquivos.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario


def _montar_empresa(dono_da_plataforma=False):
    empresa = Empresa(nome="Empresa Menu Teste", dono_da_plataforma=dono_da_plataforma)
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


def test_admin_desenvolvedor_ve_configuracoes_com_minha_empresa_e_plataforma(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@menuteste.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-grupo="config"' in html
    assert "Minha empresa" in html
    assert "Plataforma" in html
    # itens que antes ficavam soltos em "Gestão"/"Plataforma" continuam
    # todos presentes, só reagrupados dentro de "Configurações":
    for texto in ("Unidades", "Equipe", "Relatórios", "Auditoria", "Alçada de aprovação",
                  "Integrações", "Painel de licenças", "Empresas clientes",
                  "Catálogo de módulos", "Preços padrão"):
        assert texto in html


def test_admin_comum_ve_configuracoes_mas_nao_plataforma(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "admin@menuteste.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-grupo="config"' in html
    assert "Minha empresa" in html
    assert "Minha licença" in html
    assert "Módulos" in html
    # não é admin_desenvolvedor -> nenhum item da Plataforma deve aparecer
    assert "subgrupo-titulo\">Plataforma" not in html
    assert "Painel de licenças" not in html
    assert "Empresas clientes" not in html


def test_advogado_sem_gestao_nao_ve_grupo_configuracoes(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "adv@menuteste.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    # sem pode_gerenciar_usuarios() nem is_admin_desenvolvedor -> o grupo
    # inteiro (cabeçalho incluso) não deve renderizar, não só os itens.
    assert 'data-grupo="config"' not in html
    assert "subgrupo-titulo\">Minha empresa" not in html


def test_governanca_e_configuracoes_sao_grupos_recolhiveis(client, login, app):
    """Estrutura recolhível presente no HTML (o comportamento de
    abrir/fechar em si é client-side — ver o <script> em base.html;
    aqui só garante que o servidor manda a marcação certa)."""
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev2@menuteste.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-grupo="governanca"' in html
    assert 'data-grupo="config"' in html
    assert "grupo-toggle" in html
    assert "grupo-chevron" in html
    # "Operação" continua fixo (não é um grupo-colapsavel).
    assert '<div class="grupo-titulo">Operação</div>' in html


def test_pagina_dentro_de_governanca_abre_o_grupo_automaticamente(client, login, app):
    """Nunca esconde onde o usuário está: se a página atual pertence ao
    grupo Governança, o wrapper já vem com a classe "expandido" e os
    itens SEM a classe "recolhido" — sem depender de JS/localStorage pra
    mostrar o item ativo."""
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "adv2@menuteste.com", "advogado", login)

    html = client.get("/governanca/painel").data.decode("utf-8")

    inicio = html.index('data-grupo="governanca"')
    trecho = html[max(0, inicio - 40):inicio + 400]
    assert "expandido" in trecho
    assert "recolhido" not in trecho
