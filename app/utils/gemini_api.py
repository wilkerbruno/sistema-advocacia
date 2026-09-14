"""
Chamada à API do Gemini (Google) usando a chave PRÓPRIA de cada empresa
cliente — BYOK, "Bring Your Own Key", terceiro provedor do Agente de IA
ao lado do modelo local grátis e do Claude BYOK (ver
app/utils/agente_ia_router.py e PENDENCIAS.md).

Por que BYOK com FATURAMENTO ATIVADO na conta Google, e não o nível
gratuito ("Unpaid Services") da API: os Termos Adicionais de Serviço da
API do Gemini (https://ai.google.dev/gemini-api/terms, seção "Uso de
Dados — Serviços Não Pagos") dizem textualmente que o Google usa o
conteúdo enviado ao nível gratuito "para fornecer, melhorar e desenvolver
produtos e serviços do Google" e que "revisores humanos podem ler, anotar
e processar" esse conteúdo — o próprio texto pede explicitamente "não
envie informações sensíveis, confidenciais ou pessoais para os Serviços
Não Pagos". Dado de processo (nome de cliente, teor de petição, etc.) é
exatamente esse tipo de informação — incompatível com sigilo profissional
e LGPD. Já no nível PAGO (faturamento ativado na conta Google, mesmo que o
uso real fique bem barato — os modelos "Flash"/"Flash-Lite" custam
centavos de dólar por milhão de tokens), a mesma política diz que o
Google NÃO usa os prompts/respostas para melhorar produtos — mesma
garantia que já vale para o Claude BYOK. Por isso este módulo não aceita
nenhum "modo gratuito": a chave configurada aqui deve ser de um projeto do
Google AI Studio / Google Cloud com faturamento ativo.

Mesma razão de BYOK (não markup) do app/utils/claude_api.py: cada empresa
cadastra a PRÓPRIA chave (gerada em https://aistudio.google.com/apikey) e
é cobrada DIRETAMENTE pelo Google pelo que usar — a JusControl nunca vê,
processa ou intermedeia esse pagamento, só guarda a chave cifrada
(app/utils/cofre.py) para fazer a chamada em nome da empresa.

Usa a API REST diretamente via `requests` (mesmo padrão de claude_api.py e
app/utils/mercadopago.py), sem SDK extra. Endpoint `generateContent`
(v1beta) — é o método estável e amplamente documentado da API do Gemini;
existe também um endpoint mais novo ("Interactions API") em fase de
introdução, mas generateContent segue sendo o caminho oficial recomendado
e mais testado em produção por terceiros, por isso a escolha aqui.

⚠️ Não foi possível testar uma chamada real de dentro do ambiente de
geração de código (sem acesso de rede à API do Google a partir daqui),
mas o formato de request/response segue a documentação pública
(https://ai.google.dev/api/generate-content). Teste com uma chave real
(botão "Testar chave" da tela de Integrações, que usa `validar_chave`
abaixo) depois do deploy.
"""
import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
# Deixado como string simples (não um enum fechado) de propósito — mesma
# razão do MODELO_PADRAO em claude_api.py: o Google lança/aposenta/renomeia
# modelos Gemini com bastante frequência. O admin da empresa pode digitar o
# identificador exato do modelo que quiser na tela de Integrações — este é
# só o valor sugerido pré-preenchido no formulário. Confira o identificador
# atual (e o preço) em https://ai.google.dev/gemini-api/docs/models e
# https://ai.google.dev/gemini-api/docs/pricing antes de usar — "flash" é o
# equilíbrio custo/qualidade recomendado; "flash-lite" é a opção mais barata
# ainda, se o volume de uso for uma preocupação maior que qualidade.
MODELO_PADRAO = "gemini-2.5-flash"
TIMEOUT_SEGUNDOS = 60


class GeminiIndisponivelError(Exception):
    """Erro amigável — chave ausente/inválida, cota/faturamento da conta
    Google da própria empresa esgotado, modelo inexistente, conteúdo
    bloqueado pelos filtros de segurança do Google, ou erro de rede. Nunca
    deixa o erro cru da API vazar pra tela do usuário final."""


