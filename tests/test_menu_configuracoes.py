"""
Reorganização do menu lateral (PENDENCIAS.md) — pedido explícito do
usuário: "tem muita informação nesse menu, ele está muito longo [...]
quero que tenha uma aba configurações que tenha tudo de gestão e
plataforma dentro desse menu somente [...] e o resto do menu também
quero que fique mais organizado".

O que mudou (só em app/templates/base.html e app/static/css/estilo.css —
nenhuma rota/blueprint existente foi renomeada ou movida, então nenhum
teste de permissão/URL de rota individual precisou mudar):
  - Os grupos "Gestão" e "Plataforma" viraram um único grupo
    "Configurações", com subtítulos internos ("Minha conta", "Minha
    empresa" e "Plataforma") — mesmos itens de sempre, só reagrupados
    visualmente.
  - Os TRÊS grupos ("Operação", "Governança de carteira" e
    "Configurações") são grupos RECOLHÍVEIS (accordion) — o menu tinha
    até ~20 links visíveis de uma vez pra um admin, o que passava
    impressão de sistema poluído/confuso pro cliente. Cada grupo abre
    sozinho quando a página atual está dentro dele (nunca esconde onde o
    usuário está) OU quando o usuário marcou aquele grupo como favorito
    (ver app/routes/conta.py, "Minha conta > Preferências do menu" —
    dentro do próprio grupo "Configurações"); fora isso, o JS do lado do
    cliente lembra a última escolha em localStorage. Antes desta rodada
    "Operação" era o único grupo sempre fixo/aberto — agora funciona
    igual aos outros dois.
  - "Configurações" agora aparece pra QUALQUER usuário autenticado (antes
    só quem gerenciava usuários/plataforma via), porque o subgrupo
    "Minha conta" (a preferência de menu favorito) é pessoal e vale pra
    todo mundo — só "Minha empresa" e "Plataforma" continuam com as
    mesmas restrições de sempre.

CORREÇÃO (rodadas seguintes, mesma sessão): "Minha conta" e "Minha
empresa" deixaram de ser subtítulos/acordeões dentro de "Configurações" —
viraram cada uma um HUB COM ABAS numa tela só (app/routes/conta.py::hub e
app/routes/admin.py::minha_empresa), a pedido explícito do usuário. Por
isso os testes abaixo checam só que existe UM link pra cada hub dentro de
"Configurações" — o conteúdo de cada aba (Unidades, Equipe, Relatórios,
Auditoria, Alçada de aprovação, Minha licença, Módulos, Integrações) tem
cobertura própria em tests/test_hub_minha_empresa.py, e o de "Minha conta"
em tests/test_hub_rotina_e_minha_conta.py. Só "Plataforma" continua sendo
um subtítulo/acordeão de verdade dentro de "Configurações".

Estes testes cobrem só a ESTRUTURA do menu renderizado (o que aparece pra
cada papel) — não duplicam os testes de permissão de cada rota
individual, que já existem em outros arquivos. O favorito de menu em si
tem testes próprios em tests/test_menu_favorito.py.
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
    assert "Minha conta" in html
    assert "Minha empresa" in html
    assert "Plataforma" in html
    # "Minha conta" e "Minha empresa" agora são hubs (um link cada, não um
    # subtítulo com vários itens soltos) — ver tests/test_hub_minha_empresa.py
    # e tests/test_hub_rotina_e_minha_conta.py pro conteúdo de cada aba.
    assert 'href="/minha-conta/"' in html
    assert 'href="/admin/minha-empresa"' in html
    # "Plataforma" continua sendo subtítulo/acordeão de verdade, com os
    # itens soltos de sempre:
    for texto in ("Painel de licenças", "Empresas clientes", "Catálogo de módulos", "Preços padrão"):
        assert texto in html


def test_admin_comum_ve_configuracoes_mas_nao_plataforma(client, login, app):
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "admin@menuteste.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-grupo="config"' in html
    assert "Minha empresa" in html
    assert 'href="/admin/minha-empresa"' in html
    # "Minha licença"/"Módulos" agora só aparecem DENTRO do hub "Minha
    # empresa" (abas próprias, ver test_hub_minha_empresa.py) — não mais
    # como itens soltos na navegação.
    # não é admin_desenvolvedor -> nenhum item da Plataforma deve aparecer
    assert "subgrupo-titulo\">Plataforma" not in html
    assert "Painel de licenças" not in html
    assert "Empresas clientes" not in html


def test_advogado_sem_gestao_ve_so_minha_conta_em_configuracoes(client, login, app):
    """"Configurações" agora aparece pra QUALQUER usuário autenticado,
    porque "Minha conta > Preferências do menu" (o favorito de menu) é
    pessoal e vale pra todo mundo — mas "Minha empresa"/"Plataforma"
    continuam escondidos de quem não gerencia nada."""
    _, unidade = _montar_empresa(dono_da_plataforma=False)
    _criar_usuario_e_logar(unidade.id, "adv@menuteste.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-grupo="config"' in html
    assert "Preferências do menu" in html
    assert "subgrupo-titulo\">Minha empresa" not in html
    assert "subgrupo-titulo\">Plataforma" not in html


def test_operacao_governanca_e_configuracoes_sao_grupos_recolhiveis(client, login, app):
    """Estrutura recolhível presente no HTML (o comportamento de
    abrir/fechar em si é client-side — ver o <script> em base.html;
    aqui só garante que o servidor manda a marcação certa) — inclusive
    "Operação", que antes era o único grupo sempre fixo/aberto."""
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev2@menuteste.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert 'data-grupo="operacao"' in html
    assert 'data-grupo="governanca"' in html
    assert 'data-grupo="config"' in html
    assert "grupo-toggle" in html
    assert "grupo-chevron" in html
    # "Operação" contém a página atual (dashboard) -> começa expandida.
    inicio = html.index('data-grupo="operacao"')
    trecho = html[max(0, inicio - 40):inicio + 200]
    assert "expandido" in trecho


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
