"""
Banco de exemplos de resposta bem-sucedida (few-shot) — PENDENCIAS.md,
seção -124. Pedido do usuário (2026-09): "seria possivel caso o usuario
use a api do claude ou do gemini o nosso agente local aprender com a
resposta deles?".

⚠️ Honestidade de escopo, antes de tudo: isto NÃO é o agente local
"aprendendo" no sentido de ajustar pesos/fazer fine-tuning. Os modelos
locais (app/utils/ia_local.py) são UMA ÚNICA instância compartilhada por
TODAS as empresas (tenants) deste sistema — não existe um modelo por
empresa. Se o conteúdo de uma empresa fosse usado pra ajustar os PESOS
desse modelo único, esse conteúdo vazaria pra QUALQUER outra empresa que
usasse o modelo local depois — exatamente a mesma classe de bug do
vazamento cross-tenant corrigido em PENDENCIAS.md, seção -54 (prazos/
audiências/tarefas de uma empresa aparecendo pra outra por falta de
filtro de escopo). Fine-tuning de verdade foi descartado por esse motivo
— não é uma limitação técnica de hoje, é um risco de segurança que nunca
deve ser assumido aqui.

O que este módulo faz em vez disso é uma aproximação segura e isolada por
empresa: sempre que uma empresa configurada para usar Claude ou Gemini
(BYOK — a própria empresa paga a API, ver app/utils/agente_ia_router.py)
recebe uma resposta gerada com sucesso, essa pergunta+resposta é guardada
como "exemplo" (ver app/models/agente_ia.py::ExemploRespostaIA) — como um
caderno de bons exemplos, nunca uma mudança no modelo em si. Quando ESSA
MESMA empresa (nunca outra) volta a usar o modelo local depois, os
exemplos mais parecidos com a pergunta atual são injetados no PROMPT como
referência de estilo/formato — técnica bem estabelecida (in-context
learning/few-shot), sem nenhum dos riscos de fine-tuning: nada é
persistido no modelo, cada empresa só vê os próprios exemplos, e trocar
de provedor não deixa rastro nenhum no modelo em si.

Reaproveita a MESMA infraestrutura de embedding já construída para a
indexação de documentos (app/utils/indexacao_documentos.py::
provedor_embedding_ativo/cosine_similaridade) — mesma regra de nunca
misturar vetores de proveniências diferentes (embedding_modelo).

Decisões confirmadas pelo usuário (AskUserQuestion, 2026-08/09):
  1. TODAS as respostas do Claude/Gemini são salvas automaticamente como
     candidatas a exemplo (não só as marcadas manualmente).
  2. Vale tanto para o chat do Agente de IA (app/routes/agente_ia.py)
     quanto para a Análise de processo (app/utils/analise_processo_ia.py).
  3. Sem tela de administração — fica só internamente, sem lugar pra ver/
     apagar exemplos guardados (a única "limpeza" é automática, por
     volume — ver `_podar_exemplos_antigos` abaixo).
"""
import json

from app.extensions import db
from app.models import ExemploRespostaIA
from app.utils.indexacao_documentos import (
    provedor_embedding_ativo, cosine_similaridade, ErrosProvedorEmbeddingIndisponivel,
)

# Corte de tamanho generoso o bastante pra não truncar respostas reais na
# prática, mas que impede uma pergunta/resposta gigante de inflar demais
# uma linha desta tabela (ou o prompt, quando reinjetada como exemplo).
LIMITE_TAMANHO_PERGUNTA = 4000
LIMITE_TAMANHO_RESPOSTA = 8000

# Por empresa+contexto — além disso, os exemplos mais ANTIGOS são apagados
# (ver _podar_exemplos_antigos). Sem este teto, uma empresa que usa
# Claude/Gemini há muito tempo acumularia exemplos indefinidamente, sem
# limite de espaço nem de tempo de busca.
LIMITE_EXEMPLOS_POR_CONTEXTO = 400

# Nº padrão de exemplos injetados no prompt — poucos de propósito: o
# modelo local roda numa janela de contexto pequena (ver
# IA_LOCAL_CONTEXT_SIZE em config.py), então o espaço gasto aqui compete
# com o resto do prompt (dados reais do processo/escritório).
LIMITE_EXEMPLOS_PADRAO_PROMPT = 3

BLOCO_EXEMPLOS_CABECALHO = (
    "Exemplos de respostas anteriores desta mesma empresa, geradas por um modelo de IA mais "
    "avançado (API paga configurada pela própria empresa) para perguntas parecidas — use-os SOMENTE "
    "como referência de estilo, formato e nível de detalhe da resposta. É PROIBIDO copiar fato, "
    "número, nome, data ou qualquer dado específico de um exemplo abaixo para a resposta atual — a "
    "resposta atual deve vir exclusivamente dos dados reais fornecidos para ESTA pergunta.\n"
)