def gerar_resposta(system, mensagens_api, api_key, modelo=None, max_tokens=None):
    """
    system: string do system prompt.
    mensagens_api: lista de {"role": "user"|"assistant", "content": str}
    — mesmo formato usado por app/utils/ia_local.py e claude_api.py, para
    que app/utils/agente_ia_router.py possa trocar de provedor sem o
    chamador saber a diferença. A API do Gemini usa "model" em vez de
    "assistant" para o papel da IA — a conversão é feita aqui dentro.
    api_key: chave do Google AI Studio já DECIFRADA (texto puro) da
    empresa (projeto com faturamento ativo — ver docstring do módulo).

    Devolve o texto da resposta (str), sem espaços nas pontas.
    """
    if not api_key:
        raise GeminiIndisponivelError("Nenhuma chave de API do Gemini configurada.")

    modelo = modelo or MODELO_PADRAO
    url = f"{API_BASE}/{modelo}:generateContent"

    payload = {
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in mensagens_api
        ],
        "system_instruction": {"parts": [{"text": system}]},
        "generationConfig": {"maxOutputTokens": max_tokens or 700},
    }
    headers = {
        "x-goog-api-key": api_key,
        "content-type": "application/json",
    }

    try:
        resposta = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_SEGUNDOS)
    except requests.RequestException as e:
        raise GeminiIndisponivelError(f"Falha de conexão com a API do Gemini: {e}") from e

    if resposta.status_code == 400:
        detalhe = _extrair_erro(resposta)
        if "api key not valid" in detalhe.lower() or "api_key_invalid" in detalhe.lower():
            raise GeminiIndisponivelError(
                "O Google recusou a chave de API (400 — chave inválida, revogada ou digitada errada). "
                "Confira a chave em \"Minhas Integrações\" ou gere uma nova em "
                "https://aistudio.google.com/apikey."
            )
        raise GeminiIndisponivelError(f"O Google recusou a requisição (400): {detalhe}")
    if resposta.status_code == 403:
        raise GeminiIndisponivelError(
            "O Google recusou a requisição por permissão (403) — confira se a chave tem acesso ao "
            "modelo selecionado e se o faturamento está ativo no projeto do Google AI Studio/Cloud da "
            "empresa (o nível gratuito não é aceito aqui — ver aviso no topo de app/utils/gemini_api.py)."
        )
    if resposta.status_code == 404:
        raise GeminiIndisponivelError(
            f"O Google não encontrou o modelo \"{modelo}\" (404) — confira o identificador exato em "
            "https://ai.google.dev/gemini-api/docs/models."
        )
    if resposta.status_code == 429:
        raise GeminiIndisponivelError(
            "A conta Google desta empresa atingiu o limite de uso/taxa no momento (429). Tente "
            "novamente em instantes, ou confira os limites da própria conta em "
            "https://aistudio.google.com/rate-limit."
        )
    if resposta.status_code != 200:
        raise GeminiIndisponivelError(
            f"A API do Gemini respondeu {resposta.status_code} de forma inesperada: {resposta.text[:300]}"
        )

    corpo = resposta.json()
    candidatos = corpo.get("candidates") or []
    if not candidatos:
        motivo_bloqueio = (corpo.get("promptFeedback") or {}).get("blockReason")
        if motivo_bloqueio:
            raise GeminiIndisponivelError(
                f"O Gemini bloqueou esta requisição pelos próprios filtros de segurança (motivo: "
                f"{motivo_bloqueio}) — tente reformular a mensagem/pergunta."
            )
        raise GeminiIndisponivelError("O Gemini não devolveu nenhuma resposta para esta requisição.")

    partes = ((candidatos[0].get("content") or {}).get("parts")) or []
    texto = "".join(p.get("text", "") for p in partes)
    return texto.strip()


def _extrair_erro(resposta):
    try:
        return (resposta.json().get("error") or {}).get("message") or resposta.text[:300]
    except ValueError:
        return resposta.text[:300]


def validar_chave(api_key, modelo=None):
    """
    Faz uma chamada mínima (5 tokens de resposta) só pra confirmar que a
    chave funciona — usada pelo botão "Testar/salvar chave" da tela de
    Integrações (app/routes/integracoes.py). Nunca guarda nem loga o
    conteúdo da mensagem de teste. Levanta GeminiIndisponivelError se a
    chave não funcionar (incluindo o caso de nível gratuito sem
    faturamento — ver docstring do módulo); devolve True se funcionar.
    """
    gerar_resposta(
        system="Responda apenas com a palavra ok.",
        mensagens_api=[{"role": "user", "content": "teste de conexão"}],
        api_key=api_key, modelo=modelo, max_tokens=5,
    )
    return True
