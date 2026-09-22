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


def processar_mensagem_agente_ia(mensagem_id, empresa_id, usuario_id, system, mensagens_api, max_tokens=None):
    """
    Gera a resposta de uma mensagem do Agente de IA de portfólio (chat,
    ver app/routes/agente_ia.py) e grava direto na linha MensagemAgenteIA
    já criada (com status="processando") pela rota web.

    Ferramentas (tool-calling — ver app/utils/agente_ia_ferramentas.py):
    depois de cada chamada ao modelo, checa se a resposta é um PEDIDO de
    ferramenta (em vez de uma resposta final pro usuário); se for, executa
    a ferramenta e alimenta o resultado de volta pro modelo, num laço de
    até MAX_ITERACOES_FERRAMENTAS rodadas. Isso roda AQUI (no worker, fora
    da requisição web) porque cada rodada pode chamar o modelo de novo —
    lento no motor local — mas por isso mesmo o worker não tem
    `current_user`/sessão nenhuma: `usuario_id` é carregado direto do
    banco (dentro do app_context aberto logo abaixo) e repassado pras
    ferramentas, que aplicam o MESMO escopo de unidade/empresa de sempre
    (ver app/utils/acesso.py) a partir desse usuário carregado, nunca de
    um `current_user` que não existe aqui.
    """
    app = _obter_app()
    with app.app_context():
        from app.models import MensagemAgenteIA, Empresa, Usuario
        from app.utils import agente_ia_router, agente_ia_ferramentas

        mensagem = db.session.get(MensagemAgenteIA, mensagem_id)
        if mensagem is None:
            return  # conversa/mensagem apagada enquanto o job esperava na fila — nada a fazer

        empresa = db.session.get(Empresa, empresa_id) if empresa_id else None
        usuario = db.session.get(Usuario, usuario_id) if usuario_id else None

        resposta_texto = ""
        try:
            mensagens = list(mensagens_api)
            for _ in range(agente_ia_ferramentas.MAX_ITERACOES_FERRAMENTAS):
                resposta_texto = agente_ia_router.gerar_resposta(empresa, system, mensagens, max_tokens=max_tokens)
                if not resposta_texto:
                    resposta_texto = "[O agente respondeu vazio — tente reformular a pergunta.]"
                    break

                # Sem usuário carregado (não deveria acontecer numa mensagem
                # nova, só numa fila antiga de antes desta mudança), não dá
                # pra aplicar escopo nenhum — trata como resposta final,
                # nunca executa ferramenta sem saber de quem é o escopo.
                chamada = agente_ia_ferramentas.extrair_chamada_ferramenta(resposta_texto) if usuario else None
                if chamada is None:
                    break

                resultado_ferramenta = agente_ia_ferramentas.executar_ferramenta(chamada, usuario)
                mensagens = mensagens + [
                    {"role": "assistant", "content": resposta_texto},
                    {"role": "user", "content": (
                        f"[Resultado da ferramenta \"{chamada['ferramenta']}\"]\n{resultado_ferramenta}\n\n"
                        "Agora responda ao usuário com base nesse resultado (ou use outra ferramenta, "
                        "se ainda precisar de outro dado)."
                    )},
                ]
            else:
                # Esgotou as iterações e o modelo ainda estava pedindo
                # ferramenta — força uma resposta final em texto em vez de
                # devolver um bloco JSON cru pro usuário ver na tela.
                mensagens = mensagens + [{
                    "role": "user",
                    "content": "Responda agora em texto normal para o usuário, com o que já foi "
                               "consultado até aqui — não use mais nenhuma ferramenta.",
                }]
                resposta_texto = agente_ia_router.gerar_resposta(empresa, system, mensagens, max_tokens=max_tokens)
                if agente_ia_ferramentas.extrair_chamada_ferramenta(resposta_texto):
                    resposta_texto = ("Não consegui concluir a consulta com as ferramentas disponíveis — "
                                       "tente reformular a pergunta de forma mais direta.")
        except agente_ia_router.ProvedorIAIndisponivelError as e:
            resposta_texto = f"⚠️ Agente indisponível: {e}"
        except Exception as e:  # nunca deixa a mensagem travada em "processando" pra sempre
            # Reporta pro Sentry mesmo tratando com carinho pro usuário (ver
            # app/utils/monitoramento.py) — sem isso, um bug de verdade aqui
            # nunca apareceria em lugar nenhum, só como uma mensagem de erro
            # genérica na tela de quem perguntou.
            import sentry_sdk
            sentry_sdk.capture_exception(e)
            resposta_texto = f"⚠️ Não foi possível consultar o agente de IA agora: {e}"

        mensagem.conteudo = resposta_texto
        mensagem.status = "pronta"
        db.session.commit()


