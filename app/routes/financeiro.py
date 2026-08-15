from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Lancamento, Processo, Cliente, Unidade
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo
from app.utils.notificacoes import registrar_log

financeiro_bp = Blueprint("financeiro", __name__)


def _parse_data(valor):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


@financeiro_bp.route("/")
@login_required
def listar():
    query = aplicar_escopo_unidade(Lancamento.query, Lancamento)

    status = request.args.get("status")
    natureza = request.args.get("natureza")
    unidade_filtro = request.args.get("unidade_id")

    if status:
        query = query.filter(Lancamento.status == status)
    if natureza:
        query = query.filter(Lancamento.natureza == natureza)
    if current_user.is_admin and unidade_filtro:
        query = query.filter(Lancamento.unidade_id == int(unidade_filtro))

    lancamentos = query.order_by(Lancamento.data_vencimento.desc()).all()

    base_totais = aplicar_escopo_unidade(Lancamento.query, Lancamento)
    if current_user.is_admin and unidade_filtro:
        base_totais = base_totais.filter(Lancamento.unidade_id == int(unidade_filtro))

    total_a_receber = base_totais.filter(
        Lancamento.natureza == "receita", Lancamento.status == "pendente"
    ).with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()
    total_recebido_mes = base_totais.filter(
        Lancamento.natureza == "receita", Lancamento.status == "pago",
        func.extract("month", Lancamento.data_pagamento) == date.today().month,
        func.extract("year", Lancamento.data_pagamento) == date.today().year,
    ).with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()
    total_atrasado = base_totais.filter(
        Lancamento.natureza == "receita", Lancamento.status == "pendente",
        Lancamento.data_vencimento < date.today(),
    ).with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()

    unidades = unidades_do_escopo() if current_user.is_admin else None

    return render_template("financeiro/listar.html", lancamentos=lancamentos, unidades=unidades,
                            total_a_receber=total_a_receber, total_recebido_mes=total_recebido_mes,
                            total_atrasado=total_atrasado, status=status, natureza=natureza)


@financeiro_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    unidades = unidades_do_escopo() if current_user.is_admin else None
    processos = aplicar_escopo_unidade(Processo.query, Processo).order_by(Processo.numero_processo).all()
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).order_by(Cliente.nome).all()

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        lancamento = Lancamento(
            descricao=request.form["descricao"],
            tipo=request.form.get("tipo", "honorario"),
            natureza=request.form.get("natureza", "receita"),
            valor=request.form["valor"],
            status=request.form.get("status", "pendente"),
            data_vencimento=_parse_data(request.form.get("data_vencimento")),
            data_pagamento=_parse_data(request.form.get("data_pagamento")),
            forma_pagamento=request.form.get("forma_pagamento"),
            parcela=request.form.get("parcela"),
            observacoes=request.form.get("observacoes"),
            unidade_id=unidade_id,
            processo_id=request.form.get("processo_id") or None,
            cliente_id=request.form.get("cliente_id") or None,
            criado_por_id=current_user.id,
        )
        db.session.add(lancamento)
        db.session.flush()
        registrar_log(current_user, "criou", "Lancamento", lancamento.id, lancamento.descricao)
        db.session.commit()
        flash("Lançamento financeiro criado.", "success")
        return redirect(url_for("financeiro.listar"))

    return render_template("financeiro/form.html", unidades=unidades, processos=processos, clientes=clientes)


@financeiro_bp.route("/<int:lancamento_id>/status", methods=["POST"])
@login_required
def atualizar_status(lancamento_id):
    lancamento = db.get_or_404(Lancamento, lancamento_id)
    checar_acesso_unidade_ou_403(lancamento.unidade_id)
    novo_status = request.form.get("status")
    if novo_status in Lancamento.STATUS:
        lancamento.status = novo_status
        if novo_status == "pago" and not lancamento.data_pagamento:
            lancamento.data_pagamento = date.today()
        registrar_log(current_user, "status_lancamento", "Lancamento", lancamento.id, novo_status)
        db.session.commit()
        flash("Status do lançamento atualizado.", "info")
    return redirect(url_for("financeiro.listar"))
