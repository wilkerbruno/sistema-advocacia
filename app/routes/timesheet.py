"""
Timesheet — controle de horas trabalhadas (item 7 do briefing de paridade:
"timesheet, controle de horas trabalhadas"). Apontamento simples por
usuário, com ou sem vínculo a processo. Alimenta o indicador de
produtividade em governanca.produtividade.

Regra de visibilidade: cada usuário só vê e apaga o próprio apontamento
(mesma lógica do cofre de senha de processo) — admin (de empresa ou
desenvolvedor) enxerga todos os apontamentos dentro do seu escopo.
"""
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Apontamento, Processo
from app.utils.acesso import aplicar_escopo_unidade, checar_acesso_unidade_ou_403
from app.utils.notificacoes import registrar_log

timesheet_bp = Blueprint("timesheet", __name__)


def _parse_data(valor):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


@timesheet_bp.route("/")
@login_required
def listar():
    query = aplicar_escopo_unidade(Apontamento.query, Apontamento)
    somente_minhas = request.args.get("minhas")
    if not current_user.is_admin or somente_minhas:
        query = query.filter(Apontamento.usuario_id == current_user.id)

    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    if data_inicio:
        query = query.filter(Apontamento.data >= _parse_data(data_inicio))
    if data_fim:
        query = query.filter(Apontamento.data <= _parse_data(data_fim))

    apontamentos = query.order_by(Apontamento.data.desc(), Apontamento.criado_em.desc()).all()
    total_horas = sum((a.horas for a in apontamentos), Decimal("0"))
    total_faturavel = sum((a.horas for a in apontamentos if a.faturavel), Decimal("0"))

    return render_template(
        "timesheet/listar.html", apontamentos=apontamentos,
        total_horas=total_horas, total_faturavel=total_faturavel,
        somente_minhas=somente_minhas, data_inicio=data_inicio, data_fim=data_fim,
    )


@timesheet_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    processos = aplicar_escopo_unidade(Processo.query, Processo).order_by(Processo.numero_processo).all()

    if request.method == "POST":
        if not current_user.unidade_id:
            flash("Seu usuário não está vinculado a uma unidade — não é possível apontar horas.", "danger")
            return redirect(url_for("timesheet.listar"))

        try:
            horas = Decimal(request.form["horas"].replace(",", "."))
        except (InvalidOperation, KeyError):
            flash("Informe as horas em formato numérico (ex: 1.5).", "danger")
            return render_template("timesheet/form.html", processos=processos, hoje=date.today())

        if horas <= 0:
            flash("As horas precisam ser maiores que zero.", "danger")
            return render_template("timesheet/form.html", processos=processos, hoje=date.today())

        processo_id = request.form.get("processo_id") or None
        if processo_id:
            processo = db.get_or_404(Processo, processo_id)
            checar_acesso_unidade_ou_403(processo.unidade_id)

        apontamento = Apontamento(
            usuario_id=current_user.id,
            unidade_id=current_user.unidade_id,
            processo_id=processo_id,
            data=_parse_data(request.form.get("data")) or date.today(),
            horas=horas,
            descricao=request.form["descricao"],
            faturavel=bool(request.form.get("faturavel")),
        )
        db.session.add(apontamento)
        db.session.flush()
        registrar_log(current_user, "criou", "Apontamento", apontamento.id, f"{horas}h")
        db.session.commit()
        flash("Horas registradas.", "success")
        return redirect(url_for("timesheet.listar"))

    return render_template("timesheet/form.html", processos=processos, hoje=date.today())


@timesheet_bp.route("/<int:apontamento_id>/excluir", methods=["POST"])
@login_required
def excluir(apontamento_id):
    apontamento = db.get_or_404(Apontamento, apontamento_id)
    if apontamento.usuario_id != current_user.id and not current_user.is_admin:
        flash("Você não pode excluir um apontamento de outra pessoa.", "danger")
        return redirect(url_for("timesheet.listar"))
    checar_acesso_unidade_ou_403(apontamento.unidade_id)

    db.session.delete(apontamento)
    registrar_log(current_user, "excluiu", "Apontamento", apontamento_id)
    db.session.commit()
    flash("Apontamento removido.", "info")
    return redirect(url_for("timesheet.listar"))
