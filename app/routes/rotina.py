"""
Hub "Rotina" (menu simplificado, a pedido explícito): reúne em abas de uma
tela só o que antes eram três itens soltos no menu "Operação" — Tarefas,
Agenda e Horas (timesheet) — mesma ideia de
app/routes/governanca.py::entrada_processos (Cadastro por CNJ + Captação
por OAB) e app/routes/conta.py::hub (Minha conta).

Reaproveita o contexto de cada tela original através dos helpers
`_contexto_tarefas()`, `_contexto_agenda()` e `_contexto_timesheet()`
(extraídos de app/routes/tarefas.py, agenda.py e timesheet.py) — nenhuma
consulta/regra de negócio foi duplicada, e as três rotas antigas
(tarefas.listar, agenda.index, timesheet.listar) continuam existindo e
funcionando normalmente pra quem chegar direto por um link salvo.

Os três filtros (status/"somente minhas" de Tarefas, ano/mês de Agenda,
período/"somente meus apontamentos" de Horas) usam nomes de campo que não
colidem entre si na mesma URL — em especial, o checkbox "somente minhas"
de Tarefas continua se chamando `minhas` (mesmo nome de sempre), mas o de
Horas usa `horas_minhas` só dentro deste hub, pra nunca vazar um filtro de
uma aba pra outra sem querer.
"""
from flask import Blueprint, render_template, request
from flask_login import login_required

from app.routes.tarefas import _contexto_tarefas
from app.routes.agenda import _contexto_agenda
from app.routes.timesheet import _contexto_timesheet

rotina_bp = Blueprint("rotina", __name__, url_prefix="/rotina")

ABAS_VALIDAS = ("tarefas", "agenda", "horas")


@rotina_bp.route("/")
@login_required
def index():
    aba_inicial = request.args.get("tab")
    if aba_inicial not in ABAS_VALIDAS:
        aba_inicial = "tarefas"

    contexto_tarefas = _contexto_tarefas(status=request.args.get("status"), somente_minhas=request.args.get("minhas"))
    contexto_agenda = _contexto_agenda(ano=request.args.get("ano", type=int), mes=request.args.get("mes", type=int))
    contexto_timesheet = _contexto_timesheet(
        somente_minhas=request.args.get("horas_minhas"),
        data_inicio=request.args.get("data_inicio"), data_fim=request.args.get("data_fim"),
    )

    return render_template(
        "rotina/index.html",
        aba_inicial=aba_inicial,
        tarefas_ctx=contexto_tarefas, agenda_ctx=contexto_agenda, timesheet_ctx=contexto_timesheet,
    )
