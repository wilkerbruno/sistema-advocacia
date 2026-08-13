from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Unidade, Usuario, Processo, Cliente, Lancamento, LogAtividade
from app.utils.acesso import apenas_admin, login_papel_requerido
from app.utils.notificacoes import registrar_log

admin_bp = Blueprint("admin", __name__)


# ---------------------- Unidades (somente admin) ----------------------

@admin_bp.route("/unidades")
@login_required
@apenas_admin
def unidades():
    lista = Unidade.query.order_by(Unidade.nome).all()
    return render_template("admin/unidades.html", unidades=lista)


@admin_bp.route("/unidades/nova", methods=["GET", "POST"])
@login_required
@apenas_admin
def nova_unidade():
    if request.method == "POST":
        unidade = Unidade(
            nome=request.form["nome"],
            codigo=request.form["codigo"].upper(),
            cidade=request.form.get("cidade"),
            estado=request.form.get("estado"),
            endereco=request.form.get("endereco"),
            telefone=request.form.get("telefone"),
            email=request.form.get("email"),
            responsavel=request.form.get("responsavel"),
        )
        db.session.add(unidade)
        db.session.flush()
        registrar_log(current_user, "criou", "Unidade", unidade.id, unidade.nome)
        db.session.commit()
        flash("Unidade cadastrada com sucesso.", "success")
        return redirect(url_for("admin.unidades"))
    return render_template("admin/unidade_form.html", unidade=None)


@admin_bp.route("/unidades/<int:unidade_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_unidade(unidade_id):
    unidade = db.get_or_404(Unidade, unidade_id)
    if request.method == "POST":
        unidade.nome = request.form["nome"]
        unidade.codigo = request.form["codigo"].upper()
        unidade.cidade = request.form.get("cidade")
        unidade.estado = request.form.get("estado")
        unidade.endereco = request.form.get("endereco")
        unidade.telefone = request.form.get("telefone")
        unidade.email = request.form.get("email")
        unidade.responsavel = request.form.get("responsavel")
        unidade.ativa = bool(request.form.get("ativa"))
        registrar_log(current_user, "editou", "Unidade", unidade.id, unidade.nome)
        db.session.commit()
        flash("Unidade atualizada com sucesso.", "success")
        return redirect(url_for("admin.unidades"))
    return render_template("admin/unidade_form.html", unidade=unidade)


# ---------------------- Usuários ----------------------

@admin_bp.route("/usuarios")
@login_required
@login_papel_requerido("admin", "gestor")
def usuarios():
    query = Usuario.query
    if not current_user.is_admin:
        query = query.filter_by(unidade_id=current_user.unidade_id)
    lista = query.order_by(Usuario.nome).all()
    unidades = Unidade.query.filter_by(ativa=True).all()
    return render_template("admin/usuarios.html", usuarios=lista, unidades=unidades)


@admin_bp.route("/usuarios/novo", methods=["GET", "POST"])
@login_required
@login_papel_requerido("admin", "gestor")
def novo_usuario():
    unidades = Unidade.query.filter_by(ativa=True).all()

    if request.method == "POST":
        papel = request.form.get("papel", "funcionario")
        # gestor só pode criar usuários da própria unidade e nunca cria admin
        if not current_user.is_admin:
            papel = "funcionario" if papel not in ("advogado", "funcionario") else papel
            unidade_id = current_user.unidade_id
        else:
            unidade_id = request.form.get("unidade_id") or None
            if papel != "admin" and not unidade_id:
                flash("Selecione a unidade do usuário.", "danger")
                return render_template("admin/usuario_form.html", usuario=None, unidades=unidades)

        if Usuario.query.filter_by(email=request.form["email"].strip().lower()).first():
            flash("Já existe um usuário com este e-mail.", "danger")
            return render_template("admin/usuario_form.html", usuario=None, unidades=unidades)

        usuario = Usuario(
            nome=request.form["nome"],
            email=request.form["email"].strip().lower(),
            papel=papel,
            oab=request.form.get("oab"),
            telefone=request.form.get("telefone"),
            unidade_id=unidade_id if papel != "admin" else None,
        )
        usuario.set_senha(request.form["senha"])
        db.session.add(usuario)
        db.session.flush()
        registrar_log(current_user, "criou", "Usuario", usuario.id, usuario.email)
        db.session.commit()
        flash("Usuário cadastrado com sucesso.", "success")
        return redirect(url_for("admin.usuarios"))

    return render_template("admin/usuario_form.html", usuario=None, unidades=unidades)


@admin_bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@login_papel_requerido("admin", "gestor")
def editar_usuario(usuario_id):
    usuario = db.get_or_404(Usuario, usuario_id)
    if not current_user.is_admin and usuario.unidade_id != current_user.unidade_id:
        flash("Você não pode editar usuários de outra unidade.", "danger")
        return redirect(url_for("admin.usuarios"))

    unidades = Unidade.query.filter_by(ativa=True).all()

    if request.method == "POST":
        usuario.nome = request.form["nome"]
        usuario.oab = request.form.get("oab")
        usuario.telefone = request.form.get("telefone")
        usuario.ativo = bool(request.form.get("ativo"))

        if current_user.is_admin:
            usuario.papel = request.form.get("papel", usuario.papel)
            unidade_id = request.form.get("unidade_id") or None
            usuario.unidade_id = unidade_id if usuario.papel != "admin" else None

        nova_senha = request.form.get("senha")
        if nova_senha:
            usuario.set_senha(nova_senha)

        registrar_log(current_user, "editou", "Usuario", usuario.id, usuario.email)
        db.session.commit()
        flash("Usuário atualizado com sucesso.", "success")
        return redirect(url_for("admin.usuarios"))

    return render_template("admin/usuario_form.html", usuario=usuario, unidades=unidades)


# ---------------------- Relatórios consolidados (somente admin) ----------------------

@admin_bp.route("/relatorios")
@login_required
@apenas_admin
def relatorios():
    por_unidade = []
    for u in Unidade.query.order_by(Unidade.nome).all():
        por_unidade.append(dict(
            unidade=u,
            processos_ativos=Processo.query.filter_by(unidade_id=u.id, status="ativo").count(),
            processos_encerrados=Processo.query.filter_by(unidade_id=u.id, status="encerrado").count(),
            clientes=Cliente.query.filter_by(unidade_id=u.id).count(),
            receita_pendente=db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).filter(
                Lancamento.unidade_id == u.id, Lancamento.natureza == "receita",
                Lancamento.status == "pendente").scalar(),
            receita_recebida=db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).filter(
                Lancamento.unidade_id == u.id, Lancamento.natureza == "receita",
                Lancamento.status == "pago").scalar(),
        ))

    por_area = dict(
        db.session.query(Processo.area_direito, func.count(Processo.id)).group_by(Processo.area_direito).all()
    )

    return render_template("admin/relatorios.html", por_unidade=por_unidade, por_area=por_area)


@admin_bp.route("/auditoria")
@login_required
@apenas_admin
def auditoria():
    pagina = request.args.get("pagina", 1, type=int)
    logs = LogAtividade.query.order_by(LogAtividade.criado_em.desc()).paginate(page=pagina, per_page=50)
    return render_template("admin/auditoria.html", logs=logs)
