"""
Administração da plataforma — visível e acessível SOMENTE para admins
desenvolvedores (usuários da empresa dona da plataforma). Nenhuma empresa
cliente tem acesso a nada aqui, nem sabe que essas telas existem.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Empresa, Unidade, Usuario, Licenca
from app.utils.acesso import apenas_admin_desenvolvedor
from app.utils.notificacoes import registrar_log

plataforma_bp = Blueprint("plataforma", __name__)


@plataforma_bp.route("/empresas")
@login_required
@apenas_admin_desenvolvedor
def empresas():
    lista = Empresa.query.filter_by(dono_da_plataforma=False).order_by(Empresa.nome).all()
    return render_template("plataforma/empresas.html", empresas=lista)


@plataforma_bp.route("/empresas/nova", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def nova_empresa():
    if request.method == "POST":
        empresa = Empresa(
            nome=request.form["nome"],
            cnpj=request.form.get("cnpj"),
            email_contato=request.form.get("email_contato"),
            telefone=request.form.get("telefone"),
        )
        db.session.add(empresa)
        db.session.flush()

        # já cria a primeira unidade e o primeiro admin da empresa, pra
        # não deixar a empresa sem ninguém que consiga entrar
        unidade = Unidade(
            empresa_id=empresa.id,
            nome=request.form.get("unidade_nome") or "Matriz",
            codigo=request.form["unidade_codigo"].upper(),
        )
        db.session.add(unidade)
        db.session.flush()

        admin_empresa = Usuario(
            nome=request.form["admin_nome"],
            email=request.form["admin_email"].strip().lower(),
            papel="admin",
            unidade_id=unidade.id,
        )
        admin_empresa.set_senha(request.form["admin_senha"])
        db.session.add(admin_empresa)
        db.session.flush()

        # licença inicial, pendente de pagamento
        licenca = Licenca(
            empresa_id=empresa.id,
            plano=request.form.get("plano", "mensal"),
            valor_negociado=Decimal(request.form["valor_negociado"].replace(",", ".")),
            status="pendente_pagamento",
            definido_por_id=current_user.id,
        )
        db.session.add(licenca)

        registrar_log(current_user, "criou", "Empresa", empresa.id, empresa.nome)
        db.session.commit()
        flash(f"Empresa \"{empresa.nome}\" cadastrada, com unidade e admin criados. Licença pendente de pagamento.", "success")
        return redirect(url_for("plataforma.empresas"))

    return render_template("plataforma/empresa_form.html")


@plataforma_bp.route("/empresas/<int:empresa_id>")
@login_required
@apenas_admin_desenvolvedor
def detalhe_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)
    unidades = Unidade.query.filter_by(empresa_id=empresa.id).all()
    usuarios = Usuario.query.join(Unidade).filter(Unidade.empresa_id == empresa.id).order_by(Usuario.nome).all()
    return render_template("plataforma/empresa_detalhe.html", empresa=empresa, unidades=unidades, usuarios=usuarios)


@plataforma_bp.route("/empresas/<int:empresa_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def editar_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)
    if request.method == "POST":
        empresa.nome = request.form["nome"]
        empresa.cnpj = request.form.get("cnpj")
        empresa.email_contato = request.form.get("email_contato")
        empresa.telefone = request.form.get("telefone")
        empresa.ativa = bool(request.form.get("ativa"))
        registrar_log(current_user, "editou", "Empresa", empresa.id, empresa.nome)
        db.session.commit()
        flash("Empresa atualizada.", "success")
        return redirect(url_for("plataforma.detalhe_empresa", empresa_id=empresa.id))
    return render_template("plataforma/empresa_form.html", empresa=empresa)


# ---------------------- Licenças ----------------------

@plataforma_bp.route("/empresas/<int:empresa_id>/licenca", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def licenca_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)
    licenca = empresa.licenca

    if request.method == "POST":
        valor = Decimal(request.form["valor_negociado"].replace(",", "."))
        plano = request.form.get("plano", "mensal")

        if licenca is None:
            licenca = Licenca(empresa_id=empresa.id)
            db.session.add(licenca)

        licenca.plano = plano
        licenca.valor_negociado = valor
        licenca.definido_por_id = current_user.id

        # ação manual do admin dev: pode também marcar como paga direto
        # (ex: pagamento combinado fora do sistema, boleto avulso etc)
        if request.form.get("marcar_paga_manualmente"):
            licenca.status = "ativa"
            licenca.data_inicio = date.today()
            licenca.data_fim = date.today() + timedelta(days=Licenca.DIAS_POR_PLANO[plano])

        registrar_log(current_user, "definiu_licenca", "Empresa", empresa.id,
                      f"{plano} R${valor}")
        db.session.commit()
        flash("Licença atualizada.", "success")
        return redirect(url_for("plataforma.detalhe_empresa", empresa_id=empresa.id))

    return render_template("plataforma/licenca_form.html", empresa=empresa, licenca=licenca)
