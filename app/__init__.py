import os
import uuid
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from config import Config
from app.extensions import db, login_manager

NOME_COOKIE_DISPOSITIVO = "jc_device_id"
DURACAO_COOKIE_DISPOSITIVO = 60 * 60 * 24 * 730  # ~2 anos


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

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

    # ---------------------- Bloqueio por licença vencida ----------------------
    # Admin desenvolvedor e a empresa dona da plataforma nunca são bloqueados.
    # Demais empresas: se a licença não está ativa, só conseguem acessar
    # login/logout e a própria área de licenciamento (pra poder pagar).
    ENDPOINTS_SEMPRE_LIBERADOS = {
        "auth.login", "auth.logout", "static",
        "licenciamento.minha_licenca", "licenciamento.pagar_licenca",
        "licenciamento.pagamento_retorno", "licenciamento.webhook_mercadopago",
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

    from app.utils.notificacoes import contar_notificacoes_nao_lidas

    @app.context_processor
    def injetar_globais():
        from flask_login import current_user
        qtd_notif = contar_notificacoes_nao_lidas(current_user) if current_user.is_authenticated else 0
        return dict(qtd_notificacoes=qtd_notif)

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

    @app.errorhandler(403)
    def erro_403(e):
        from flask import render_template
        return render_template("erro.html", codigo=403,
                                mensagem="Você não tem permissão para acessar este recurso."), 403

    @app.errorhandler(404)
    def erro_404(e):
        from flask import render_template
        return render_template("erro.html", codigo=404, mensagem="Página não encontrada."), 404

    return app
