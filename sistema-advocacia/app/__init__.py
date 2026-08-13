import os
from flask import Flask
from config import Config
from app.extensions import db, login_manager


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import Usuario

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(Usuario, int(user_id))

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.clientes import clientes_bp
    from app.routes.processos import processos_bp
    from app.routes.financeiro import financeiro_bp
    from app.routes.tarefas import tarefas_bp
    from app.routes.admin import admin_bp
    from app.routes.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(clientes_bp, url_prefix="/clientes")
    app.register_blueprint(processos_bp, url_prefix="/processos")
    app.register_blueprint(financeiro_bp, url_prefix="/financeiro")
    app.register_blueprint(tarefas_bp, url_prefix="/tarefas")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

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
