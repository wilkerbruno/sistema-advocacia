"""
Banco de exemplos de resposta few-shot (PENDENCIAS.md, seção -124) —
app/models/agente_ia.py::ExemploRespostaIA, app/utils/exemplos_resposta_ia.py
e app/utils/agente_ia_router.py::provedor_atual.

Cobre: (1) provedor_atual decide certo entre "local"/"claude"/"gemini";
(2) salvar_exemplo_se_byok nunca levanta e sempre trunca/valida antes de
gravar, e poda exemplos antigos além do limite; (3) buscar_exemplos_relevantes
nunca cruza empresa nem contexto, nunca mistura embedding de modelos
diferentes, ordena por similaridade quando possível e cai pros mais
recentes quando não; (4) montar_bloco_exemplos formata o texto pronto pro
prompt; (5) a injeção no chat do Agente de IA (app/routes/agente_ia.py) e
na Análise de processo (app/utils/analise_processo_ia.py) só acontece
quando o provedor ATUAL é o local, e o SALVAMENTO do exemplo só acontece
quando a resposta veio de Claude/Gemini BYOK, nunca do local nem de uma
resposta de erro/sentinela.

Nenhuma chamada de rede de verdade (Claude/Gemini) nem modelo local real
acontece aqui — sempre mockado, mesmo espírito de test_gemini_byok.py e
test_indexacao_documentos.py.
"""
import json

import pytest

from app.extensions import db
from app.models import Empresa, ExemploRespostaIA
from app.utils import agente_ia_router as router_mod
from app.utils import exemplos_resposta_ia as exemplos_mod
import app.utils.indexacao_documentos as idx_mod


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture()
def empresa(app, empresa_basica):
    return db.session.get(Empresa, empresa_basica["empresa_id"])


def _mocka_embedding_local(monkeypatch, vetor_por_texto=None, nome_modelo="local:fake-embedding.gguf"):
    """Mesmo helper de tests/test_indexacao_documentos.py — faz o modelo
    de embedding local "existir" (sem chave do Gemini) devolvendo vetores
    determinísticos, sem carregar nenhum arquivo GGUF de verdade."""
    def _fake(textos):
        if vetor_por_texto:
            return [vetor_por_texto.get(t, [0.0]) for t in textos]
        return [[float(len(t))] for t in textos]

    monkeypatch.setattr(idx_mod.ia_local, "embedding_disponivel", lambda: True)
    monkeypatch.setattr(idx_mod.ia_local, "nome_modelo_embedding", lambda: nome_modelo)
    monkeypatch.setattr(idx_mod.ia_local, "gerar_embeddings_lote", _fake)


def _sem_embedding_disponivel(monkeypatch):
    monkeypatch.setattr(idx_mod.ia_local, "embedding_disponivel", lambda: False)


# ---------------------------------------------------------------------
# provedor_atual
# ---------------------------------------------------------------------

def test_provedor_atual_local_por_padrao(app, empresa):
    assert router_mod.provedor_atual(empresa) == "local"


def test_provedor_atual_local_sem_empresa():
    assert router_mod.provedor_atual(None) == "local"


def test_provedor_atual_claude_quando_configurado(app, empresa):
    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_CLAUDE_BYOK
    db.session.commit()
    assert router_mod.provedor_atual(empresa) == "claude"


def test_provedor_atual_gemini_quando_configurado(app, empresa):
    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    db.session.commit()
    assert router_mod.provedor_atual(empresa) == "gemini"


# ---------------------------------------------------------------------
# salvar_exemplo_se_byok
# ---------------------------------------------------------------------

def test_salvar_exemplo_cria_linha_com_provedor_e_contexto(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:operacao", "qual meu prazo?", "Você tem 2 prazos.")

    linha = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).one()
    assert linha.provedor == "claude"
    assert linha.contexto == "agente_ia:operacao"
    assert linha.pergunta == "qual meu prazo?"
    assert linha.resposta == "Você tem 2 prazos."
    assert linha.embedding is None  # sem provedor de embedding disponível


def test_salvar_exemplo_grava_embedding_quando_disponivel(app, empresa, monkeypatch):
    _mocka_embedding_local(monkeypatch, nome_modelo="local:fake.gguf")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "gemini", "agente_ia:gestao", "quantos processos ativos?", "42 ativos.")

    linha = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).one()
    assert linha.embedding_modelo == "local:fake.gguf"
    assert json.loads(linha.embedding) == [float(len("quantos processos ativos?"))]


