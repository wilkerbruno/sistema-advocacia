from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Tarefa, Processo, Usuario, Unidade
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403
from app.utils.notificacoes import registrar_log, notificar

tarefas_bp = Blueprint("tarefas", __name__)


def _parse_data(valor):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


@tarefas_bp.route("/")
@login_required
def listar():
    query = aplicar_escopo_unidade(Tarefa.query, Tarefa)
    status = request.args.get("status")
    somente_minhas = request.args.get("minhas")

    if status:
        query = query.filter(Tarefa.status == status)
    if somente_minhas:
        query = query.filter(Tarefa.responsavel_id == current_user.id)

    tarefas = query.order_by(Tarefa.data_vencimento).all()
    return render_template("tarefas/listar.html", tarefas=tarefas, status=status,
                            somente_minhas=somente_minhas, hoje=date.today())


@tarefas_bp.route("/nova", methods=["GET", "POST"])
@login_required
def nova():
    unidades = aplicar_escopo_unidade(Unidade.query, Unidade, "id").filter_by(ativa=True).all() if current_user.is_admin else None
    minha_unidade_id = None if current_user.is_admin else current_user.unidade_id
    processos = aplicar_escopo_unidade(Processo.query, Processo).order_by(Processo.numero_processo).all()
    if minha_unidade_id:
        equipe = Usuario.query.filter_by(unidade_id=minha_unidade_id, ativo=True).all()
    else:
        equipe = aplicar_escopo_unidade(Usuario.query.join(Unidade, Usuario.unidade_id == Unidade.id), Unidade, "id").filter(Usuario.ativo == True).all()

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)
        responsavel_id = request.form.get("responsavel_id") or current_user.id

        tarefa = Tarefa(
            titulo=request.form["titulo"],
            descricao=request.form.get("descricao"),
            prioridade=request.form.get("prioridade", "normal"),
            data_vencimento=_parse_data(request.form.get("data_vencimento")),
            processo_id=request.form.get("processo_id") or None,
            responsavel_id=responsavel_id,
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
        )
        db.session.add(tarefa)
        db.session.flush()
        if int(responsavel_id) != current_user.id:
            notificar(responsavel_id, "Nova tarefa atribuída a você", tarefa.titulo,
                       tipo="tarefa", link=url_for("tarefas.listar"))
        registrar_log(current_user, "criou", "Tarefa", tarefa.id, tarefa.titulo)
        db.session.commit()
        flash("Tarefa criada com sucesso.", "success")
        return redirect(url_for("tarefas.listar"))

    return render_template("tarefas/form.html", unidades=unidades, processos=processos, equipe=equipe)


@tarefas_bp.route("/<int:tarefa_id>/status", methods=["POST"])
@login_required
def atualizar_status(tarefa_id):
    tarefa = db.get_or_404(Tarefa, tarefa_id)
    checar_acesso_unidade_ou_403(tarefa.unidade_id)
    novo_status = request.form.get("status")
    if novo_status in Tarefa.STATUS:
        tarefa.status = novo_status
        if novo_status == "concluida":
            tarefa.concluida_em = datetime.utcnow()
        registrar_log(current_user, "status_tarefa", "Tarefa", tarefa.id, novo_status)
        db.session.commit()
        flash("Status da tarefa atualizado.", "info")
    return redirect(url_for("tarefas.listar"))
