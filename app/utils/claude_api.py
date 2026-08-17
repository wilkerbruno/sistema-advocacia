"""
Chamada à API da Anthropic (Claude) usando a chave PRÓPRIA de cada empresa
cliente — BYOK, "Bring Your Own Key" (ver app/utils/agente_ia_router.py e
PENDENCIAS.md, seção sobre a escolha de provedor de IA por empresa).

Por que BYOK e não a JusControl repassar/cobrar markup sobre o uso da API:
os Termos Comerciais da Anthropic proíbem revenda dos Serviços sem acordo
de revenda expresso, e proíbem usar autenticação por assinatura para dar
acesso à API a terceiros — ou seja, o modelo "a empresa paga um pouco a
mais pra gente e a gente usa nossa própria chave por trás" não é permitido
sem um acordo específico com a Anthropic. Por isso cada empresa cadastra a
PRÓPRIA chave (gerada em https://console.anthropic.com/settings/keys) e é
cobrada DIRETAMENTE pela Anthropic pelo que usar — a JusControl nunca vê,
processa ou intermedeia esse pagamento, só guarda a chave cifrada
(app/utils/cofre.py) para fazer a chamada em nome da empresa.

Usa a API REST diretamente via `requests` (mesmo padrão de
app/utils/mercadopago.py), sem SDK extra.

⚠️ Não foi possível testar uma chamada real de dentro do ambiente de
geração de código (sem acesso de rede à API da Anthropic a partir daqui),
mas o formato de request/response segue a documentação pública da Messages
API (https://docs.claude.com/en/api/messages). Teste com uma chave real
(pode usar o botão "Testar chave" da tela de Integrações, que usa
`validar_chave` abaixo) depois do deploy.
"""
import requests

API_BASE = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# Deixado como string simples (não um enum fechado) de propósito: a
# Anthropic lança/aposenta modelos com mais frequência do que este código é
# revisado. O admin da empresa pode digitar o identificador exato do
# modelo que quiser na tela de Integrações — este é só o valor sugerido
# pré-preenchido no formulário. Confira o identificador atual em
# https://docs.claude.com/en/docs/about-claude/models antes de usar.
MODELO_PADRAO = "claude-sonnet-4-5"
TIMEOUT_SEGUNDOS = 60


class ClaudeIndisponivelError(Exception):
    """Erro amigável — chave ausente/inválida, limite de uso/crédito
    esgotado na conta Anthropic da própria empresa, ou erro de rede. Nunca
    deixa o erro cru da API vazar pra tela do usuário final."""


def gerar_resposta(system, mensagens_api, api_key, modelo=None, max_tokens=None):
    """
    system: string do system prompt.
    mensagens_api: lista de {"role": "user"|"assistant", "content": str}
    — mesmo formato usado por app/utils/ia_local.py, para que
    app/utils/agente_ia_router.py possa trocar de provedor sem o
    chamador saber a diferença.
    api_key: chave da Anthropic já DECIFRADA (texto puro) da empresa.

    Devolve o texto da resposta (str), sem espaços nas pontas.
    """
    if not api_key:
        raise ClaudeIndisponivelError("Nenhuma chave de API do Claude configurada.")

    payload = {
        "model": modelo or MODELO_PADRAO,
        "max_tokens": max_tokens or 700,
        "system": system,
        "messages": [{"role": m["role"], "content": m["content"]} for m in mensagens_api],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }

    try:
        resposta = requests.post(API_BASE, json=payload, headers=headers, timeout=TIMEOUT_SEGUNDOS)
    except requests.RequestException as e:
        raise ClaudeIndisponivelError(f"Falha de conexão com a API da Anthropic: {e}") from e

    if resposta.status_code == 401:
        raise ClaudeIndisponivelError(
            "A Anthropic recusou a chave de API (401 — chave inválida, revogada ou digitada errada). "
            "Confira a chave em \"Minhas Integrações\" ou gere uma nova em "
            "https://console.anthropic.com/settings/keys."
        )
    if resposta.status_code == 403:
        raise ClaudeIndisponivelError(
            "A Anthropic recusou a requisição por permissão (403) — confira se a chave tem acesso ao "
            "modelo selecionado e se a conta Anthropic da empresa está em dia."
        )
    if resposta.status_code == 429:
        raise ClaudeIndisponivelError(
            "A conta Anthropic desta empresa atingiu o limite de uso/taxa no momento (429). Tente "
            "novamente em instantes, ou confira os limites da própria conta no console da Anthropic."
        )
    if resposta.status_code == 400:
        raise ClaudeIndisponivelError(f"A Anthropic recusou a requisição (400): {_extrair_erro(resposta)}")
    if resposta.status_code != 200:
        raise ClaudeIndisponivelError(
            f"A API da Anthropic respondeu {resposta.status_code} de forma inesperada: {resposta.text[:300]}"
        )

    corpo = resposta.json()
    blocos = corpo.get("content") or []
    texto = "".join(b.get("text", "") for b in blocos if b.get("type") == "text")
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
    conteúdo da mensagem de teste. Levanta ClaudeIndisponivelError se a
    chave não funcionar; devolve True se funcionar.
    """
    gerar_resposta(
        system="Responda apenas com a palavra ok.",
        mensagens_api=[{"role": "user", "content": "teste de conexão"}],
        api_key=api_key, modelo=modelo, max_tokens=5,
    )
    return True
