"""
Agenda integrada — visão única de calendário combinando prazos, audiências
e tarefas (item 1 do briefing de paridade: "agenda integrada", que antes
vivia espalhado em três listas separadas).

100% server-side, sem biblioteca JS de calendário — consistente com o
resto do sistema (Jinja2 + Bootstrap, sem SPA).
"""
from calendar import Calendar
from datetime import date, timedelta

from flask import Blueprint, render_template, request, url_for
from flask_login import login_required, current_user

from app.models import Prazo, Audiencia, Tarefa, Processo
from app.utils.acesso import checar_acesso_unidade_ou_403  # noqa: F401 (mantém padrão de import do módulo)

agenda_bp = Blueprint("agenda", __name__)

NOMES_MES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
DIAS_SEMANA = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


@agenda_bp.route("/")
@login_required
def index():
    hoje = date.today()
    ano = request.args.get("ano", hoje.year, type=int)
    mes = request.args.get("mes", hoje.month, type=int)
    if mes < 1:
        mes, ano = 12, ano - 1
    elif mes > 12:
        mes, ano = 1, ano + 1

    primeiro_dia = date(ano, mes, 1)
    if mes == 12:
        ultimo_dia = date(ano + 1, 1, 1) - timedelta(days=1)
    else:
        ultimo_dia = date(ano, mes + 1, 1) - timedelta(days=1)

    prazos_q = Prazo.query.join(Processo).filter(
        Prazo.deletado_em.is_(None),
        Prazo.data_vencimento.between(primeiro_dia, ultimo_dia),
    )
    audiencias_q = Audiencia.query.join(Processo).filter(
        Audiencia.data_hora >= primeiro_dia,
        Audiencia.data_hora < ultimo_dia + timedelta(days=1),
    )
    tarefas_q = Tarefa.query.filter(
        Tarefa.data_vencimento.isnot(None),
        Tarefa.data_vencimento.between(primeiro_dia, ultimo_dia),
    )
    if not current_user.is_admin:
        prazos_q = prazos_q.filter(Processo.unidade_id == current_user.unidade_id)
        audiencias_q = audiencias_q.filter(Processo.unidade_id == current_user.unidade_id)
        tarefas_q = tarefas_q.filter(Tarefa.unidade_id == current_user.unidade_id)

    eventos_por_dia = {}

    def _add(dia, tipo, titulo, url, situacao):
        eventos_por_dia.setdefault(dia, []).append(
            dict(tipo=tipo, titulo=titulo, url=url, situacao=situacao)
        )

    for p in prazos_q.all():
        situacao = "vencido" if (p.data_vencimento < hoje and p.status not in ("cumprido",)) else p.status
        _add(p.data_vencimento, "prazo", p.descricao,
             url_for("processos.detalhe", processo_id=p.processo_id), situacao)

    for a in audiencias_q.all():
        titulo = f"{a.data_hora.strftime('%H:%M')} · {a.tipo or 'Audiência'}"
        _add(a.data_hora.date(), "audiencia", titulo,
             url_for("processos.detalhe", processo_id=a.processo_id), a.status)

    for t in tarefas_q.all():
        _add(t.data_vencimento, "tarefa", t.titulo, url_for("tarefas.listar"), t.status)

    cal = Calendar(firstweekday=0)  # semana começa na segunda
    semanas = cal.monthdatescalendar(ano, mes)

    mes_anterior, ano_mes_anterior = (12, ano - 1) if mes == 1 else (mes - 1, ano)
    mes_seguinte, ano_mes_seguinte = (1, ano + 1) if mes == 12 else (mes + 1, ano)

    total_prazos = prazos_q.count()
    total_audiencias = audiencias_q.count()
    total_tarefas = tarefas_q.count()

    return render_template(
        "agenda/index.html",
        semanas=semanas, eventos_por_dia=eventos_por_dia, hoje=hoje,
        ano=ano, mes=mes, nome_mes=NOMES_MES[mes], dias_semana=DIAS_SEMANA,
        mes_anterior=mes_anterior, ano_mes_anterior=ano_mes_anterior,
        mes_seguinte=mes_seguinte, ano_mes_seguinte=ano_mes_seguinte,
        total_prazos=total_prazos, total_audiencias=total_audiencias, total_tarefas=total_tarefas,
    )
