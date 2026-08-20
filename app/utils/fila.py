"""
Fila de processamento em segundo plano (RQ + Redis) — ver PENDENCIAS.md,
seção -32.

Por que existe: a geração de resposta do Agente de IA (chat de portfólio,
app/routes/agente_ia.py) e da Análise de processo (resumo dos autos /
rascunho de petição, app/routes/processos.py::gerar_analise_ia) podia levar
minutos rodando o modelo local por CPU — isso acontecia DENTRO do ciclo de
requisição/resposta normal, ocupando um worker inteiro do gunicorn (que já
é escasso neste servidor, ver Dockerfile) até terminar. Com só 1 worker
(ver PENDENCIAS.md, seção -31), isso significava o sistema INTEIRO travado
pra todo mundo enquanto qualquer pessoa usava a IA.

Como funciona agora: a rota web só valida o pedido, grava um registro
"processando" no banco e devolve a resposta na hora (a página recarrega
sozinha a cada poucos segundos até o resultado ficar pronto — ver
conversa.html e detalhe.html). Quem realmente chama o modelo é um processo
separado (`rq worker`, iniciado em segundo plano pelo entrypoint.sh, ver
docker/entrypoint.sh) — o worker do gunicorn nunca fica bloqueado.

Onde o Redis roda: dentro do MESMO container da aplicação (ver Dockerfile),
por simplicidade — não exige configurar um serviço novo no EasyPanel. Isso
tem uma limitação consciente: se o container reiniciar (redeploy, restart
manual) enquanto um job está na fila ou em processamento, esse job específico
se perde (a mensagem/análise fica "processando" pra sempre, sem re-tentar
sozinha) — aceitável para este caso de uso (gerar de novo é só clicar
"enviar"/"gerar análise" outra vez), mas não seria adequado se a fila
guardasse algo que não pudesse ser perdido/refeito. Se um dia isso incomodar
na prática, a evolução natural é apontar REDIS_URL (ver config.py) para um
serviço de Redis separado, com persistência própria.
"""
from redis import Redis
from rq import Queue

from flask import current_app

FILA_PADRAO = "jus_control_ia"

_redis = None
_fila = None


def obter_fila():
    """
    Devolve o objeto Queue do RQ, conectado ao Redis configurado em
    REDIS_URL (ver config.py). Criado sob demanda (lazy) e reaproveitado
    entre chamadas dentro do mesmo processo — tanto faz se é a
    aplicação web (só publica jobs) ou o worker do RQ (também precisa de
    uma Queue pra alguns comandos internos).
    """
    global _redis, _fila
    if _fila is None:
        url = current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
        _redis = Redis.from_url(url)
        _fila = Queue(FILA_PADRAO, connection=_redis)
    return _fila


def enfileirar(func_path, *args, job_timeout=420, **kwargs):
    """
    Publica um job na fila. `func_path` é uma string "modulo.funcao" (não
    a função em si) — é assim que o RQ recomenda pra evitar problemas de
    import circular/pickling entre o processo web e o processo worker (ver
    app/jobs/agente_ia_jobs.py para as funções reais).

    job_timeout=420 (7 min): folga confortável acima do --timeout 300 do
    gunicorn (ver Dockerfile) — aqui não tem esse limite do gunicorn, mas
    um teto evita que um job travado (ex.: modelo entrando em loop) fique
    "processando" pra sempre sem nunca marcar erro.
    """
    fila = obter_fila()
    return fila.enqueue(func_path, *args, job_timeout=job_timeout, **kwargs)