def salvar_exemplo_se_byok(empresa, provedor, contexto, pergunta, resposta):
    """
    Chamado DEPOIS de uma geração bem-sucedida via Claude ou Gemini BYOK —
    quem chama já decidiu isso checando `agente_ia_router.provedor_atual`
    antes (esta função não checa sozinha; `provedor` aqui é só guardado
    pra auditoria/debug, nunca usado pra decidir se salva ou não). Nunca
    chamar com provedor "local" — não faz sentido usar o próprio modelo
    local como exemplo pra ele mesmo.

    Nunca levanta exceção pro caller: salvar um exemplo é sempre
    "best-effort" — uma falha aqui (embedding indisponível, erro de banco)
    NUNCA pode derrubar ou atrasar a resposta que o usuário já recebeu.
    """
    if not empresa or not pergunta or not resposta:
        return
    pergunta = pergunta.strip()[:LIMITE_TAMANHO_PERGUNTA]
    resposta = resposta.strip()[:LIMITE_TAMANHO_RESPOSTA]
    if not pergunta or not resposta:
        return

    try:
        gerar_fn, nome_modelo = provedor_embedding_ativo(empresa)
        embedding, embedding_modelo = None, None
        if gerar_fn:
            try:
                vetor = gerar_fn([pergunta])[0]
                if vetor:
                    embedding = json.dumps(vetor)
                    embedding_modelo = nome_modelo
            except ErrosProvedorEmbeddingIndisponivel:
                # Sem embedding agora — o exemplo ainda é salvo (fallback
                # de buscar_exemplos_relevantes vira "mais recentes"), só
                # fica sem participar da busca por similaridade até algum
                # exemplo novo ser salvo com embedding disponível.
                pass

        db.session.add(ExemploRespostaIA(
            empresa_id=empresa.id, contexto=contexto, provedor=provedor,
            pergunta=pergunta, resposta=resposta,
            embedding=embedding, embedding_modelo=embedding_modelo,
        ))
        db.session.commit()
        _podar_exemplos_antigos(empresa, contexto)
    except Exception as e:
        db.session.rollback()
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(e)
        except Exception:
            pass


def _podar_exemplos_antigos(empresa, contexto):
    """Mantém no máximo LIMITE_EXEMPLOS_POR_CONTEXTO exemplos por empresa+
    contexto, apagando os mais ANTIGOS além disso — ver docstring da
    constante acima."""
    total = ExemploRespostaIA.query.filter_by(empresa_id=empresa.id, contexto=contexto).count()
    excedente = total - LIMITE_EXEMPLOS_POR_CONTEXTO
    if excedente <= 0:
        return
    antigos = (ExemploRespostaIA.query.filter_by(empresa_id=empresa.id, contexto=contexto)
               .order_by(ExemploRespostaIA.criado_em.asc()).limit(excedente).all())
    for exemplo in antigos:
        db.session.delete(exemplo)
    db.session.commit()


def buscar_exemplos_relevantes(empresa, contexto, pergunta_atual, limite=LIMITE_EXEMPLOS_PADRAO_PROMPT):
    """
    Devolve até `limite` ExemploRespostaIA da MESMA empresa e do MESMO
    `contexto` (nunca cruza empresa nem contexto — um exemplo do chat
    "operacao" nunca aparece pra "gestao", e um exemplo de uma empresa
    nunca aparece pra outra), os mais parecidos com `pergunta_atual`.

    Mesma lógica de app/utils/indexacao_documentos.py::
    buscar_trechos_relevantes: busca por similaridade de cosseno quando há
    provedor de embedding disponível AGORA e pelo menos um exemplo salvo
    com embedding DO MESMO modelo (nunca mistura modelos); cai pros
    exemplos mais RECENTES como fallback, nunca finge uma similaridade que
    não foi calculada.

    Devolve lista vazia se não há nenhum exemplo salvo ainda pra esta
    empresa+contexto — nunca gera erro pra quem chama.
    """
    if not empresa:
        return []
    todos = (ExemploRespostaIA.query.filter_by(empresa_id=empresa.id, contexto=contexto)
             .order_by(ExemploRespostaIA.criado_em.desc()).all())
    if not todos:
        return []

    gerar_fn, nome_modelo = provedor_embedding_ativo(empresa) if pergunta_atual else (None, None)
    com_embedding = [e for e in todos if e.embedding and e.embedding_modelo == nome_modelo] if gerar_fn else []

    if gerar_fn and com_embedding:
        try:
            vetor_consulta = gerar_fn([pergunta_atual])[0]
            pontuados = [
                (e, cosine_similaridade(vetor_consulta, json.loads(e.embedding)))
                for e in com_embedding
            ]
            pontuados.sort(key=lambda par: par[1], reverse=True)
            return [e for e, _pontuacao in pontuados[:limite]]
        except ErrosProvedorEmbeddingIndisponivel:
            pass  # cai pro fallback abaixo — nunca quebra a montagem do prompt por causa disso
        except (ValueError, TypeError):
            pass  # embedding salvo malformado numa linha antiga — mesmo fallback

    return todos[:limite]


def montar_bloco_exemplos(empresa, contexto, pergunta_atual, limite=LIMITE_EXEMPLOS_PADRAO_PROMPT):
    """
    Monta o texto pronto pra anexar ao system prompt com até `limite`
    exemplos relevantes (ver buscar_exemplos_relevantes) — ou None se não
    há nenhum exemplo salvo ainda pra esta empresa+contexto (quem chama
    simplesmente não injeta nada nesse caso).

    Só faz sentido chamar isto quando o provedor ATUAL da empresa é o
    local (ver agente_ia_router.provedor_atual) — esta função não checa
    isso sozinha, pra não acoplar este módulo ao router; quem chama decide.
    """
    exemplos = buscar_exemplos_relevantes(empresa, contexto, pergunta_atual, limite=limite)
    if not exemplos:
        return None
    partes = [BLOCO_EXEMPLOS_CABECALHO]
    for i, exemplo in enumerate(exemplos, start=1):
        partes.append(f"\nExemplo {i}:\nPergunta: {exemplo.pergunta[:1000]}\nResposta: {exemplo.resposta[:2000]}\n")
    return "".join(partes)