def test_salvar_exemplo_nunca_levanta_com_falha_de_embedding(app, empresa, monkeypatch):
    from app.utils import ia_local

    monkeypatch.setattr(idx_mod.ia_local, "embedding_disponivel", lambda: True)
    monkeypatch.setattr(idx_mod.ia_local, "nome_modelo_embedding", lambda: "local:fake.gguf")

    def _falha(textos):
        raise ia_local.ModeloIndisponivelError("indisponível agora")

    monkeypatch.setattr(idx_mod.ia_local, "gerar_embeddings_lote", _falha)

    # não levanta — exemplo ainda é salvo, só sem embedding.
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:operacao", "pergunta", "resposta")
    linha = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).one()
    assert linha.embedding is None
    assert linha.embedding_modelo is None


def test_salvar_exemplo_nunca_levanta_com_erro_inesperado(app, empresa, monkeypatch):
    def _quebrado(empresa):
        raise RuntimeError("erro inesperado qualquer")

    monkeypatch.setattr(exemplos_mod, "provedor_embedding_ativo", _quebrado)
    # não deve levantar pro caller mesmo com uma falha totalmente
    # inesperada no meio do caminho — salvar exemplo é sempre best-effort.
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:operacao", "pergunta", "resposta")
    assert ExemploRespostaIA.query.count() == 0


def test_salvar_exemplo_ignora_pergunta_ou_resposta_vazia(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "", "resposta")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta", "")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "   ", "   ")
    exemplos_mod.salvar_exemplo_se_byok(None, "claude", "ctx", "pergunta", "resposta")
    assert ExemploRespostaIA.query.count() == 0


def test_salvar_exemplo_trunca_textos_grandes(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    pergunta_grande = "p" * (exemplos_mod.LIMITE_TAMANHO_PERGUNTA + 500)
    resposta_grande = "r" * (exemplos_mod.LIMITE_TAMANHO_RESPOSTA + 500)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", pergunta_grande, resposta_grande)

    linha = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).one()
    assert len(linha.pergunta) == exemplos_mod.LIMITE_TAMANHO_PERGUNTA
    assert len(linha.resposta) == exemplos_mod.LIMITE_TAMANHO_RESPOSTA


def test_salvar_exemplo_poda_exemplos_antigos_alem_do_limite(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    monkeypatch.setattr(exemplos_mod, "LIMITE_EXEMPLOS_POR_CONTEXTO", 2)

    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta 1", "resposta 1")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta 2", "resposta 2")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta 3", "resposta 3")

    restantes = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id, contexto="ctx") \
        .order_by(ExemploRespostaIA.criado_em.asc()).all()
    assert len(restantes) == 2
    # o mais antigo (pergunta 1) foi podado — só os 2 mais recentes ficam.
    assert [e.pergunta for e in restantes] == ["pergunta 2", "pergunta 3"]


def test_salvar_exemplo_nao_poda_alem_do_limite_entre_contextos_diferentes(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    monkeypatch.setattr(exemplos_mod, "LIMITE_EXEMPLOS_POR_CONTEXTO", 1)

    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:operacao", "pergunta A", "resposta A")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:gestao", "pergunta B", "resposta B")

    # limite é por CONTEXTO — os dois exemplos, de contextos diferentes,
    # sobrevivem mesmo com limite=1.
    assert ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).count() == 2


# ---------------------------------------------------------------------
# buscar_exemplos_relevantes / montar_bloco_exemplos
# ---------------------------------------------------------------------

def test_buscar_exemplos_vazio_sem_nenhum_salvo(app, empresa):
    assert exemplos_mod.buscar_exemplos_relevantes(empresa, "agente_ia:operacao", "pergunta qualquer") == []
    assert exemplos_mod.montar_bloco_exemplos(empresa, "agente_ia:operacao", "pergunta qualquer") is None


