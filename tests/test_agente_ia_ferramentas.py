"""
Pedido explícito do usuário: "quero melhorar o agente local e... treinar
ele para resolver tudo que depende de um agente de forma mais eficaz
possível". Treinar de verdade (ajustar pesos) não é viável neste ambiente
(sem GPU/dado de treino) — o caminho real é dar ao agente ACESSO REAL aos
dados do sistema durante a própria conversa (tool-calling), em vez de só
receber um resumo fixo pré-carregado no início. Ver
app/utils/agente_ia_ferramentas.py para a implementação completa.

Estes testes cobrem: reconhecimento do formato de chamada de ferramenta
(extrair_chamada_ferramenta), despacho (executar_ferramenta), e cada
ferramenta em si — sempre confirmando que o ESCOPO de unidade/empresa é
respeitado (nunca vaza dado de outra unidade/empresa) e que a restrição de
acesso financeiro (Usuario.pode_ver_financeiro) é reforçada dentro da
própria ferramenta, não só na tela.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Prazo, Tarefa, Lancamento, Usuario
from app.utils import agente_ia_ferramentas as ferramentas


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_a = empresa_basica["unidade_id"]
    from app.models import Unidade
    unidade_b = Unidade(nome="Filial", codigo="F1", empresa_id=empresa_basica["empresa_id"])
    db.session.add(unidade_b)
    db.session.flush()

    advogado_a_id = criar_usuario(unidade_a, "adv.a@ferramentas.com", papel="advogado", nome="Advogado A")
    advogado_b_id = criar_usuario(unidade_b.id, "adv.b@ferramentas.com", papel="advogado", nome="Advogado B")
    admin_id = criar_usuario(unidade_a, "admin@ferramentas.com", papel="admin", nome="Admin Ferramentas")
    funcionario_sem_financeiro_id = criar_usuario(
        unidade_a, "func@ferramentas.com", papel="funcionario", nome="Funcionário", acesso_financeiro=False,
    )

    cliente_a = Cliente(nome="Cliente da Unidade A", unidade_id=unidade_a)
    cliente_b = Cliente(nome="Cliente da Unidade B", unidade_id=unidade_b.id)
    db.session.add_all([cliente_a, cliente_b])
    db.session.flush()

    processo_a = Processo(numero_processo="0000001-11.2026.8.26.0100", numero_interno="P-A-1",
                           cliente_id=cliente_a.id, unidade_id=unidade_a, area_direito="Cível",
                           status="ativo", responsavel_id=advogado_a_id, criado_por_id=advogado_a_id)
    processo_b = Processo(numero_processo="0000002-22.2026.8.26.0100", numero_interno="P-B-1",
                           cliente_id=cliente_b.id, unidade_id=unidade_b.id, area_direito="Cível",
                           status="ativo", responsavel_id=advogado_b_id, criado_por_id=advogado_b_id)
    db.session.add_all([processo_a, processo_b])
    db.session.flush()

    prazo_a = Prazo(processo_id=processo_a.id, descricao="Contestar", status="pendente",
                     data_vencimento=date.today() + timedelta(days=3))
    prazo_b = Prazo(processo_id=processo_b.id, descricao="Recurso", status="pendente",
                     data_vencimento=date.today() + timedelta(days=3))
    db.session.add_all([prazo_a, prazo_b])

    tarefa_a = Tarefa(titulo="Revisar petição", status="pendente", prioridade="alta",
                       unidade_id=unidade_a, responsavel_id=advogado_a_id, criado_por_id=advogado_a_id)
    db.session.add(tarefa_a)

    lancamento_a = Lancamento(descricao="Honorário A", valor=1000, natureza="receita", status="pendente",
                               unidade_id=unidade_a, criado_por_id=advogado_a_id)
    db.session.add(lancamento_a)

    db.session.commit()

    return dict(unidade_a=unidade_a, unidade_b=unidade_b.id, advogado_a_id=advogado_a_id,
                advogado_b_id=advogado_b_id, admin_id=admin_id, funcionario_id=funcionario_sem_financeiro_id,
                processo_a_id=processo_a.id, processo_b_id=processo_b.id,
                cliente_a_id=cliente_a.id, cliente_b_id=cliente_b.id)


# ---------------------- extrair_chamada_ferramenta ----------------------

def test_extrai_chamada_valida():
    texto = '{"ferramenta": "buscar_processos", "argumentos": {"termo": "123"}}'
    chamada = ferramentas.extrair_chamada_ferramenta(texto)
    assert chamada == {"ferramenta": "buscar_processos", "argumentos": {"termo": "123"}}


def test_extrai_chamada_com_espacos_em_volta():
    texto = '  \n {"ferramenta": "consultar_prazos", "argumentos": {}}  \n'
    assert ferramentas.extrair_chamada_ferramenta(texto) is not None


def test_extrai_chamada_envolvida_em_bloco_markdown():
    texto = '```json\n{"ferramenta": "consultar_tarefas", "argumentos": {"status": "pendente"}}\n```'
    chamada = ferramentas.extrair_chamada_ferramenta(texto)
    assert chamada["ferramenta"] == "consultar_tarefas"


def test_texto_normal_nao_e_reconhecido_como_chamada():
    assert ferramentas.extrair_chamada_ferramenta("Você tem 3 prazos vencendo essa semana.") is None


def test_texto_com_chaves_no_meio_nao_e_reconhecido():
    texto = 'O processo {"numero": "123"} está ativo — só um exemplo de anotação, não é uma ferramenta.'
    assert ferramentas.extrair_chamada_ferramenta(texto) is None


def test_json_sem_chave_ferramenta_nao_e_reconhecido():
    assert ferramentas.extrair_chamada_ferramenta('{"algo": "qualquer coisa"}') is None


def test_json_malformado_nao_e_reconhecido():
    assert ferramentas.extrair_chamada_ferramenta('{"ferramenta": "buscar_processos", "argumentos": ') is None


def test_texto_vazio_nao_e_reconhecido():
    assert ferramentas.extrair_chamada_ferramenta("") is None
    assert ferramentas.extrair_chamada_ferramenta(None) is None


def test_argumentos_que_nao_sao_objeto_viram_dict_vazio():
    chamada = ferramentas.extrair_chamada_ferramenta('{"ferramenta": "consultar_tarefas", "argumentos": "oi"}')
    assert chamada["argumentos"] == {}


# ---------------------- executar_ferramenta (despacho) ----------------------

def test_executar_ferramenta_desconhecida_lista_as_validas(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.executar_ferramenta({"ferramenta": "apagar_tudo", "argumentos": {}}, usuario)
    assert "não existe" in resultado
    assert "buscar_processos" in resultado


def test_executar_ferramenta_com_erro_interno_nao_propaga_excecao(app, cenario, monkeypatch):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])

    def _explode(usuario, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(ferramentas.FERRAMENTAS, "buscar_processos",
                         dict(ferramentas.FERRAMENTAS["buscar_processos"], executar=_explode))
    resultado = ferramentas.executar_ferramenta({"ferramenta": "buscar_processos", "argumentos": {}}, usuario)
    assert "Não foi possível executar" in resultado


# ---------------------- buscar_processos ----------------------

def test_buscar_processos_por_numero(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_buscar_processos(usuario, termo="0000001-11")
    assert "0000001-11.2026.8.26.0100" in resultado
    assert "Cliente da Unidade A" in resultado


def test_buscar_processos_por_nome_de_cliente(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_buscar_processos(usuario, termo="Cliente da Unidade A")
    assert "0000001-11" in resultado


def test_buscar_processos_nao_vaza_outra_unidade(app, cenario):
    """Advogado da unidade A busca por um termo que só existe na unidade B
    — não pode aparecer nada (mesmo escopo de sempre, aplicar_escopo_unidade)."""
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_buscar_processos(usuario, termo="0000002-22")
    assert "Nenhum processo encontrado" in resultado


def test_buscar_processos_admin_ve_as_duas_unidades_da_empresa(app, cenario):
    usuario = db.session.get(Usuario, cenario["admin_id"])
    resultado_a = ferramentas.ferramenta_buscar_processos(usuario, termo="0000001-11")
    resultado_b = ferramentas.ferramenta_buscar_processos(usuario, termo="0000002-22")
    assert "0000001-11" in resultado_a
    assert "0000002-22" in resultado_b


def test_buscar_processos_sem_termo_pede_termo(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_buscar_processos(usuario, termo="")
    assert "Informe um termo" in resultado


# ---------------------- buscar_cliente ----------------------

def test_buscar_cliente_conta_processos_visiveis(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_buscar_cliente(usuario, termo="Cliente da Unidade A")
    assert "1 processo(s)" in resultado


def test_buscar_cliente_nao_vaza_outra_unidade(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_buscar_cliente(usuario, termo="Cliente da Unidade B")
    assert "Nenhum cliente encontrado" in resultado


# ---------------------- consultar_prazos ----------------------

def test_consultar_prazos_lista_so_da_propria_unidade(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_consultar_prazos(usuario, dias=7, status="pendente")
    assert "Contestar" in resultado
    assert "Recurso" not in resultado


def test_consultar_prazos_fora_do_periodo_nao_aparece(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_consultar_prazos(usuario, dias=1, status="pendente")
    assert "Nenhum prazo" in resultado  # prazo vence em 3 dias, período pedido é só 1


def test_consultar_prazos_dias_invalido_usa_padrao(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_consultar_prazos(usuario, dias="não é número")
    assert "Contestar" in resultado


# ---------------------- consultar_tarefas ----------------------

def test_consultar_tarefas_filtra_por_status(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_consultar_tarefas(usuario, status="pendente")
    assert "Revisar petição" in resultado


def test_consultar_tarefas_status_concluida_nao_traz_pendente(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    resultado = ferramentas.ferramenta_consultar_tarefas(usuario, status="concluida")
    assert "Nenhuma tarefa" in resultado


# ---------------------- consultar_financeiro ----------------------

def test_consultar_financeiro_bloqueado_sem_acesso(app, cenario):
    usuario = db.session.get(Usuario, cenario["funcionario_id"])
    resultado = ferramentas.ferramenta_consultar_financeiro(usuario)
    assert "não tem acesso a dados financeiros" in resultado


def test_consultar_financeiro_soma_correta(app, cenario):
    usuario = db.session.get(Usuario, cenario["advogado_a_id"])
    # advogado comum não tem pode_ver_financeiro por padrão (só admin/gestor
    # ou concessão explícita) — usar o admin, que sempre tem.
    admin = db.session.get(Usuario, cenario["admin_id"])
    resultado = ferramentas.ferramenta_consultar_financeiro(admin, natureza="receita", status="pendente")
    assert "1.000,00" in resultado or "1000,00" in resultado


def test_consultar_financeiro_natureza_invalida_e_ignorada(app, cenario):
    admin = db.session.get(Usuario, cenario["admin_id"])
    resultado = ferramentas.ferramenta_consultar_financeiro(admin, natureza="algo_invalido")
    # não quebra, só ignora o filtro inválido e soma tudo do escopo
    assert "lançamento(s)" in resultado
