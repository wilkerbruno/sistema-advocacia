"""
Agenda integrada — visão única de calendário combinando prazos, audiências,
tarefas e compromissos (item 1 do briefing de paridade: "agenda
integrada", que antes vivia espalhado em três listas separadas).

100% server-side, sem biblioteca JS de calendário — consistente com o
resto do sistema (Jinja2 + Bootstrap, sem SPA).

Compromisso (reunião/evento livre, com lembrete configurável) foi
adicionado a pedido do usuário — ver app/models/compromisso.py para o
modelo e enviar_lembretes_compromissos.py para o job que dispara o
lembrete na hora marcada.
"""
from calendar import Calendar
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Prazo, Audiencia, Tarefa, Processo, Compromisso, Cliente
from app.utils.acesso import (checar_acesso_unidade_ou_403, aplicar_escopo_unidade,
                               unidade_id_para_novo_registro, unidades_do_escopo,
                               usuarios_do_escopo)
from app.utils.notificacoes import registrar_log

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
    compromissos_q = Compromisso.query.filter(
        Compromisso.status != "cancelado",
        Compromisso.data_hora >= primeiro_dia,
        Compromisso.data_hora < ultimo_dia + timedelta(days=1),
    )
    if not current_user.is_admin:
        prazos_q = prazos_q.filter(Processo.unidade_id == current_user.unidade_id)
        audiencias_q = audiencias_q.filter(Processo.unidade_id == current_user.unidade_id)
        tarefas_q = tarefas_q.filter(Tarefa.unidade_id == current_user.unidade_id)
        compromissos_q = compromissos_q.filter(Compromisso.unidade_id == current_user.unidade_id)

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

    for c in compromissos_q.all():
        titulo = f"{c.data_hora.strftime('%H:%M')} · {c.titulo}"
        _add(c.data_hora.date(), "compromisso", titulo,
             url_for("agenda.editar_compromisso", compromisso_id=c.id), c.status)

    cal = Calendar(firstweekday=0)  # semana começa na segunda
    semanas = cal.monthdatescalendar(ano, mes)

    mes_anterior, ano_mes_anterior = (12, ano - 1) if mes == 1 else (mes - 1, ano)
    mes_seguinte, ano_mes_seguinte = (1, ano + 1) if mes == 12 else (mes + 1, ano)

    total_prazos = prazos_q.count()
    total_audiencias = audiencias_q.count()
    total_tarefas = tarefas_q.count()
    total_compromissos = compromissos_q.count()

    return render_template(
        "agenda/index.html",
        semanas=semanas, eventos_por_dia=eventos_por_dia, hoje=hoje,
        ano=ano, mes=mes, nome_mes=NOMES_MES[mes], dias_semana=DIAS_SEMANA,
        mes_anterior=mes_anterior, ano_mes_anterior=ano_mes_anterior,
        mes_seguinte=mes_seguinte, ano_mes_seguinte=ano_mes_seguinte,
        total_prazos=total_prazos, total_audiencias=total_audiencias,
        total_tarefas=total_tarefas, total_compromissos=total_compromissos,
    )


# ---------------------- Compromissos (reunião/evento livre) ----------------------

def _smtp_configurado():
    cfg = current_app.config
    return bool(cfg.get("SMTP_HOST") and cfg.get("SMTP_USER") and cfg.get("SMTP_PASSWORD"))


def _whatsapp_configurado():
    return bool(current_app.config.get("WHATSAPP_BRIDGE_URL"))


def _parse_datetime_local(valor):
    """Converte o valor de um <input type="datetime-local"> ('2026-08-20T14:30')
    para datetime, ou None se vazio/mal formado."""
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


