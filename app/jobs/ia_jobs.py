"""
Jobs de IA que rodam em segundo plano via RQ (ver app/utils/fila.py e
PENDENCIAS.md, seção -32) — o motivo de existirem é tirar a chamada ao
modelo (que pode levar minutos, rodando por CPU) de dentro do ciclo de
requisição/resposta do gunicorn.

Cada função aqui é referenciada pelo caminho "app.jobs.ia_jobs.nome_funcao"
(string) no `enfileirar(...)`, nunca importada e passada como objeto
função — é assim que o RQ evita depender do processo web e do processo
worker terem exatamente os mesmos objetos Python carregados em memória.

Cada função entra num `app.app_context()` (ver docstring de
app/jobs/__init__.py) — o worker é um processo separado, sem sessão de
login nem `current_user`. Por isso o trabalho de montar o `system
prompt`/contexto real (que depende de current_user/escopo do usuário) é
feito ANTES de enfileirar, ainda dentro da requisição web normal (é rápido
— só leitura de banco, não é a parte lenta) — o job só recebe texto já
pronto e faz a parte lenta de verdade: chamar o modelo.

A `app` em si é criada UMA ÚNICA VEZ por processo worker (ver
`_obter_app()` abaixo), não uma nova a cada job — de propósito: rodamos com
`--worker-class rq.worker.SimpleWorker` (ver docker/entrypoint.sh), que
processa jobs sem criar um processo filho novo pra cada um, exatamente pra
deixar o modelo de IA carregado uma vez só na memória entre mensagens (ver
app/utils/ia_local.py). Criar uma `Flask app`/engine do SQLAlchemy nova a
cada job, nesse cenário de processo de longa duração, vazaria uma conexão
de banco nova por job (o engine antigo só seria liberado quando o coletor
de lixo do Python decidisse rodar, não é garantido) — reaproveitar a mesma
`app` evita esse acúmulo.
"""
from app import create_app
from app.extensions import db

_app = None


def _obter_app():
    global _app
    if _app is None:
        _app = create_app()
    return _app


def processar_mensagem_agente_ia(mensagem_id, empresa_id, system, mensagens_api, max_tokens=None):
    """
    Gera a resposta de uma mensagem do Agente de IA de portfólio (chat,
    ver app/routes/agente_ia.py) e grava direto na linha MensagemAgenteIA
    já criada (com status="processando") pela rota web.
    """
    app = _obter_app()
    with app.app_context():
        from app.models import MensagemAgenteIA, Empresa
        from app.utils import agente_ia_router

        mensagem = db.session.get(MensagemAgenteIA, mensagem_id)
        if mensagem is None:
            return  # conversa/mensagem apagada enquanto o job esperava na fila — nada a fazer

        empresa = db.session.get(Empresa, empresa_id) if empresa_id else None

        try:
            resposta_texto = agente_ia_router.gerar_resposta(empresa, system, mensagens_api, max_tokens=max_tokens)
            if not resposta_texto:
                resposta_texto = "[O agente respondeu vazio — tente reformular a pergunta.]"
        except agente_ia_router.ProvedorIAIndisponivelError as e:
            resposta_texto = f"⚠️ Agente indisponível: {e}"
        except Exception as e:  # nunca deixa a mensagem travada em "processando" pra sempre
            resposta_texto = f"⚠️ Não foi possível consultar o agente de IA agora: {e}"

        mensagem.conteudo = resposta_texto
        mensagem.status = "pronta"
        db.session.commit()


def processar_analise_processo_ia(analise_id, processo_id, tipo, instrucao):
    """
    Gera o resumo/rascunho de petição de um processo (ver
    app/utils/analise_processo_ia.py::gerar_analise) e grava direto na
    linha AnaliseProcessoIA já criada (com status="processando", resultado
    vazio) pela rota web.
    """
    app = _obter_app()
    with app.app_context():
        from app.models import AnaliseProcessoIA, Processo
        from app.utils import agente_ia_router
        from app.utils.analise_processo_ia import gerar_analise

        analise = db.session.get(AnaliseProcessoIA, analise_id)
        if analise is None:
            return  # análise apagada enquanto o job esperava na fila — nada a fazer

        processo = db.session.get(Processo, processo_id)
        if processo is None:
            analise.resultado = "⚠️ Não foi possível gerar: o processo foi removido enquanto a análise estava na fila."
            analise.status = "pronta"
            db.session.commit()
            return

        try:
            resultado, truncado = gerar_analise(processo, tipo, instrucao)
            analise.resultado = resultado
            analise.digest_truncado = truncado
        except agente_ia_router.ProvedorIAIndisponivelError as e:
            analise.resultado = f"⚠️ Agente de IA indisponível: {e}"
        except ValueError as e:
            # validação já é feita antes de enfileirar (ver rota) — isso só
            # cobre uma corrida rara/edge-case, não deveria acontecer na prática.
            analise.resultado = f"⚠️ {e}"
        except Exception as e:
            analise.resultado = f"⚠️ Não foi possível gerar a análise agora: {e}"

        analise.status = "pronta"
        db.session.commit()