def test_buscar_exemplos_nunca_cruza_empresa(app, empresa, monkeypatch):
    from app.models import Licenca
    from datetime import date, timedelta

    _sem_embedding_disponivel(monkeypatch)
    outra_empresa = Empresa(nome="Outro Escritório")
    db.session.add(outra_empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=outra_empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    db.session.commit()

    exemplos_mod.salvar_exemplo_se_byok(outra_empresa, "claude", "agente_ia:operacao", "pergunta da outra", "resposta da outra")

    assert exemplos_mod.buscar_exemplos_relevantes(empresa, "agente_ia:operacao", "pergunta qualquer") == []


def test_buscar_exemplos_nunca_cruza_contexto(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:gestao", "pergunta gestao", "resposta gestao")

    assert exemplos_mod.buscar_exemplos_relevantes(empresa, "agente_ia:operacao", "pergunta qualquer") == []


def test_buscar_exemplos_ordena_por_similaridade_quando_ha_embedding(app, empresa, monkeypatch):
    vetores = {
        "pergunta parecida": [1.0, 0.0],
        "pergunta bem diferente": [0.0, 1.0],
        "consulta atual": [0.9, 0.1],
    }
    _mocka_embedding_local(monkeypatch, vetor_por_texto=vetores)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta bem diferente", "resposta 1")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta parecida", "resposta 2")

    encontrados = exemplos_mod.buscar_exemplos_relevantes(empresa, "ctx", "consulta atual")
    assert [e.resposta for e in encontrados] == ["resposta 2", "resposta 1"]


def test_buscar_exemplos_nunca_mistura_embedding_de_modelos_diferentes(app, empresa, monkeypatch):
    _mocka_embedding_local(monkeypatch, nome_modelo="local:modelo-antigo.gguf")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta antiga", "resposta antiga")

    # troca o "modelo ativo" — o exemplo salvo acima é de um modelo
    # DIFERENTE do que geraria o vetor da consulta agora; nunca deve ser
    # comparado por similaridade (a única forma de confirmar isso aqui é
    # ver que o resultado cai pro fallback "mais recente", não que a busca
    # quebra ou finge similaridade).
    _mocka_embedding_local(monkeypatch, nome_modelo="local:modelo-novo.gguf")
    encontrados = exemplos_mod.buscar_exemplos_relevantes(empresa, "ctx", "consulta qualquer")
    assert [e.resposta for e in encontrados] == ["resposta antiga"]  # fallback "mais recentes", não similaridade


def test_buscar_exemplos_cai_para_mais_recentes_sem_provedor_de_embedding(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta 1", "resposta 1")
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "pergunta 2", "resposta 2")

    encontrados = exemplos_mod.buscar_exemplos_relevantes(empresa, "ctx", "consulta qualquer")
    assert [e.resposta for e in encontrados] == ["resposta 2", "resposta 1"]  # mais recente primeiro


def test_buscar_exemplos_respeita_limite(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    for i in range(5):
        exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", f"pergunta {i}", f"resposta {i}")

    assert len(exemplos_mod.buscar_exemplos_relevantes(empresa, "ctx", "consulta", limite=2)) == 2


def test_montar_bloco_exemplos_none_quando_vazio(app, empresa):
    assert exemplos_mod.montar_bloco_exemplos(empresa, "ctx", "pergunta") is None


def test_montar_bloco_exemplos_formata_perguntas_e_respostas(app, empresa, monkeypatch):
    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "ctx", "qual o prazo do processo X?", "O prazo vence em 10 dias.")

    bloco = exemplos_mod.montar_bloco_exemplos(empresa, "ctx", "pergunta atual")
    assert "qual o prazo do processo X?" in bloco
    assert "O prazo vence em 10 dias." in bloco
    # instrução explícita de nunca copiar fato/dado dos exemplos.
    assert "PROIBIDO copiar" in bloco


# ---------------------------------------------------------------------
# Wiring no chat do Agente de IA (app/routes/agente_ia.py)
# ---------------------------------------------------------------------

def test_montar_system_injeta_exemplos_quando_provedor_local(app, empresa, monkeypatch):
    import app.routes.agente_ia as rota_mod

    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:operacao", "pergunta antiga", "resposta salva antes")

    system, _ = rota_mod._montar_system_e_mensagens(
        "operacao", [], "contexto qualquer", empresa=empresa, pergunta_atual="pergunta nova",
    )
    assert "resposta salva antes" in system


def test_montar_system_nao_injeta_quando_provedor_e_byok(app, empresa, monkeypatch):
    import app.routes.agente_ia as rota_mod

    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_CLAUDE_BYOK
    db.session.commit()
    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(empresa, "claude", "agente_ia:operacao", "pergunta antiga", "resposta que nao deveria aparecer")

    system, _ = rota_mod._montar_system_e_mensagens(
        "operacao", [], "contexto qualquer", empresa=empresa, pergunta_atual="pergunta nova",
    )
    assert "resposta que nao deveria aparecer" not in system


def test_montar_system_sem_empresa_nao_quebra(app):
    import app.routes.agente_ia as rota_mod

    system, mensagens = rota_mod._montar_system_e_mensagens("operacao", [], "contexto qualquer")
    assert isinstance(system, str)
    assert mensagens == []


# ---------------------------------------------------------------------
# Wiring no job do chat (app/jobs/ia_jobs.py::processar_mensagem_agente_ia)
# ---------------------------------------------------------------------

@pytest.fixture()
def cenario_job(app, empresa_basica, criar_usuario):
    from app.models import ConversaAgenteIA, MensagemAgenteIA

    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "exemplo-job@teste.com", papel="advogado", nome="Advogado Exemplo")
    conversa = ConversaAgenteIA(usuario_id=usuario_id, unidade_id=unidade_id, persona="operacao")
    db.session.add(conversa)
    db.session.flush()
    mensagem = MensagemAgenteIA(conversa_id=conversa.id, papel="assistant", conteudo="", status="processando")
    db.session.add(mensagem)
    db.session.commit()
    return dict(usuario_id=usuario_id, mensagem_id=mensagem.id, empresa_id=empresa_basica["empresa_id"])


