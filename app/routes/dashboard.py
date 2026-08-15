from datetime import date, timedelta
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Processo, Prazo, Audiencia, Tarefa, Cliente, Unidade, Lancamento
from app.utils.acesso import aplicar_escopo_unidade, unidades_do_escopo

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    hoje = date.today()
    limite_alerta = hoje + timedelta(days=5)

    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    clientes_q = aplicar_escopo_unidade(Cliente.query, Cliente)
    prazos_q = aplicar_escopo_unidade(Prazo.query, Prazo, "responsavel_id") \
        if False else Prazo.query.join(Processo)
    if not current_user.is_admin:
        prazos_q = prazos_q.filter(Processo.unidade_id == current_user.unidade_id)

    tarefas_q = aplicar_escopo_unidade(Tarefa.query, Tarefa)
    audiencias_q = Audiencia.query.join(Processo)
    if not current_user.is_admin:
        audiencias_q = audiencias_q.filter(Processo.unidade_id == current_user.unidade_id)

    # KPIs principais
    total_processos_ativos = processos_q.filter(Processo.status == "ativo").count()
    total_clientes = clientes_q.filter(Cliente.ativo == True).count()  # noqa: E712
    prazos_vencendo = prazos_q.filter(
        Prazo.status == "pendente",
        Prazo.data_vencimento <= limite_alerta,
    ).order_by(Prazo.data_vencimento).limit(8).all()
    prazos_perdidos_count = prazos_q.filter(
        Prazo.status == "pendente", Prazo.data_vencimento < hoje
    ).count()
    proximas_audiencias = audiencias_q.filter(
        Audiencia.data_hora >= hoje, Audiencia.status == "agendada"
    ).order_by(Audiencia.data_hora).limit(6).all()
    tarefas_pendentes = tarefas_q.filter(
        Tarefa.status.in_(["pendente", "em_andamento"])
    ).order_by(Tarefa.data_vencimento).limit(8).all()

    processos_por_status = dict(
        processos_q.with_entities(Processo.status, func.count(Processo.id))
        .group_by(Processo.status).all()
    )
    processos_por_area = dict(
        processos_q.with_entities(Processo.area_direito, func.count(Processo.id))
        .group_by(Processo.area_direito).all()
    )

    contexto = dict(
        total_processos_ativos=total_processos_ativos,
        total_clientes=total_clientes,
        prazos_vencendo=prazos_vencendo,
        prazos_perdidos_count=prazos_perdidos_count,
        proximas_audiencias=proximas_audiencias,
        tarefas_pendentes=tarefas_pendentes,
        processos_por_status=processos_por_status,
        processos_por_area=processos_por_area,
        hoje=hoje,
    )

    if current_user.is_admin:
        # Visão consolidada extra: comparativo entre todas as unidades (da própria empresa, ou de todas se admin desenvolvedor)
        unidades = unidades_do_escopo()
        resumo_unidades = []
        for u in unidades:
            qtd_processos = Processo.query.filter_by(unidade_id=u.id, status="ativo").count()
            qtd_clientes = Cliente.query.filter_by(unidade_id=u.id, ativo=True).count()
            receita_pendente = db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).filter(
                Lancamento.unidade_id == u.id,
                Lancamento.natureza == "receita",
                Lancamento.status == "pendente",
            ).scalar()
            qtd_prazos_criticos = Prazo.query.join(Processo).filter(
                Processo.unidade_id == u.id,
                Prazo.status == "pendente",
                Prazo.data_vencimento <= limite_alerta,
            ).count()
            resumo_unidades.append(dict(
                unidade=u, qtd_processos=qtd_processos, qtd_clientes=qtd_clientes,
                receita_pendente=receita_pendente, qtd_prazos_criticos=qtd_prazos_criticos,
            ))
        contexto["resumo_unidades"] = resumo_unidades
        contexto["total_unidades"] = len(unidades)

    return render_template("dashboard/index.html", **contexto)
