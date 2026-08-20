"""
Motor local do Agente de IA jurídica (Operação/Gestão/Negócios) e da Análise
de processo (resumo dos autos / rascunho de petição, ver
app/utils/analise_processo_ia.py) — modelo de até 2B parâmetros
(Qwen2.5-1.5B-Instruct, quantizado em GGUF) rodando dentro do próprio
servidor via llama-cpp-python. Sem chave de API, sem custo por mensagem, sem
dado do escritório saindo do servidor.

Existe um modelo maior/mais robusto pronto (Qwen3-4B, ~2,5 GB) em
baixar_modelo_ia_local.py, mas está desligado por padrão por falta de RAM
sobrando no servidor de produção atual — ver PENDENCIAS.md, seção -6, para
o passo a passo de como ativar quando (se) o plano do servidor crescer.

Ver baixar_modelo_ia_local.py para o download dos pesos (feito automaticamente
durante o build da imagem Docker — ver Dockerfile) e PENDENCIAS.md para o
registro da decisão e dos trade-offs.

⚠️ Trade-off consciente, não bug: um modelo desse tamanho é sensivelmente
mais fraco que uma API de LLM grande — alucina mais, principalmente em
português e em raciocínio jurídico mais elaborado. Os system prompts em
app/routes/agente_ia.py já instruem o modelo a nunca inventar número fora
do contexto real injetado e a sinalizar quando uma resposta é só sugestão a
validar — mas revisão humana continua sendo necessária, ainda mais aqui do
que já era com o Claude.

⚠️ Custo de RAM: o modelo fica carregado (lazy, na primeira mensagem que
cada worker do gunicorn atender) em memória por processo — ver comentário
sobre número de workers no Dockerfile.
"""
import os
import threading

from flask import current_app

_modelo = None
_lock = threading.Lock()


class ModeloIndisponivelError(Exception):
    """Erro claro e amigável — nunca deixa a conversa travada nem finge resposta."""


def _caminho_modelo():
    return current_app.config.get("IA_LOCAL_MODELO_PATH")


def modelo_disponivel():
    """
    Checagem rápida (só olha se o arquivo existe, não carrega o modelo em
    memória) — usada nas telas do Agente de IA pra avisar o usuário sem
    pagar o custo de carregar o modelo só para exibir a página.
    """
    caminho = _caminho_modelo()
    return bool(caminho and os.path.isfile(caminho))


def _obter_modelo():
    global _modelo
    if _modelo is not None:
        return _modelo
    with _lock:
        if _modelo is not None:  # outra chamada pode ter carregado enquanto esperava o lock
            return _modelo
        # ⚠️ Mensagens abaixo (ImportError e arquivo ausente) cobrem também o
        # cenário ATUAL, intencional (não é erro de deploy): o motor de IA
        # local está temporariamente desativado — ver PENDENCIAS.md, seção
        # -30 — porque pesava demais para o servidor de produção atual. Por
        # isso o texto é direcionado ao usuário final (ex: usar a API do
        # Claude com chave própria em "Minhas Integrações"), em vez de
        # instruir a rodar comandos no servidor, que só confundiriam quem
        # está apenas usando o sistema pelo navegador.
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise ModeloIndisponivelError(
                "O modelo de IA local está temporariamente desativado neste servidor "
                "(mudança planejada, não é uma falha) — enquanto isso, use a API do "
                "Claude com chave própria da empresa em \"Minhas Integrações\" (menu do "
                "administrador), se disponível."
            ) from e

        caminho = _caminho_modelo()
        if not caminho or not os.path.isfile(caminho):
            raise ModeloIndisponivelError(
                "O modelo de IA local está temporariamente desativado neste servidor "
                "(mudança planejada, não é uma falha) — enquanto isso, use a API do "
                "Claude com chave própria da empresa em \"Minhas Integrações\" (menu do "
                "administrador), se disponível."
            )

        n_ctx = current_app.config.get("IA_LOCAL_CONTEXT_SIZE", 4096)
        n_threads = current_app.config.get("IA_LOCAL_THREADS")
        _modelo = Llama(
            model_path=caminho,
            n_ctx=n_ctx,
            n_threads=n_threads,
            verbose=False,
        )
        return _modelo