@agenda_bp.route("/compromissos/novo", methods=["GET", "POST"])
@login_required
def novo_compromisso():
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()
    responsaveis = usuarios_do_escopo()
    unidades = unidades_do_escopo() if current_user.is_admin else None

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        data_hora = _parse_datetime_local(request.form.get("data_hora"))
        if not data_hora:
            flash("Informe uma data/hora válida para o compromisso.", "danger")
            return redirect(url_for("agenda.novo_compromisso"))

        notificar_em = _parse_datetime_local(request.form.get("notificar_em"))
        if notificar_em and notificar_em >= data_hora:
            flash("O horário da notificação precisa ser antes do horário do compromisso.", "danger")
            return redirect(url_for("agenda.novo_compromisso"))

        responsavel_id = request.form.get("responsavel_id", type=int) or current_user.id

        compromisso = Compromisso(
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
            responsavel_id=responsavel_id,
            titulo=request.form["titulo"],
            descricao=request.form.get("descricao") or None,
            local=request.form.get("local") or None,
            data_hora=data_hora,
            cliente_id=request.form.get("cliente_id", type=int) or None,
            notificar_em=notificar_em,
            enviar_whatsapp=bool(request.form.get("enviar_whatsapp")),
        )
        db.session.add(compromisso)
        db.session.flush()
        registrar_log(current_user, "criou", "Compromisso", compromisso.id, compromisso.titulo)
        db.session.commit()
        flash("Compromisso agendado com sucesso.", "success")
        return redirect(url_for("agenda.index", ano=data_hora.year, mes=data_hora.month))

    return render_template("agenda/compromisso_form.html", compromisso=None,
                            clientes=clientes, responsaveis=responsaveis, unidades=unidades,
                            smtp_configurado=_smtp_configurado(),
                            whatsapp_configurado=_whatsapp_configurado())


@agenda_bp.route("/compromissos/<int:compromisso_id>/editar", methods=["GET", "POST"])
@login_required
def editar_compromisso(compromisso_id):
    compromisso = db.get_or_404(Compromisso, compromisso_id)
    checar_acesso_unidade_ou_403(compromisso.unidade_id)

    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()
    responsaveis = usuarios_do_escopo()
    unidades = unidades_do_escopo() if current_user.is_admin else None

    if request.method == "POST":
        data_hora = _parse_datetime_local(request.form.get("data_hora"))
        if not data_hora:
            flash("Informe uma data/hora válida para o compromisso.", "danger")
            return redirect(url_for("agenda.editar_compromisso", compromisso_id=compromisso.id))

        notificar_em = _parse_datetime_local(request.form.get("notificar_em"))
        if notificar_em and notificar_em >= data_hora:
            flash("O horário da notificação precisa ser antes do horário do compromisso.", "danger")
            return redirect(url_for("agenda.editar_compromisso", compromisso_id=compromisso.id))

        # Se a data/hora do lembrete mudou (ou foi removida/recolocada),
        # limpa a marca de "já enviado" para o novo horário poder disparar
        # de novo — nunca deixa um lembrete "preso" num horário antigo.
        if notificar_em != compromisso.notificar_em:
            compromisso.notificacao_enviada_em = None

        compromisso.titulo = request.form["titulo"]
        compromisso.descricao = request.form.get("descricao") or None
        compromisso.local = request.form.get("local") or None
        compromisso.data_hora = data_hora
        compromisso.cliente_id = request.form.get("cliente_id", type=int) or None
        compromisso.notificar_em = notificar_em
        compromisso.enviar_whatsapp = bool(request.form.get("enviar_whatsapp"))
        compromisso.responsavel_id = request.form.get("responsavel_id", type=int) or compromisso.responsavel_id
        if current_user.is_admin and request.form.get("unidade_id"):
            compromisso.unidade_id = int(request.form["unidade_id"])

        registrar_log(current_user, "editou", "Compromisso", compromisso.id, compromisso.titulo)
        db.session.commit()
        flash("Compromisso atualizado.", "success")
        return redirect(url_for("agenda.index", ano=data_hora.year, mes=data_hora.month))

    return render_template("agenda/compromisso_form.html", compromisso=compromisso,
                            clientes=clientes, responsaveis=responsaveis, unidades=unidades,
                            smtp_configurado=_smtp_configurado(),
                            whatsapp_configurado=_whatsapp_configurado())


@agenda_bp.route("/compromissos/<int:compromisso_id>/cancelar", methods=["POST"])
@login_required
def cancelar_compromisso(compromisso_id):
    compromisso = db.get_or_404(Compromisso, compromisso_id)
    checar_acesso_unidade_ou_403(compromisso.unidade_id)
    compromisso.status = "cancelado"
    registrar_log(current_user, "cancelou", "Compromisso", compromisso.id, compromisso.titulo)
    db.session.commit()
    flash("Compromisso cancelado.", "info")
    return redirect(url_for("agenda.index", ano=compromisso.data_hora.year, mes=compromisso.data_hora.month))
