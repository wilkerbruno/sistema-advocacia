"""
Monitoramento de erros em produção via Sentry (PENDENCIAS.md, seção -49).

Mesmo padrão "degrada honestamente sem credencial" usado em SMTP_HOST,
WHATSAPP_BRIDGE_URL e DATAJUD_API_KEY (ver config.py): sem SENTRY_DSN
configurado, `inicializar_sentry()` simplesmente não faz nada — o sistema
continua funcionando exatamente igual, só sem reportar erro nenhum pra
lugar nenhum. Cadastro gratuito em https://sentry.io (o plano grátis já
cobre um volume razoável pra um escritório) — cole a DSN do projeto criado
lá na variável de ambiente SENTRY_DSN pra ativar.

O que é enviado pro Sentry, e o que NUNCA é:
- `send_default_pii=False` — nunca manda IP, cookie, corpo bruto da
  requisição nem cabeçalhos automaticamente. Decisão deliberada,
  consistente com o resto do sistema levando LGPD a sério (ver
  anonimização de cliente, base legal de tratamento etc.).
- `_before_send()` abaixo tira à força qualquer campo sensível (senha,
  csrf_token, token de API, CPF/CNPJ, chave do cofre de senha de
  processo...) que porventura viesse junto do contexto de uma exceção —
  segunda camada de proteção, mesmo com `send_default_pii` desligado.
- `identificar_usuario_atual()` manda só o ID numérico, o papel e a
  empresa/unidade do usuário logado — nunca nome, e-mail ou qualquer
  outro dado pessoal — suficiente pra saber "qual usuário/empresa bateu
  nesse erro" sem mandar dado de cliente nenhum pra um serviço terceiro.

Um único ponto de entrada cobre os três lugares onde erro pode acontecer,
sem precisar duplicar nada: os workers do gunicorn (processo web, via
`create_app()`), o worker da fila de IA em segundo plano
(`app/jobs/ia_jobs.py::_obter_app()` também chama `create_app()`) e os
scripts `.cron` (`enviar_lembretes_*.py`, `capturar_movimentacoes.py`
etc., que também chamam `create_app()`).

Amostragem de PERFORMANCE (traces) fica DESLIGADA por padrão
(`SENTRY_TRACES_SAMPLE_RATE=0`) — o objetivo aqui é capturar ERRO, não
rastrear performance; ligar tracing consome a cota gratuita do Sentry bem
mais rápido. Captura de erro não depende disso — fica sempre ligada
quando `SENTRY_DSN` está definido.
"""
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.rq import RqIntegration

# Comparação por substring (case-insensitive) contra o NOME do campo —
# de propósito mais abrangente que uma lista exata, pra pegar variações
# tipo "nova_senha"/"confirmar_senha" sem precisar listar cada uma.
TERMOS_CAMPO_SENSIVEL = (
    "senha", "csrf_token", "token", "authorization", "cofre_senha",
    "cpf_cnpj", "valor_criptografado", "secret", "cookie",
)


def _campo_sensivel(nome):
    nome_min = str(nome).lower()
    return any(termo in nome_min for termo in TERMOS_CAMPO_SENSIVEL)


def _remover_campos_sensiveis(valor):
    """Percorre dict/list recursivamente removendo qualquer chave sensível
    — usado no before_send abaixo como segunda camada de proteção, mesmo
    com send_default_pii=False já bloqueando a maior parte disso."""
    if isinstance(valor, dict):
        return {
            chave: "[removido]" if _campo_sensivel(chave) else _remover_campos_sensiveis(sub)
            for chave, sub in valor.items()
        }
    if isinstance(valor, list):
        return [_remover_campos_sensiveis(item) for item in valor]
    return valor


def _before_send(event, hint):
    request = event.get("request")
    if request:
        # "cookies" é tratado à parte, sempre removido por inteiro — as
        # CHAVES ali são nome de cookie ("session", "jc_device_id" etc.),
        # não têm por que bater com nenhum termo de TERMOS_CAMPO_SENSIVEL,
        # mas o VALOR de um cookie de sessão já é por si só equivalente a
        # uma senha (quem tiver o cookie está autenticado). send_default_pii
        # =False já deveria impedir isso de vir preenchido em primeiro
        # lugar — isto aqui é só a segunda camada de proteção.
        if "cookies" in request:
            request["cookies"] = "[removido]"
        for campo in ("data", "headers", "query_string"):
            if campo in request:
                request[campo] = _remover_campos_sensiveis(request[campo])
    return event


def inicializar_sentry(app):
    dsn = app.config.get("SENTRY_DSN")
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            FlaskIntegration(),
            # event_level="ERROR": só vira "erro" reportado no Sentry o que
            # já era logger.error()/logger.exception() no código (ver
            # app/utils/email.py e app/utils/whatsapp.py, que já usam
            # current_app.logger.warning para falha esperada/degradação
            # graciosa — warning não sobe pro Sentry, de propósito, senão
            # toda vez que o SMTP estivesse fora do ar viraria alerta).
            LoggingIntegration(level=None, event_level="ERROR"),
            # Cobre o worker da fila de IA (app/jobs/ia_jobs.py) — mas
            # aquele código de propósito CAPTURA a exceção pra nunca deixar
            # a mensagem travada em "processando" (ver comentário lá); por
            # isso o worker RQ nunca vê o job como "falho" de verdade.
            # Reforçado com sentry_sdk.capture_exception(e) explícito nos
            # blocos "except Exception" de ia_jobs.py, pra não perder essa
            # visibilidade mesmo com o erro sendo tratado com carinho pro
            # usuário.
            RqIntegration(),
        ],
        environment=app.config.get("SENTRY_ENVIRONMENT") or "producao",
        release=app.config.get("SENTRY_RELEASE") or None,
        traces_sample_rate=app.config.get("SENTRY_TRACES_SAMPLE_RATE") or 0.0,
        send_default_pii=False,
        before_send=_before_send,
    )


def identificar_usuario_atual():
    """
    Chamado a cada requisição autenticada (ver app/__init__.py), depois de
    `inicializar_sentry()` já ter rodado no boot da aplicação — mas
    funciona (sem quebrar nada) mesmo se o Sentry nunca tiver sido
    inicializado: `sentry_sdk.set_user`/`set_tag` são no-op nesse caso,
    mesmo padrão "degrada sem credencial" do resto deste módulo.
    """
    from flask_login import current_user

    if not current_user.is_authenticated:
        return

    sentry_sdk.set_user({
        "id": current_user.id,
        "papel": current_user.papel,
    })
    sentry_sdk.set_tag("empresa_id", current_user.empresa_id_atual)
    sentry_sdk.set_tag("unidade_id", current_user.unidade_id)
    sentry_sdk.set_tag("papel", current_user.papel)
