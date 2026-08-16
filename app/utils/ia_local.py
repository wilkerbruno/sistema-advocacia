"""
Motor local do Agente de IA jurídica (Operação/Gestão/Negócios) — modelo de
até 2B parâmetros (Qwen2.5-1.5B-Instruct, quantizado em GGUF) rodando dentro
do próprio servidor via llama-cpp-python. Sem chave de API, sem custo por
mensagem, sem dado do escritório saindo do servidor.

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
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise ModeloIndisponivelError(
                "Biblioteca 'llama-cpp-python' não instalada no servidor — rode "
                "'pip install -r requirements.txt' e reinicie a aplicação."
            ) from e

        caminho = _caminho_modelo()
        if not caminho or not os.path.isfile(caminho):
            raise ModeloIndisponivelError(
                f"Modelo de IA local não encontrado em '{caminho}'. Rode "
                "'python baixar_modelo_ia_local.py' no servidor para baixar os "
                "pesos (~1,1 GB, feito uma única vez; já roda sozinho durante o "
                "build da imagem Docker — se está faltando, o build pode ter "
                "falhado nessa etapa, veja o log do deploy)."
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
    pronto — nunca deixa exceção crua vazar pra tela do usuário.
    """
    modelo = _obter_modelo()
    max_tokens = max_tokens or current_app.config.get("IA_LOCAL_MAX_TOKENS_RESPOSTA", 700)

    resposta = modelo.create_chat_completion(
        messages=[{"role": "system", "content": system}] + mensagens_api,
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return resposta["choices"][0]["message"]["content"].strip()
