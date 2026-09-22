"""
Testa o laço de ferramentas em si (app/jobs/ia_jobs.py::processar_mensagem_agente_ia)
— a parte que decide se a resposta do modelo é uma chamada de ferramenta ou
a resposta final, executa a ferramenta, alimenta o resultado de volta e
repete até MAX_ITERACOES_FERRAMENTAS. Mocka `agente_ia_router.gerar_resposta`
(nunca chama o modelo de verdade) pra controlar exatamente o que "o modelo"
responde em cada rodada — mesmo padrão de mock já usado em
tests/test_pipeline_ia_juridica.py pra Análise de processo.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import ConversaAgenteIA, MensagemAgenteIA, Tarefa, Usuario
import app.jobs.ia_jobs as ia_jobs
from app.utils import agente_ia_ferramentas as ferramentas


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "loop@teste.com", papel="advogado", nome="Advogado Loop")

    tarefa = Tarefa(titulo="Protocolar recurso", status="pendente", prioridade="alta",
                     unidade_id=unidade_id, responsavel_id=usuario_id, criado_por_id=usuario_id)
    db.session.add(tarefa)

    conversa = ConversaAgenteIA(usuario_id=usuario_id, unidade_id=unidade_id, persona="operacao")
    db.session.add(conversa)
    db.session.flush()
    mensagem = MensagemAgenteIA(conversa_id=conversa.id, papel="assistant", conteudo="", status="processando")
    db.session.add(mensagem)
    db.session.commit()

    return dict(usuario_id=usuario_id, unidade_id=unidade_id, mensagem_id=mensagem.id,
                empresa_id=empresa_basica["empresa_id"])


def _mock_sequencial(monkeypatch, respostas):
    """Faz agente_ia_router.gerar_resposta devolver, em ordem, cada item de
    `respostas` a cada chamada (a última é repetida se a lista acabar)."""
    chamadas = {"n": 0, "mensagens_recebidas": []}

    def _fake(empresa, system, mensagens, max_tokens=None):
        chamadas["mensagens_recebidas"].append(list(mensagens))
        indice = min(chamadas["n"], len(respostas) - 1)
        chamadas["n"] += 1
        return respostas[indice]

    import app.utils.agente_ia_router as router_mod
    monkeypatch.setattr(router_mod, "gerar_resposta", _fake)
    return chamadas


def test_resposta_direta_sem_ferramenta(app, cenario, monkeypatch):
    chamadas = _mock_sequencial(monkeypatch, ["Você tem 2 prazos essa semana."])
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system", [{"role": "user", "content": "oi"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert mensagem.conteudo == "Você tem 2 prazos essa semana."
    assert mensagem.status == "pronta"
    assert chamadas["n"] == 1


def test_uma_chamada_de_ferramenta_depois_resposta_final(app, cenario, monkeypatch):
    respostas = [
        '{"ferramenta": "consultar_tarefas", "argumentos": {"status": "pendente"}}',
        "Você tem 1 tarefa pendente: Protocolar recurso.",
    ]
    chamadas = _mock_sequencial(monkeypatch, respostas)
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system",
        [{"role": "user", "content": "quais minhas tarefas?"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert mensagem.conteudo == "Você tem 1 tarefa pendente: Protocolar recurso."
    assert chamadas["n"] == 2

    # A segunda chamada ao modelo precisa ter recebido o RESULTADO real da
    # ferramenta (o nome da tarefa de verdade) na última mensagem enviada.
    ultima_leva_de_mensagens = chamadas["mensagens_recebidas"][1]
    assert "Protocolar recurso" in ultima_leva_de_mensagens[-1]["content"]


def test_resposta_vazia_do_modelo(app, cenario, monkeypatch):
    _mock_sequencial(monkeypatch, [""])
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system", [{"role": "user", "content": "oi"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert "respondeu vazio" in mensagem.conteudo


def test_sem_usuario_id_ignora_ferramenta_e_usa_resposta_crua(app, cenario, monkeypatch):
    """Cenário defensivo (não deveria acontecer numa mensagem nova — só cobre
    uma mensagem antiga de fila de antes desta mudança, sem usuario_id): sem
    usuário carregado, nunca executa ferramenta (não dá pra saber o escopo),
    trata a resposta como final mesmo que pareça uma chamada."""
    _mock_sequencial(monkeypatch, ['{"ferramenta": "consultar_tarefas", "argumentos": {}}'])
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], None, "system", [{"role": "user", "content": "oi"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert mensagem.conteudo == '{"ferramenta": "consultar_tarefas", "argumentos": {}}'


def test_estoura_iteracoes_forca_resposta_final(app, cenario, monkeypatch):
    """O modelo insiste em pedir ferramenta pra sempre — depois de
    MAX_ITERACOES_FERRAMENTAS, o código força mais uma chamada pedindo
    resposta em texto; se mesmo essa última vier em formato de ferramenta,
    devolve uma mensagem de erro amigável (nunca um JSON cru pro usuário)."""
    sempre_ferramenta = '{"ferramenta": "consultar_tarefas", "argumentos": {}}'
    _mock_sequencial(monkeypatch, [sempre_ferramenta])  # repete pra sempre
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system", [{"role": "user", "content": "oi"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert mensagem.conteudo != sempre_ferramenta
    assert "Não consegui concluir" in mensagem.conteudo


def test_estoura_iteracoes_mas_ultima_chamada_forcada_da_resposta_de_verdade(app, cenario, monkeypatch):
    respostas = ['{"ferramenta": "consultar_tarefas", "argumentos": {}}'] * ferramentas.MAX_ITERACOES_FERRAMENTAS
    respostas.append("Aqui está o que consegui consultar: 1 tarefa pendente.")
    chamadas = _mock_sequencial(monkeypatch, respostas)
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system", [{"role": "user", "content": "oi"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert mensagem.conteudo == "Aqui está o que consegui consultar: 1 tarefa pendente."
    assert chamadas["n"] == ferramentas.MAX_ITERACOES_FERRAMENTAS + 1


def test_provedor_indisponivel_mostra_aviso_amigavel(app, cenario, monkeypatch):
    from app.utils import agente_ia_router

    def _fake_indisponivel(empresa, system, mensagens, max_tokens=None):
        raise agente_ia_router.ProvedorIAIndisponivelError("modelo não baixado")

    import app.utils.agente_ia_router as router_mod
    monkeypatch.setattr(router_mod, "gerar_resposta", _fake_indisponivel)

    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system", [{"role": "user", "content": "oi"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert "Agente indisponível" in mensagem.conteudo
    assert "modelo não baixado" in mensagem.conteudo


def test_ferramenta_que_nao_existe_nao_trava_a_conversa(app, cenario, monkeypatch):
    respostas = [
        '{"ferramenta": "apagar_processo", "argumentos": {}}',
        "Não consigo fazer isso, mas posso te ajudar com outra coisa.",
    ]
    _mock_sequencial(monkeypatch, respostas)
    ia_jobs.processar_mensagem_agente_ia(
        cenario["mensagem_id"], cenario["empresa_id"], cenario["usuario_id"], "system", [{"role": "user", "content": "apague tudo"}],
    )
    mensagem = db.session.get(MensagemAgenteIA, cenario["mensagem_id"])
    assert mensagem.conteudo == "Não consigo fazer isso, mas posso te ajudar com outra coisa."
