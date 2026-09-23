import os
import uuid
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from app.extensions import db, login_manager, csrf

NOME_COOKIE_DISPOSITIVO = "jc_device_id"
DURACAO_COOKIE_DISPOSITIVO = 60 * 60 * 24 * 730  # ~2 anos


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Monitoramento de erros (Sentry) — ver app/utils/monitoramento.py e
    # PENDENCIAS.md, seção -49. Sem SENTRY_DSN configurado, não faz nada.
    # Cedo de propósito: quanto mais cedo no boot, mais coisa fica coberta
    # se algo quebrar logo na inicialização.
    from app.utils.monitoramento import inicializar_sentry
    inicializar_sentry(app)

    # EasyPanel (e a maioria dos hosts em nuvem) coloca o app atrás de um
    # proxy reverso. Sem isso, request.remote_addr sempre traria o IP
    # interno do proxy, não o IP real de quem acessou — o que estragaria
    # tanto o log de IP quanto a tentativa de resolução de MAC (ver
    # app/utils/rede.py). x_for=1 confia em um único proxy à frente
    # (o padrão do EasyPanel); ajuste se houver mais de um proxy encadeado.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.models import Usuario

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Usuario, int(user_id))

    # ---------------------- Identificador de dispositivo (auditoria) ----------------------
    # O MAC address só é descobrível na rede local (ver app/utils/rede.py) —
    # inútil pra usuário acessando pela internet. Isso aqui funciona sempre:
    # cookie de 1ª parte, aleatório, gerado no primeiro acesso de cada
    # navegador, guardado em app.utils.notificacoes.registrar_log() junto
    # com o log de auditoria. Registrado ANTES do bloqueio de licença, pra
    # já existir mesmo em ações que acontecem antes de qualquer verificação
    # (ex: tentativa de login).
    @app.before_request
    def preparar_dispositivo_id():
        from flask import g, request as req
        dispositivo_id = req.cookies.get(NOME_COOKIE_DISPOSITIVO)
        g.dispositivo_novo = not dispositivo_id
        g.dispositivo_id = dispositivo_id or uuid.uuid4().hex

    # Anexa usuário/empresa/unidade (nunca nome, e-mail ou outro dado
    # pessoal) a todo erro reportado ao Sentry nesta requisição — ver
    # app/utils/monitoramento.py. Não faz nada se SENTRY_DSN não estiver
    # configurado.
    @app.before_request
    def identificar_usuario_no_monitoramento():
        from app.utils.monitoramento import identificar_usuario_atual
        identificar_usuario_atual()

    @app.after_request
    def persistir_dispositivo_id(response):
        from flask import g
        if getattr(g, "dispositivo_novo", False):
            response.set_cookie(
                NOME_COOKIE_DISPOSITIVO, g.dispositivo_id,
                max_age=DURACAO_COOKIE_DISPOSITIVO, httponly=True, samesite="Lax",
            )
        return response

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.clientes import clientes_bp
    from app.routes.processos import processos_bp
    from app.routes.financeiro import financeiro_bp
    from app.routes.tarefas import tarefas_bp
    from app.routes.admin import admin_bp
    from app.routes.api import api_bp
    from app.routes.governanca import governanca_bp
    from app.routes.api_integracao import api_integracao_bp
    from app.routes.plataforma import plataforma_bp
    from app.routes.licenciamento import licenciamento_bp
    from app.routes.agenda import agenda_bp
    from app.routes.timesheet import timesheet_bp
    from app.routes.agente_ia import agente_ia_bp
    from app.routes.integracoes import integracoes_bp
    from app.routes.agente_local import agente_local_bp
    from app.routes.agente_local_api import agente_local_api_bp
    from app.routes.conta import conta_bp
    from app.routes.leads import leads_bp
    from app.routes.captacao_oab import captacao_oab_bp
    from app.routes.captacao_dou import captacao_dou_bp
    from app.routes.rotina import rotina_bp
    from app.routes.suporte_ia import suporte_ia_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(clientes_bp, url_prefix="/clientes")
    app.register_blueprint(processos_bp, url_prefix="/processos")
    app.register_blueprint(financeiro_bp, url_prefix="/financeiro")
    app.register_blueprint(tarefas_bp, url_prefix="/tarefas")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(governanca_bp, url_prefix="/governanca")
    app.register_blueprint(api_integracao_bp, url_prefix="/api/v1")
    app.register_blueprint(plataforma_bp, url_prefix="/plataforma")
    app.register_blueprint(licenciamento_bp)
    app.register_blueprint(agenda_bp, url_prefix="/agenda")
    app.register_blueprint(timesheet_bp, url_prefix="/timesheet")
    app.register_blueprint(agente_ia_bp, url_prefix="/agente-ia")
    app.register_blueprint(integracoes_bp)
    app.register_blueprint(agente_local_bp)
    app.register_blueprint(agente_local_api_bp, url_prefix="/api/agente-local")
    app.register_blueprint(conta_bp)
    app.register_blueprint(leads_bp, url_prefix="/leads")
    app.register_blueprint(captacao_oab_bp)
    app.register_blueprint(captacao_dou_bp)
    app.register_blueprint(rotina_bp)
    app.register_blueprint(suporte_ia_bp, url_prefix="/suporte-ia")

    # A API de integração (/api/v1/*) é autenticada por token Bearer, não
    # por cookie de sessão — CSRF protege contra um navegador enviar um
    # cookie de sessão automaticamente sem o usuário perceber, o que não
    # se aplica aqui (não há cookie nenhum envolvido). Isenta a mais por
    # segurança: mesmo se um POST for adicionado aqui no futuro, não faz
    # sentido exigir token CSRF de quem já está autenticado por token.
    csrf.exempt(api_integracao_bp)
    # Mesmo raciocínio para a API do Agente Local (/api/agente-local/*):
    # autenticada por Bearer token (AgenteLocalPareado), sem cookie de
    # sessão envolvido.
    csrf.exempt(agente_local_api_bp)

    # ---------------------- Autenticador obrigatório (2FA) ----------------------
    # PENDENCIAS.md, seção -104 — "toda vez que o cliente for logar deve
    # pedir o autenticador". Roda ANTES do bloqueio de licença/módulo de
    # propósito: identidade vem antes de cobrança. Bloqueia QUALQUER tela
    # (inclusive do admin desenvolvedor — este gate não tem exceção de
    # papel, ao contrário dos dois de baixo) até o usuário confirmar o
    # autenticador pelo menos uma vez; a partir daí nunca mais bloqueia (a
    # exigência do CÓDIGO a cada login já acontece antes, em
    # app/routes/auth.py::login/verificar_totp — isto aqui só cobre quem
    # ainda não tem NADA configurado).
    #
    # Só entra em ação se `totp_disponivel()` (TOTP_CIFRA_KEY configurada,
    # ver config.py) — sem isso, a funcionalidade inteira fica desligada e
    # este gate nunca bloqueia nada (ver docstring de app/utils/totp.py:
    # nunca travar o sistema inteiro por uma variável de ambiente que
    # porventura não foi configurada no deploy).
    ENDPOINTS_LIBERADOS_SEM_2FA = {
        "static", "auth.logout",
        "conta.configurar_totp", "conta.confirmar_totp", "conta.reconfigurar_totp",
    }

    @app.before_request
    def exigir_autenticador_configurado():
        from flask import request as req, redirect as redir, url_for as urlf, flash as fl
        from flask_login import current_user as cu
        from app.utils.totp import totp_disponivel

        if req.endpoint in ENDPOINTS_LIBERADOS_SEM_2FA or req.endpoint is None:
            return None
        if not cu.is_authenticated or not totp_disponivel():
            return None
        if cu.totp_configurado:
            return None

        fl("Por segurança, configure o autenticador (2FA) da sua conta antes de continuar — "
           "escaneie o QR code abaixo com um app como Google Authenticator ou Microsoft Authenticator.",
           "warning")
        return redir(urlf("conta.configurar_totp"))

    # ---------------------- Bloqueio por licença vencida ----------------------
    # Admin desenvolvedor e a empresa dona da plataforma nunca são bloqueados.
    # Demais empresas: se a licença não está ativa, só conseguem acessar
    # login/logout e a própria área de licenciamento (pra poder pagar).
    #
    # As três rotas de autenticador (`conta.configurar_totp`/`confirmar_totp`/
    # `reconfigurar_totp`) também ficam sempre liberadas aqui — sem isso, um
    # admin recém-cadastrado (licença "pendente_pagamento" por padrão) cairia
    # num vaivém infinito entre "configure o autenticador" (gate de 2FA,
    # acima) e "regularize sua licença" (este gate logo abaixo), porque cada
    # um mandaria de volta pra tela que o outro acabou de bloquear. Identidade
    # (2FA) sempre vem antes de cobrança (licença) — nunca o contrário.
    ENDPOINTS_SEMPRE_LIBERADOS = {
        "auth.login", "auth.logout", "static",
        "licenciamento.minha_licenca", "licenciamento.pagar_licenca",
        "licenciamento.pagamento_retorno", "licenciamento.webhook_mercadopago",
        "conta.configurar_totp", "conta.confirmar_totp", "conta.reconfigurar_totp",
    }

    @app.before_request
    def bloquear_empresa_sem_licenca_ativa():
        from flask import request as req, redirect as redir, url_for as urlf, flash as fl
        from flask_login import current_user as cu

        if req.endpoint in ENDPOINTS_SEMPRE_LIBERADOS or req.endpoint is None:
            return None
        if not cu.is_authenticated:
            return None
        if cu.is_admin_desenvolvedor:
            return None

        empresa = cu.empresa
        if empresa is None or empresa.dono_da_plataforma:
            return None

        licenca = empresa.licenca
        if licenca is not None and licenca.esta_ativa():
            return None

        if cu.is_admin:
            fl("A licença da sua empresa está vencida ou pendente de pagamento. "
               "Regularize para voltar a usar o sistema.", "danger")
            return redir(urlf("licenciamento.minha_licenca"))
        from flask import render_template as rt
        return rt("erro.html", codigo=402,
                   mensagem="A licença da sua empresa está vencida. Fale com o administrador da sua conta."), 402

    # ---------------------- Bloqueio por módulo não contratado ----------------------
    # Complementa o bloqueio de licença acima: aquele trava a empresa
    # INTEIRA quando a licença não está em dia; este trava só as telas de
    # um módulo específico que a empresa não contratou, mesmo com a
    # licença ativa (ver app/models/modulo.py e app/utils/modulos.py).
    # Roda DEPOIS do bloqueio de licença de propósito (mesma ordem de
    # registro = mesma ordem de execução no Flask) — não faz sentido
    # avisar "módulo não contratado" pra quem nem tem licença ativa.
    #
    # Todo blueprint que NÃO está cadastrado no catálogo de módulos (login,
    # painel, admin, api, plataforma, licenciamento, integrações) nunca é
    # bloqueado aqui — só telas de blueprints com uma linha correspondente
    # em Modulo (chave == nome do blueprint) entram nessa checagem.
    @app.before_request
    def bloquear_modulo_nao_contratado():
        from flask import request as req, redirect as redir, url_for as urlf, flash as fl
        from flask_login import current_user as cu
        from app.utils.modulos import modulo_da_tela_atual, modulo_liberado_para

        if req.endpoint in ENDPOINTS_SEMPRE_LIBERADOS or req.endpoint is None:
            return None
        if not cu.is_authenticated or cu.is_admin_desenvolvedor:
            return None

        empresa = cu.empresa
        if empresa is None or empresa.dono_da_plataforma:
            return None

        modulo = modulo_da_tela_atual(req.blueprint)
        if modulo_liberado_para(empresa, modulo):
            return None

        if cu.is_admin:
            fl(f"O módulo \"{modulo.nome}\" não está contratado pela sua empresa. "
               "Solicite a liberação na área de módulos.", "warning")
            return redir(urlf("licenciamento.modulos"))
        from flask import render_template as rt
        return rt("erro.html", codigo=403,
                   mensagem=f"O módulo \"{modulo.nome}\" não está contratado pela sua empresa. "
                            "Fale com o administrador da sua conta."), 403

    from app.utils.notificacoes import contar_notificacoes_nao_lidas
    from app.utils.paginacao import url_pagina

    @app.context_processor
    def injetar_globais():
        from flask import request as req
        from flask_login import current_user
        qtd_notif = contar_notificacoes_nao_lidas(current_user) if current_user.is_authenticated else 0
        qtd_triagem_oab = 0
        qtd_pendente_dou = 0
        if current_user.is_authenticated:
            from app.models import IntimacaoCapturada, PublicacaoDouCapturada
            from app.utils.acesso import aplicar_escopo_unidade
            qtd_triagem_oab = aplicar_escopo_unidade(
                IntimacaoCapturada.query, IntimacaoCapturada
            ).filter_by(status="pendente_triagem").count()
            qtd_pendente_dou = aplicar_escopo_unidade(
                PublicacaoDouCapturada.query, PublicacaoDouCapturada
            ).filter_by(status="pendente_revisao").count()
        # url_pagina: usado pelo partial templates/_paginacao.html (ver
        # app/utils/paginacao.py, PENDENCIAS.md seção -47) pra montar o
        # link de cada página mantendo os filtros da URL atual.
        from app.utils.totp import totp_disponivel

        # Tutorial guiado de primeiro acesso (ver app/static/js/tour_guiado.js
        # e Usuario.tour_concluido_em). Só considera iniciar sozinho na tela
        # do Painel — é a página de pouso depois do login, então é o único
        # lugar onde o menu lateral inteiro já está visível pra apontar; em
        # qualquer outra tela o gate ficaria disputando atenção com o
        # conteúdo daquela página. `?tutorial=1` (usado pelo link "Rever
        # tutorial" em Minha conta) força a exibição de novo mesmo pra quem
        # já concluiu — nunca precisa "resetar" nada no banco pra rever.
        tour_deve_iniciar = (
            current_user.is_authenticated
            and req.endpoint == "dashboard.index"
            and (current_user.tour_concluido_em is None or req.args.get("tutorial") == "1")
        )
        return dict(qtd_notificacoes=qtd_notif, qtd_triagem_oab=qtd_triagem_oab, qtd_pendente_dou=qtd_pendente_dou,
                    url_pagina=url_pagina,
                    totp_disponivel=totp_disponivel(), tour_deve_iniciar=tour_deve_iniciar)

    @app.template_filter("moeda")
    def formatar_moeda(valor):
        if valor is None:
            return "R$ 0,00"
        return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    @app.template_filter("data_br")
    def formatar_data(valor):
        if not valor:
            return "-"
        return valor.strftime("%d/%m/%Y")

    @app.template_filter("data_hora_br")
    def formatar_data_hora(valor):
        if not valor:
            return "-"
        return valor.strftime("%d/%m/%Y %H:%M")

    @app.template_filter("eh_cnj_esaj_candidato")
    def eh_cnj_esaj_candidato(numero_processo):
        """Usado só pra mostrar/esconder o botão "Buscar dados públicos do
        e-SAJ" na tela do processo (PENDENCIAS.md, seções -71 e -72) — o
        conector em si valida de novo (nunca confia só no template).

        Broadeado na seção -72: antes só aparecia pra TJSP (tribunal "26"
        confirmado); agora aparece pra QUALQUER número de Justiça Estadual
        (segmento "8"), porque o conector agora tenta também TJAC/TJAL/
        TJAM/TJCE/TJMS (ver app/utils/conector_esaj_publico.py) — inclusive
        pra tribunais estaduais que este conector ainda NÃO cobre, caso em
        que o clique só resulta numa mensagem "não encontrado em nenhum dos
        tribunais e-SAJ testados", nunca um erro confuso ou crash."""
        from app.utils.cnj import somente_digitos
        d = somente_digitos(numero_processo)
        return len(d) == 20 and d[13] == "8"

    @app.template_filter("eh_cnj_pje_jt_candidato")
    def eh_cnj_pje_jt_candidato(numero_processo):
        """Mesma ideia de eh_cnj_esaj_candidato acima, só que pro botão
        "Buscar dados públicos do PJe-JT" (PENDENCIAS.md, seção -90) —
        aparece pra qualquer número da Justiça do Trabalho (segmento "5"),
        mesmo pro TRT-3/TRT-23 (não cobertos pelo conector — o clique
        resulta numa mensagem clara de "não encontrado", nunca um crash)."""
        from app.utils.cnj import somente_digitos
        d = somente_digitos(numero_processo)
        return len(d) == 20 and d[13] == "5"

    @app.errorhandler(403)
    def erro_403(e):
        from flask import render_template
        return render_template("erro.html", codigo=403,
                                mensagem="Você não tem permissão para acessar este recurso."), 403

    @app.errorhandler(404)
    def erro_404(e):
        from flask import render_template
        return render_template("erro.html", codigo=404, mensagem="Página não encontrada."), 404

    # Sentry (ver app/utils/monitoramento.py) já captura o erro de verdade
    # ANTES deste handler rodar — ele só troca a página de erro padrão do
    # Flask/Werkzeug (feia, em inglês, sem nada da identidade visual) por
    # uma consistente com o resto do sistema, sem vazar traceback nem
    # detalhe interno pro usuário final.
    @app.errorhandler(500)
    def erro_500(e):
        from flask import render_template
        return render_template("erro.html", codigo=500,
                                mensagem="Ocorreu um erro inesperado. Tente novamente em "
                                         "alguns instantes — se persistir, avise o suporte."), 500

    return app