def processar_mensagem_suporte_ia(mensagem_id, empresa_id, system, mensagens_api, max_tokens=None):
    """
    Gera a resposta de uma pergunta do chat de suporte flutuante (ver
    app/routes/suporte_ia.py e app/utils/suporte_ia.py) — mesmo mecanismo
    de fila em segundo plano + polling do Agente de IA de portfólio
    (processar_mensagem_agente_ia acima), mas SEM ferramentas: o chat de
    suporte responde só com base no conteúdo de ajuda estático (ver
    app/utils/juscontrol_manual.py), nunca consulta nenhum dado real do
    escritório — por isso não recebe (nem precisa de) `usuario_id`.
    """
    app = _obter_app()
    with app.app_context():
        from app.models import MensagemSuporteIA, Empresa
        from app.utils import agente_ia_router

        mensagem = db.session.get(MensagemSuporteIA, mensagem_id)
        if mensagem is None:
            return  # sessão do widget encerrada/mensagem apagada enquanto esperava na fila

        empresa = db.session.get(Empresa, empresa_id) if empresa_id else None

        try:
            resposta_texto = agente_ia_router.gerar_resposta(empresa, system, mensagens_api, max_tokens=max_tokens)
            if not resposta_texto:
                resposta_texto = "Não consegui gerar uma resposta — tente reformular a pergunta."
        except agente_ia_router.ProvedorIAIndisponivelError as e:
            resposta_texto = f"⚠️ Suporte indisponível no momento: {e}"
        except Exception as e:
            import sentry_sdk
            sentry_sdk.capture_exception(e)
            resposta_texto = "⚠️ Não foi possível responder agora — tente novamente em instantes."

        mensagem.resposta = resposta_texto
        mensagem.status = "pronta"
        db.session.commit()


def processar_analise_processo_ia(analise_id, processo_id, tipo, instrucao, texto_referencia=None,
                                   tipo_peca=None, delimitacao_id=None, modelo_peca_id=None,
                                   legislacao_relacionada=None):
    """
    Gera o resumo/rascunho de petição de um processo (ver
    app/utils/analise_processo_ia.py::gerar_analise) e grava direto na
    linha AnaliseProcessoIA já criada (com status="processando", resultado
    vazio) pela rota web.

    `texto_referencia` (PENDENCIAS.md, seção -53): trecho de documento já
    extraído pela rota ANTES de enfileirar — o job nunca lê arquivo do
    disco, só recebe o texto já pronto (ver
    app/routes/processos.py::gerar_analise_ia).

    `tipo_peca`/`delimitacao_id` (PENDENCIAS.md, seção -101 — itens 4 e 7):
    `delimitacao_id` é o id da DelimitacaoObjeto já criada pela rota (o job
    recebe só o id, não o objeto, pelo mesmo motivo de sempre — nada do
    request/sessão web atravessa a fila) e é recarregada aqui dentro do
    app_context do worker.

    `modelo_peca_id` (item 10 — PENDENCIAS.md, seção -107): id do ModeloPeca
    já resolvido automaticamente pela rota (mesmo padrão de
    `delimitacao_id` — é só um id de linha do banco, sem I/O de disco
    nenhum, então recarregar aqui dentro do app_context do worker é
    suficiente, ao contrário de `texto_referencia` que precisa ser
    extraído de arquivo ANTES de enfileirar).

    `legislacao_relacionada` (item 8 — PENDENCIAS.md, seção -108): lista de
    dicts (título/ementa/data/link) já buscada no LexML pela rota, ANTES
    de enfileirar — mesmo motivo de sempre: o job nunca faz chamada de
    rede a serviço externo nenhum, só recebe dado já pronto (aqui é uma
    lista pequena de strings, serializa sem problema pelo RQ, ao contrário
    de um objeto de modelo do SQLAlchemy).
    """
    app = _obter_app()
    with app.app_context():
        from app.models import AnaliseProcessoIA, Processo, DelimitacaoObjeto, ModeloPeca
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

        delimitacao = db.session.get(DelimitacaoObjeto, delimitacao_id) if delimitacao_id else None
        modelo_peca = db.session.get(ModeloPeca, modelo_peca_id) if modelo_peca_id else None

        try:
            resultado, truncado = gerar_analise(processo, tipo, instrucao, texto_referencia=texto_referencia,
                                                 tipo_peca=tipo_peca, delimitacao=delimitacao,
                                                 modelo_peca=modelo_peca,
                                                 legislacao_relacionada=legislacao_relacionada)
            analise.resultado = resultado
            analise.digest_truncado = truncado
        except agente_ia_router.ProvedorIAIndisponivelError as e:
            analise.resultado = f"⚠️ Agente de IA indisponível: {e}"
        except ValueError as e:
            # validação já é feita antes de enfileirar (ver rota) — isso só
            # cobre uma corrida rara/edge-case, não deveria acontecer na prática.
            analise.resultado = f"⚠️ {e}"
        except Exception as e:
            # Mesmo motivo do capture_exception em processar_mensagem_agente_ia
            # acima — reporta pro Sentry sem deixar de tratar com carinho pro
            # usuário (ver app/utils/monitoramento.py).
            import sentry_sdk
            sentry_sdk.capture_exception(e)
            analise.resultado = f"⚠️ Não foi possível gerar a análise agora: {e}"

        analise.status = "pronta"
        db.session.commit()
