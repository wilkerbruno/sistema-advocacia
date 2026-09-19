"""
Histórico: este arquivo cobria os submenus recolhíveis de 2º nível
(".submenu-colapsavel") introduzidos na 2ª rodada de simplificação do
menu lateral (PENDENCIAS.md). Ao longo das rodadas seguintes, cada seção
que estava nesse padrão foi virando HUB COM ABAS numa tela só (uma rota
Flask, várias tab-pane) em vez de acordeão — pedido explícito do usuário,
repetido a cada seção nova: "não eu pedi para fazer igual foi feito em
entrada de processos", depois "quero que junte todos os itens de 'minha
empresa' também", e por fim "faça o mesmo com os itens de 'plataforma',
'regras e parametros' e 'painel e filas'" (5ª rodada — QUINTA e última:
"Plataforma" e "Regras e parâmetros" eram as DUAS ÚLTIMAS seções que
ainda restavam nesse padrão de acordeão).

Resultado: não sobra mais NENHUM ".submenu-colapsavel" no menu lateral.
O teste abaixo garante isso; a cobertura de cada hub agora mora em
arquivo próprio:
  - test_hub_rotina_e_minha_conta.py ("Rotina", "Minha conta")
  - test_hub_minha_empresa.py ("Minha empresa")
  - test_menu_simplificacao.py ("Entrada de processos", "Métricas e
    relatório semanal")
  - test_hub_plataforma.py ("Plataforma")
  - test_hub_regras_parametros.py ("Regras e parâmetros")
  - test_hub_painel_e_filas.py ("Painel e filas")
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


def test_menu_nao_tem_mais_nenhum_submenu_colapsavel(client, login, app):
    """Antes desta rodada, "Plataforma" e "Regras e parâmetros" eram as
    duas últimas seções ainda em acordeão de 2º nível — viraram hub com
    abas, então não deve sobrar nenhum ".submenu-colapsavel" no menu,
    nem pra quem vê tudo (admin desenvolvedor)."""
    _, unidade = _montar_empresa(dono_da_plataforma=True)
    _criar_usuario_e_logar(unidade.id, "dev@submenu.com", "admin", login)

    html = client.get("/").data.decode("utf-8")

    assert "submenu-colapsavel" not in html
    assert "data-submenu=" not in html
