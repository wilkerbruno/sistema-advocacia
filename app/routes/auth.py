from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import Usuario
from app.utils.notificacoes import registrar_log

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        lembrar = bool(request.form.get("lembrar"))

        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and usuario.ativo and usuario.checar_senha(senha):
            login_user(usuario, remember=lembrar)
            usuario.ultimo_login = datetime.utcnow()
            registrar_log(usuario, "login", "Usuario", usuario.id)
            db.session.commit()
            proximo = request.args.get("next")
            return redirect(proximo or url_for("dashboard.index"))

        flash("E-mail ou senha inválidos, ou usuário inativo.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    registrar_log(current_user, "logout", "Usuario", current_user.id)
    db.session.commit()
    logout_user()
    flash("Sessão encerrada com sucesso.", "info")
    return redirect(url_for("auth.login"))