def _mock_router_resposta(monkeypatch, texto):
    import app.utils.agente_ia_router as router_mod_local
    monkeypatch.setattr(router_mod_local, "gerar_resposta", lambda empresa, system, mensagens, max_tokens=None: texto)


def test_job_salva_exemplo_quando_resposta_veio_de_byok(app, cenario_job, empresa, monkeypatch):
    import app.jobs.ia_jobs as ia_jobs

    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_CLAUDE_BYOK
    db.session.commit()
    _mock_router_resposta(monkeypatch, "Resposta de verdade do Claude.")

    ia_jobs.processar_mensagem_agente_ia(
        cenario_job["mensagem_id"], cenario_job["empresa_id"], cenario_job["usuario_id"],
        "system", [{"role": "user", "content": "qual meu prazo mais próximo?"}],
        persona="operacao",
    )

    salvo = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).one()
    assert salvo.provedor == "claude"
    assert salvo.contexto == "agente_ia:operacao"
    assert salvo.pergunta == "qual meu prazo mais próximo?"
    assert salvo.resposta == "Resposta de verdade do Claude."


def test_job_nao_salva_exemplo_quando_provedor_e_local(app, cenario_job, monkeypatch):
    import app.jobs.ia_jobs as ia_jobs

    _mock_router_resposta(monkeypatch, "Resposta do modelo local.")
    ia_jobs.processar_mensagem_agente_ia(
        cenario_job["mensagem_id"], cenario_job["empresa_id"], cenario_job["usuario_id"],
        "system", [{"role": "user", "content": "oi"}], persona="operacao",
    )
    assert ExemploRespostaIA.query.count() == 0


def test_job_nao_salva_exemplo_sem_persona(app, cenario_job, empresa, monkeypatch):
    import app.jobs.ia_jobs as ia_jobs

    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    db.session.commit()
    _mock_router_resposta(monkeypatch, "Resposta do Gemini.")
    ia_jobs.processar_mensagem_agente_ia(
        cenario_job["mensagem_id"], cenario_job["empresa_id"], cenario_job["usuario_id"],
        "system", [{"role": "user", "content": "oi"}],  # sem persona= — chamada antiga/compatível
    )
    assert ExemploRespostaIA.query.count() == 0


def test_job_nao_salva_exemplo_em_resposta_de_erro(app, cenario_job, empresa, monkeypatch):
    import app.jobs.ia_jobs as ia_jobs

    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_CLAUDE_BYOK
    db.session.commit()

    def _falha(empresa, system, mensagens, max_tokens=None):
        raise router_mod.ProvedorIAIndisponivelError("chave inválida")

    monkeypatch.setattr(router_mod, "gerar_resposta", _falha)
    ia_jobs.processar_mensagem_agente_ia(
        cenario_job["mensagem_id"], cenario_job["empresa_id"], cenario_job["usuario_id"],
        "system", [{"role": "user", "content": "oi"}], persona="operacao",
    )
    assert ExemploRespostaIA.query.count() == 0


