from datetime import datetime, date, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import Usuario, Empresa, Unidade, Licenca
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


@auth_bp.route("/cadastrar-empresa", methods=["GET", "POST"])
def cadastrar_empresa():
    """
    Cadastro público (self-service): qualquer visitante pode criar sua
    própria empresa cliente, com a primeira unidade e o primeiro admin
    dessa empresa, e já sai com uma licença pendente de pagamento pronta
    para pagar via Mercado Pago.
    """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    precos = {
        "mensal": Decimal(current_app.config["PRECO_PADRAO_MENSAL"]),
        "trimestral": Decimal(current_app.config["PRECO_PADRAO_TRIMESTRAL"]),
        "anual": Decimal(current_app.config["PRECO_PADRAO_ANUAL"]),
    }

    if request.method == "POST":
        nome_empresa = request.form.get("empresa_nome", "").strip()
        nome_admin = request.form.get("admin_nome", "").strip()
        email_admin = request.form.get("admin_email", "").strip().lower()
        senha = request.form.get("admin_senha", "")
        plano = request.form.get("plano", "mensal")

        erros = []
        if not nome_empresa:
            erros.append("Informe o nome da empresa.")
        if not nome_admin or not email_admin:
            erros.append("Informe seu nome e e-mail.")
        if len(senha) < 6:
            erros.append("A senha precisa ter pelo menos 6 caracteres.")
        if plano not in Licenca.PLANOS:
            erros.append("Plano inválido.")
        if Usuario.query.filter_by(email=email_admin).first():
            erros.append("Já existe uma conta com este e-mail.")

        if erros:
            for e in erros:
                flash(e, "danger")
            return render_template("auth/cadastro_empresa.html", precos=precos, form=request.form)

        empresa = Empresa(nome=nome_empresa, cnpj=request.form.get("empresa_cnpj") or None,
                           email_contato=email_admin, dono_da_plataforma=False)
        db.session.add(empresa)
        db.session.flush()

        unidade = Unidade(empresa_id=empresa.id, nome="Matriz", codigo=f"EMP{empresa.id}-01")
        db.session.add(unidade)
        db.session.flush()

        admin = Usuario(nome=nome_admin, email=email_admin, papel="admin", unidade_id=unidade.id)
        admin.set_senha(senha)
        db.session.add(admin)
        db.session.flush()

        licenca = Licenca(
            empresa_id=empresa.id, plano=plano, valor_negociado=precos[plano],
            status="pendente_pagamento",
        )
        db.session.add(licenca)

        registrar_log(admin, "cadastro_self_service", "Empresa", empresa.id, empresa.nome)
        db.session.commit()

        login_user(admin)
        flash(f"Empresa \"{nome_empresa}\" cadastrada! Falta só ativar sua licença para começar a usar.", "success")
        return redirect(url_for("licenciamento.minha_licenca"))

    return render_template("auth/cadastro_empresa.html", precos=precos, form={})


@auth_bp.route("/logout")
@login_required
def logout():
    registrar_log(current_user, "logout", "Usuario", current_user.id)
    db.session.commit()
    logout_user()
    flash("Sessão encerrada com sucesso.", "info")
    return redirect(url_for("auth.login"))