def gerar_resposta(system, mensagens_api, max_tokens=None):
    """
    system: string do system prompt (persona + contexto real do escritório
    já embutido, ver app/routes/agente_ia.py).
    mensagens_api: lista de {"role": "user"|"assistant", "content": str}.

    Devolve o texto da resposta (str), sem espaços nas pontas. Levanta
    ModeloIndisponivelError com mensagem amigável se o modelo não estiver
    pronto (ou se o pedido não couber de jeito nenhum na janela de contexto,
    ver abaixo) — nunca deixa exceção crua vazar pra tela do usuário.
    """
    modelo = _obter_modelo()
    max_tokens = max_tokens or current_app.config.get("IA_LOCAL_MAX_TOKENS_RESPOSTA", 700)
    n_ctx = current_app.config.get("IA_LOCAL_CONTEXT_SIZE", 4096)

    # Conta quantos tokens o prompt (system + instrução do usuário) ocupa DE
    # VERDADE, em vez de simplesmente pedir `max_tokens` de resposta sem
    # checar se ainda cabe na janela de contexto do modelo local (n_ctx).
    # Isso já causou um problema real: um pedido de rascunho de petição
    # longo e detalhado, somado ao digest do processo (ver
    # app/utils/analise_processo_ia.py), passava do limite de contexto — o
    # modelo, sem espaço de sobra pra "pensar" numa resposta nova, degenerava
    # em só ecoar de volta o texto do próprio pedido em vez de gerar a peça
    # (sintoma clássico de estouro de contexto num modelo pequeno, não um
    # bug de lógica). Agora o `max_tokens` pedido é sempre limitado ao que
    # realmente sobra de espaço — e se nem uma resposta mínima couber mais
    # (pedido do usuário já grande demais sozinho), avisa em vez de gerar
    # uma resposta capenga ou um eco do próprio prompt.
    texto_prompt = system + "\n\n" + "\n\n".join(m.get("content", "") for m in mensagens_api)
    try:
        n_prompt_tokens = len(modelo.tokenize(texto_prompt.encode("utf-8")))
    except Exception:
        n_prompt_tokens = len(texto_prompt) // 3  # estimativa grosseira, só se a contagem exata falhar

    MARGEM_SEGURANCA = 64  # tokens especiais de formatação do chat template, arredondamento etc.
    MINIMO_RESPOSTA_UTIL = 150  # resposta menor que isso não serve pra nada (viraria lixo cortado)
    espaco_disponivel = n_ctx - n_prompt_tokens - MARGEM_SEGURANCA

    if espaco_disponivel < MINIMO_RESPOSTA_UTIL:
        raise ModeloIndisponivelError(
            "O pedido ficou grande demais para o modelo de IA local processar de uma vez (já "
            "incluindo os dados do processo injetados automaticamente) — encurte a instrução que "
            "você escreveu e tente de novo. Se precisar de instruções bem detalhadas com "
            "frequência, considere usar a API do Claude com chave própria da empresa (\"Minhas "
            "Integrações\"), que tem uma janela de contexto bem maior."
        )

    max_tokens = min(max_tokens, espaco_disponivel)

    resposta = modelo.create_chat_completion(
        messages=[{"role": "system", "content": system}] + mensagens_api,
        max_tokens=max_tokens,
        temperature=0.3,
        # repeat_penalty: a biblioteca (llama-cpp-python) usa 1.0 por padrão
        # quando não é passado explicitamente — ou seja, NENHUMA penalidade
        # de repetição. Isso já causou um problema real: um rascunho de
        # petição de um processo com histórico repetitivo (várias
        # movimentações de texto quase idêntico, ex. vários "Ato
        # ordinatório") entrou num loop e devolveu a mesma frase repetida
        # dezenas de vezes até estourar o limite de tokens, em vez de gerar
        # a peça. 1.2 é um valor padrão bem estabelecido em modelos GGUF
        # pequenos pra desencorajar esse tipo de loop sem prejudicar
        # muito a qualidade do texto (valores muito altos, tipo 1.5+,
        # tendem a deixar o texto estranho/incoerente).
        repeat_penalty=1.2,
    )
    return resposta["choices"][0]["message"]["content"].strip()
