from datetime import date, timedelta
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Processo, Prazo, Audiencia, Tarefa, Cliente, Unidade, Lancamento
from app.utils.acesso import aplicar_escopo_unidade, unidades_do_escopo, filtrar_processos_visiveis

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    hoje = date.today()
    limite_alerta = hoje + timedelta(days=5)

    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    clientes_q = aplicar_escopo_unidade(Cliente.query, Cliente)
    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): isto usava
    # `if not current_user.is_admin: filter(unidade_id == ...)`, o que só
    # restringia usuário comum — QUALQUER admin (inclusive admin de uma
    # empresa cliente comum, não só o admin desenvolvedor) via "Prazos em
    # atenção"/"Prazos perdidos" de TODAS as empresas do sistema, não só
    # da própria. `aplicar_escopo_unidade` já implementa a regra certa das
    # 3 camadas (admin desenvolvedor vê tudo, admin de empresa vê só a
    # própria empresa, demais só a própria unidade) — passar `Processo`
    # como `modelo` funciona porque a query já está com JOIN nele.
    # `filtrar_processos_visiveis` e o filtro de `deletado_em` faltavam
    # aqui (PENDENCIAS.md, seção -100) — sem eles, um processo sigiloso
    # (segredo_justica) ou um prazo já soft-deletado ainda contavam nos
    # KPIs/páginas de "Prazos em atenção"/"Prazos perdidos", inconsistente
    # com `governanca.fila_intimacoes` e `governanca.painel`, que já
    # aplicam os dois.
    prazos_q = filtrar_processos_visiveis(
        aplicar_escopo_unidade(Prazo.query.join(Processo), Processo)
    ).filter(Prazo.deletado_em.is_(None))

    tarefas_q = aplicar_escopo_unidade(Tarefa.query, Tarefa)
    audiencias_q = filtrar_processos_visiveis(
        aplicar_escopo_unidade(Audiencia.query.join(Processo), Processo)
    )

    # KPIs principais
    processos_ativos_q = processos_q.filter(Processo.status == "ativo")
    total_processos_ativos = processos_ativos_q.count()
    # Soma do valor da causa dos processos ativos do escopo (pedido
    # explícito: "no card processos ativos deve aparecer também o valor
    # total dos processos em R$") — `coalesce` pra não virar `None` quando
    # nenhum processo ativo tem `valor_causa` preenchido (campo opcional).
    valor_total_processos_ativos = processos_ativos_q.with_entities(
        func.coalesce(func.sum(Processo.valor_causa), 0)
    ).scalar()
    total_clientes = clientes_q.filter(Cliente.ativo == True).count()  # noqa: E712
    # "Em atenção" e "perdidos" agora são janelas SEM sobreposição (antes,
    # `prazos_vencendo` usava `<= limite_alerta` sem piso em `hoje`, o que
    # incluía prazo já vencido também — o mesmo prazo podia contar nos
    # dois KPIs ao mesmo tempo). E a contagem do card não fica mais presa
    # ao `.limit(8)` da lista de prévia — antes, com mais de 8 prazos em
    # atenção, o número mostrado no card ficava sempre travado em "8",
    # escondendo quantos realmente havia (só aparecia certo por coincidência).
    prazos_em_atencao_q = prazos_q.filter(
        Prazo.status == "pendente",
        Prazo.data_vencimento >= hoje,
        Prazo.data_vencimento <= limite_alerta,
    )
    prazos_em_atencao_count = prazos_em_atencao_q.count()
    prazos_vencendo = prazos_em_atencao_q.order_by(Prazo.data_vencimento).limit(8).all()
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
        valor_total_processos_ativos=valor_total_processos_ativos,
        total_clientes=total_clientes,
        prazos_vencendo=prazos_vencendo,
        prazos_em_atencao_count=prazos_em_atencao_count,
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