def test_job_nao_salva_exemplo_em_resposta_vazia_sentinela(app, cenario_job, empresa, monkeypatch):
    import app.jobs.ia_jobs as ia_jobs

    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_CLAUDE_BYOK
    db.session.commit()
    _mock_router_resposta(monkeypatch, "")  # modelo respondeu vazio -> vira a sentinela
    ia_jobs.processar_mensagem_agente_ia(
        cenario_job["mensagem_id"], cenario_job["empresa_id"], cenario_job["usuario_id"],
        "system", [{"role": "user", "content": "oi"}], persona="operacao",
    )
    assert ExemploRespostaIA.query.count() == 0


# ---------------------------------------------------------------------
# Wiring na Análise de processo (app/utils/analise_processo_ia.py::gerar_analise)
# ---------------------------------------------------------------------

@pytest.fixture()
def processo_cenario(app, empresa_basica, criar_usuario):
    from app.models import Cliente, Processo

    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "analise-exemplo@teste.com", papel="advogado", nome="Advogado Análise")
    cliente = Cliente(nome="Cliente Análise Exemplo", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-EX-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id, criado_por_id=usuario_id)
    db.session.add(processo)
    db.session.commit()
    return dict(processo_id=processo.id, empresa_id=empresa_basica["empresa_id"])


def test_gerar_analise_salva_exemplo_quando_byok(app, processo_cenario, empresa, monkeypatch):
    import app.utils.analise_processo_ia as analise_mod
    from app.models import Processo

    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_GEMINI_BYOK
    db.session.commit()
    monkeypatch.setattr(analise_mod.agente_ia_router, "gerar_resposta",
                         lambda empresa, system, mensagens, max_tokens=None: "SITUAÇÃO ATUAL\nEm andamento.")

    processo = db.session.get(Processo, processo_cenario["processo_id"])
    resultado, _truncado = analise_mod.gerar_analise(processo, "resumo")

    salvo = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id).one()
    assert salvo.provedor == "gemini"
    assert salvo.contexto == "analise_processo:resumo"
    assert salvo.resposta == "SITUAÇÃO ATUAL\nEm andamento."
    # a checagem automática de grounding roda DEPOIS de salvar o exemplo —
    # o resultado devolvido ao caller pode ganhar um bloco de aviso extra,
    # mas o exemplo salvo é sempre o texto CRU gerado pelo modelo.
    assert resultado == salvo.resposta or resultado.endswith(salvo.resposta)


def test_gerar_analise_nao_salva_exemplo_quando_provedor_e_local(app, processo_cenario, monkeypatch):
    import app.utils.analise_processo_ia as analise_mod
    from app.models import Processo

    monkeypatch.setattr(analise_mod.agente_ia_router, "gerar_resposta",
                         lambda empresa, system, mensagens, max_tokens=None: "SITUAÇÃO ATUAL\nEm andamento.")
    processo = db.session.get(Processo, processo_cenario["processo_id"])
    analise_mod.gerar_analise(processo, "resumo")

    assert ExemploRespostaIA.query.count() == 0


def test_gerar_analise_injeta_exemplos_no_prompt_quando_local(app, processo_cenario, empresa, monkeypatch):
    import app.utils.analise_processo_ia as analise_mod
    from app.models import Processo

    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(
        empresa, "claude", "analise_processo:resumo", "resuma este processo", "Resumo modelo salvo antes.",
    )

    capturado = {}

    def _fake(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        return "SITUAÇÃO ATUAL\nEm andamento."

    monkeypatch.setattr(analise_mod.agente_ia_router, "gerar_resposta", _fake)
    processo = db.session.get(Processo, processo_cenario["processo_id"])
    analise_mod.gerar_analise(processo, "resumo")

    assert "Resumo modelo salvo antes." in capturado["system"]


def test_gerar_analise_nao_injeta_exemplos_de_rascunho_no_resumo(app, processo_cenario, empresa, monkeypatch):
    import app.utils.analise_processo_ia as analise_mod
    from app.models import Processo

    _sem_embedding_disponivel(monkeypatch)
    exemplos_mod.salvar_exemplo_se_byok(
        empresa, "claude", "analise_processo:rascunho_peticao", "faça uma contestação", "Rascunho de contestação salvo antes.",
    )

    capturado = {}

    def _fake(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        return "SITUAÇÃO ATUAL\nEm andamento."

    monkeypatch.setattr(analise_mod.agente_ia_router, "gerar_resposta", _fake)
    processo = db.session.get(Processo, processo_cenario["processo_id"])
    analise_mod.gerar_analise(processo, "resumo")  # contexto "analise_processo:resumo", não "rascunho_peticao"

    assert "Rascunho de contestação salvo antes." not in capturado["system"]
