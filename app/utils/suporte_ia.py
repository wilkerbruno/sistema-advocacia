"""
Motor do chat de suporte flutuante (botão visível em toda tela, pedido
explícito do usuário: "quero que tenha um botão flutuante com icone de
suporte com um chat aonde o cliente pergunta algo sobre o sistema e uma
ia que responde tudo sobre o juscontrol, como tudo funciona").

Reaproveita o MESMO motor de IA já usado pelo Agente de IA de portfólio
(app/utils/agente_ia_router.py — modelo local grátis por padrão, ou
Claude/Gemini BYOK se a empresa configurou em "Minhas Integrações") e o
MESMO mecanismo de job em segundo plano + polling (ver app/jobs/ia_jobs.py
e PENDENCIAS.md, seção -32) — nada novo sendo inventado nessas duas
frentes, só um system prompt e uma fonte de conhecimento diferentes (ver
app/utils/juscontrol_manual.py).

Busca no manual: por PALAVRA-CHAVE simples (interseção de termos entre a
pergunta e as `palavras_chave` de cada tópico), deliberadamente SEM
embeddings. O mecanismo de embeddings que já existe no sistema (ver
app/utils/indexacao_documentos.py) só funciona quando a empresa tem uma
chave Gemini BYOK cadastrada — o chat de suporte precisa responder em
QUALQUER empresa cliente, mesmo sem nenhuma integração paga configurada
(é ajuda sobre "como o sistema funciona", não deveria depender disso pra
sequer existir).

Este agente NUNCA recebe dado real do escritório (nem contexto pré-
carregado, nem ferramentas — diferente do Agente de IA de portfólio, ver
app/utils/agente_ia_ferramentas.py) — só o conteúdo de ajuda estático.
"""
import re
import unicodedata

from app.utils.juscontrol_manual import TOPICOS

MAX_TOPICOS_NO_PROMPT = 4
MAX_HISTORICO_TURNOS = 6  # pares pergunta/resposta anteriores repassados pro modelo

SYSTEM_BASE = (
    "Você é o assistente de suporte do JusControl, um sistema de gestão para escritórios de "
    "advocacia. Responda SOMENTE com base no conteúdo de ajuda fornecido abaixo — nunca invente "
    "uma funcionalidade, tela ou regra que não esteja descrita nele. Se a pergunta for sobre algo "
    "que não está coberto no conteúdo, diga honestamente que não tem essa informação e sugira "
    "procurar o administrador da empresa ou o suporte técnico responsável pelo contrato, em vez de "
    "arriscar uma resposta errada. Responda sempre em português do Brasil, de forma direta e "
    "amigável, em poucos parágrafos curtos (evite listas longas, salvo quando a pergunta pedir uma "
    "lista de passos). Você NÃO tem acesso aos dados do escritório de quem pergunta — não sabe "
    "quantos processos, quais clientes ou quais números financeiros essa empresa específica tem. Se "
    "pedirem um dado real assim, explique que isso deve ser consultado direto na tela "
    "correspondente, ou no Agente de IA (menu Operação, que sim tem acesso aos dados reais do "
    "escritório de quem pergunta) — nunca finja saber."
)


# Palavras conectivas comuns em português — sem filtrar isso, uma pergunta
# como "como funciona o modelo de cobrança por êxito?" pontuava mais alto
# em tópicos que não tinham nada a ver (só por compartilharem "como"/"o"/
# "de"/"por") do que no tópico "financeiro" de verdade (que só bate em
# "êxito"), e um tópico genérico acabava desbancando o certo no corte de
# MAX_TOPICOS_NO_PROMPT. Lista curta, de propósito — só remove o que é
# puro ruído de conexão, nunca um termo que possa ser palavra-chave real.
_PALAVRAS_VAZIAS = frozenset("""
    a as o os e ou de da do das dos em no na nos nas por para com sem um uma uns umas
    que se ao aos à às é são foi ser tem têm pode posso qual quais quando onde porque
    isso isto essa esse essas esses minha meu minhas meus sua seu suas seus muito mais
    menos ja ainda tambem so apenas como eu tu ele ela nos vos eles elas me te lhe nos
    lhes meu teu seu nosso vosso este esta estes estas aquele aquela aqueles aquelas
""".split())


def _normalizar(texto):
    """minúsculas, sem acento — pra 'êxito' bater com 'exito', etc."""
    texto = (texto or "").lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto


def _termos(texto):
    brutos = re.findall(r"[a-z0-9]+", _normalizar(texto))
    return {t for t in brutos if t not in _PALAVRAS_VAZIAS}


_TOPICOS_NORMALIZADOS = None


def _topicos_indexados():
    global _TOPICOS_NORMALIZADOS
    if _TOPICOS_NORMALIZADOS is None:
        indexado = []
        for topico in TOPICOS:
            termos_chave = set()
            for chave in topico["palavras_chave"]:
                termos_chave |= _termos(chave)
            # o título também conta como palavra-chave implícita
            termos_chave |= _termos(topico["titulo"])
            indexado.append((topico, termos_chave))
        _TOPICOS_NORMALIZADOS = indexado
    return _TOPICOS_NORMALIZADOS


def buscar_topicos_relevantes(pergunta, limite=MAX_TOPICOS_NO_PROMPT):
    """
    Busca simples por sobreposição de palavras entre a pergunta e as
    palavras-chave de cada tópico — pontua pela quantidade de termos em
    comum, sem peso/stemming nenhum. Sem nenhum termo em comum com nada,
    devolve os tópicos "visão geral" e "suporte" como fallback (pelo menos
    orienta minimamente em vez de vir sem nada).
    """
    termos_pergunta = _termos(pergunta)
    pontuados = []
    for topico, termos_chave in _topicos_indexados():
        pontos = len(termos_pergunta & termos_chave)
        if pontos > 0:
            pontuados.append((pontos, topico["id"], topico))
    pontuados.sort(key=lambda item: (-item[0], item[1]))
    escolhidos = [topico for _, _, topico in pontuados[:limite]]
    if not escolhidos:
        escolhidos = [t for t in TOPICOS if t["id"] in ("visao_geral", "suporte")]
    return escolhidos


def montar_system_prompt(pergunta):
    topicos = buscar_topicos_relevantes(pergunta)
    blocos = "\n\n".join(f"### {t['titulo']}\n{t['conteudo']}" for t in topicos)
    return SYSTEM_BASE + "\n\nConteúdo de ajuda relevante para esta pergunta:\n\n" + blocos


def montar_mensagens_api(pergunta, historico=None):
    """
    `historico`: lista opcional de {"papel": "user"|"assistant", "texto": str}
    (mesma forma simples que o widget mantém em memória no navegador — ver
    app/routes/suporte_ia.py) — os últimos MAX_HISTORICO_TURNOS pares são
    repassados pro modelo pra dar contexto de continuidade numa pergunta de
    acompanhamento (ex.: "e no financeiro?"), sem persistir nada no banco.
    """
    mensagens = []
    if historico:
        for item in historico[-(MAX_HISTORICO_TURNOS * 2):]:
            papel = item.get("papel")
            texto = (item.get("texto") or "").strip()
            if papel in ("user", "assistant") and texto:
                mensagens.append({"role": papel, "content": texto})
    mensagens.append({"role": "user", "content": pergunta})
    return mensagens
