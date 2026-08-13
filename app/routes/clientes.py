from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Cliente, Unidade
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403
from app.utils.notificacoes import registrar_log

clientes_bp = Blueprint("clientes", __name__)


@clientes_bp.route("/")
@login_required
def listar():
    termo = request.args.get("q", "").strip()
    query = aplicar_escopo_unidade(Cliente.query, Cliente)
    if termo:
        like = f"%{termo}%"
        query = query.filter(db.or_(Cliente.nome.ilike(like), Cliente.cpf_cnpj.ilike(like)))
    clientes = query.order_by(Cliente.nome).all()
    return render_template("clientes/listar.html", clientes=clientes, termo=termo)


@clientes_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    unidades = Unidade.query.filter_by(ativa=True).all() if current_user.is_admin else None

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        cliente = Cliente(
            tipo_pessoa=request.form.get("tipo_pessoa", "PF"),
            nome=request.form["nome"],
            cpf_cnpj=request.form.get("cpf_cnpj"),
            rg_ie=request.form.get("rg_ie"),
            email=request.form.get("email"),
            telefone=request.form.get("telefone"),
            whatsapp=request.form.get("whatsapp"),
            endereco=request.form.get("endereco"),
            cidade=request.form.get("cidade"),
            estado=request.form.get("estado"),
            cep=request.form.get("cep"),
            observacoes=request.form.get("observacoes"),
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
        )
        db.session.add(cliente)
        db.session.flush()
        registrar_log(current_user, "criou", "Cliente", cliente.id, cliente.nome)
        db.session.commit()
        flash("Cliente cadastrado com sucesso.", "success")
        return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))

    return render_template("clientes/form.html", cliente=None, unidades=unidades)


@clientes_bp.route("/<int:cliente_id>")
@login_required
def detalhe(cliente_id):
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)
    return render_template("clientes/detalhe.html", cliente=cliente)


@clientes_bp.route("/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
def editar(cliente_id):
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)
    unidades = Unidade.query.filter_by(ativa=True).all() if current_user.is_admin else None

    if request.method == "POST":
        cliente.tipo_pessoa = request.form.get("tipo_pessoa", "PF")
        cliente.nome = request.form["nome"]
        cliente.cpf_cnpj = request.form.get("cpf_cnpj")
        cliente.rg_ie = request.form.get("rg_ie")
        cliente.email = request.form.get("email")
        cliente.telefone = request.form.get("telefone")
        cliente.whatsapp = request.form.get("whatsapp")
        cliente.endereco = request.form.get("endereco")
        cliente.cidade = request.form.get("cidade")
        cliente.estado = request.form.get("estado")
        cliente.cep = request.form.get("cep")
        cliente.observacoes = request.form.get("observacoes")
        if current_user.is_admin and request.form.get("unidade_id"):
            cliente.unidade_id = int(request.form["unidade_id"])

        registrar_log(current_user, "editou", "Cliente", cliente.id, cliente.nome)
        db.session.commit()
        flash("Cliente atualizado com sucesso.", "success")
        return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))

    return render_template("clientes/form.html", cliente=cliente, unidades=unidades)


@clientes_bp.route("/<int:cliente_id>/inativar", methods=["POST"])
@login_required
def inativar(cliente_id):
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)
    cliente.ativo = not cliente.ativo
    registrar_log(current_user, "alterou_status", "Cliente", cliente.id,
                  f"ativo={cliente.ativo}")
    db.session.commit()
    flash("Status do cliente atualizado.", "info")
    return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))
