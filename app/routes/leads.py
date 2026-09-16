"""
Captação de clientes — quadro kanban de Leads (ver app/models/lead.py pra
contexto completo do pedido/decisão). Escopo desta rodada, aprovado
explicitamente pelo usuário entre 3 opções: só o pipeline manual (cadastro
+ quadro + conversão em cliente) — SEM integração automática de WhatsApp
nem triagem por Agente de IA (isso ficou registrado como ideia futura em
PENDENCIAS.md, não implementado aqui).

Mesmo padrão de isolamento multi-tenant de app/routes/clientes.py:
`aplicar_escopo_unidade` pra listar, `unidade_id_para_novo_registro` +
`checar_acesso_unidade_ou_403` pra criar, e `checar_acesso_unidade_ou_403`
de novo pra abrir/editar um lead específico.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Lead, Cliente
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, \
    checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo
from app.utils.notificacoes import registrar_log

leads_bp = Blueprint("leads", __name__)


def _parse_valor(valor):
    if not valor:
        return None
    try:
        return Decimal(str(valor).replace(",", "."))
    except InvalidOperation:
        return None


@leads_bp.route("/")
@login_required
def kanban():
    leads = aplicar_escopo_unidade(Lead.query, Lead).order_by(Lead.criado_em.desc()).all()
    colunas = {etapa: [] for etapa in Lead.ETAPAS}
    for lead in leads:
        colunas.setdefault(lead.etapa, []).append(lead)
    return render_template("leads/kanban.html", colunas=colunas, etapas=Lead.ETAPAS,
                            nomes_etapas=Lead.NOMES_ETAPAS)


@leads_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    unidades = unidades_do_escopo() if current_user.is_admin else None
    responsaveis = usuarios_do_escopo()

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe ao menos o nome do contato.", "danger")
            return render_template("leads/form.html", lead=None, unidades=unidades,
                                    responsaveis=responsaveis, origens=Lead.ORIGENS,
                                    nomes_origens=Lead.NOMES_ORIGENS)

        responsavel_id = request.form.get("responsavel_id") or None

        lead = Lead(
            nome=nome,
            telefone=request.form.get("telefone") or None,
            whatsapp=request.form.get("whatsapp") or None,
            email=request.form.get("email") or None,
            area_interesse=request.form.get("area_interesse") or None,
            origem=request.form.get("origem") or None,
            valor_estimado_causa=_parse_valor(request.form.get("valor_estimado_causa")),
            observacoes=request.form.get("observacoes") or None,
            responsavel_id=int(responsavel_id) if responsavel_id else None,
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
        )
        db.session.add(lead)
        db.session.flush()
        registrar_log(current_user, "criou", "Lead", lead.id, lead.nome)
        db.session.commit()
        flash("Contato cadastrado no funil de captação.", "success")
        return redirect(url_for("leads.detalhe", lead_id=lead.id))

    return render_template("leads/form.html", lead=None, unidades=unidades, responsaveis=responsaveis,
                            origens=Lead.ORIGENS, nomes_origens=Lead.NOMES_ORIGENS)


@leads_bp.route("/<int:lead_id>")
@login_required
def detalhe(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    checar_acesso_unidade_ou_403(lead.unidade_id)
    return render_template("leads/detalhe.html", lead=lead, etapas=Lead.ETAPAS,
                            nomes_etapas=Lead.NOMES_ETAPAS)


@leads_bp.route("/<int:lead_id>/editar", methods=["GET", "POST"])
@login_required
def editar(lead_id):
    lead = db.get_or_404(Lead, lead_id)
    checar_acesso_unidade_ou_403(lead.unidade_id)
    unidades = unidades_do_escopo() if current_user.is_admin else None
    responsaveis = usuarios_do_escopo()

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe ao menos o nome do contato.", "danger")
            return render_template("leads/form.html", lead=lead, unidades=unidades,
                                    responsaveis=responsaveis, origens=Lead.ORIGENS,
                                    nomes_origens=Lead.NOMES_ORIGENS)

        responsavel_id = request.form.get("responsavel_id") or None
        lead.nome = nome
        lead.telefone = request.form.get("telefone") or None
        lead.whatsapp = request.form.get("whatsapp") or None
        lead.email = request.form.get("email") or None
        lead.area_interesse = request.form.get("area_interesse") or None
        lead.origem = request.form.get("origem") or None
        lead.valor_estimado_causa = _parse_valor(request.form.get("valor_estimado_causa"))
        lead.observacoes = request.form.get("observacoes") or None
        lead.responsavel_id = int(responsavel_id) if responsavel_id else None

        if current_user.is_admin:
            nova_unidade_id = request.form.get("unidade_id")
            if nova_unidade_id:
                nova_unidade_id = int(nova_unidade_id)
                checar_acesso_unidade_ou_403(nova_unidade_id)
                lead.unidade_id = nova_unidade_id

        registrar_log(current_user, "editou", "Lead", lead.id, lead.nome)
        db.session.commit()
        flash("Contato atualizado.", "success")
        return redirect(url_for("leads.detalhe", lead_id=lead.id))

    return render_template("leads/form.html", lead=lead, unidades=unidades, responsaveis=responsaveis,
                            origens=Lead.ORIGENS, nomes_origens=Lead.NOMES_ORIGENS)


@leads_bp.route("/<int:lead_id>/mover", methods=["POST"])
@login_required
def mover(lead_id):
    """Muda a etapa do lead no funil — usado pelo seletor de cada card do
    quadro kanban (app/templates/leads/kanban.html) e pela tela de
    detalhe. Não é drag-and-drop (mantém a tela simples e acessível por
    teclado); se um dia fizer sentido adicionar arrastar-e-soltar, dá pra
    fazer só de JS por cima, sem mudar esta rota."""
    lead = db.get_or_404(Lead, lead_id)
    checar_acesso_unidade_ou_403(lead.unidade_id)

    nova_etapa = request.form.get("etapa")
    if nova_etapa not in Lead.ETAPAS:
        flash("Etapa inválida.", "danger")
        return redirect(url_for("leads.detalhe", lead_id=lead.id))

    if nova_etapa == Lead.ETAPA_CONVERTIDO and not lead.cliente_id:
        flash('Use o botão "Converter em cliente" pra mover pra esta etapa — ele já cria o cadastro '
              'de cliente a partir dos dados deste contato.', "warning")
        return redirect(request.referrer or url_for("leads.detalhe", lead_id=lead.id))

    lead.etapa = nova_etapa
    if nova_etapa == Lead.ETAPA_PERDIDO:
        lead.motivo_perda = request.form.get("motivo_perda") or lead.motivo_perda
    lead.atualizado_em = datetime.utcnow()
    registrar_log(current_user, "moveu_etapa", "Lead", lead.id, f"{lead.nome} -> {nova_etapa}")
    db.session.commit()
    flash(f'"{lead.nome}" movido para "{lead.nome_etapa}".', "success")
    return redirect(request.referrer or url_for("leads.detalhe", lead_id=lead.id))


@leads_bp.route("/<int:lead_id>/converter", methods=["POST"])
@login_required
def converter(lead_id):
    """Cria o Cliente de verdade a partir dos dados já coletados no Lead
    (evita digitar tudo de novo) e fecha o funil deste contato como
    "convertido". Mantém o vínculo lead.cliente_id pra sempre dar pra ver
    de onde este cliente veio."""
    lead = db.get_or_404(Lead, lead_id)
    checar_acesso_unidade_ou_403(lead.unidade_id)

    if lead.cliente_id:
        flash("Este contato já foi convertido em cliente.", "info")
        return redirect(url_for("clientes.detalhe", cliente_id=lead.cliente_id))

    tipo_pessoa = request.form.get("tipo_pessoa", "PF")
    if tipo_pessoa not in ("PF", "PJ"):
        tipo_pessoa = "PF"

    observacoes_origem = f"Convertido do funil de captação (lead #{lead.id})."
    if lead.observacoes:
        observacoes_origem += f"\n\nObservações do contato original:\n{lead.observacoes}"

    cliente = Cliente(
        tipo_pessoa=tipo_pessoa,
        nome=lead.nome,
        email=lead.email,
        telefone=lead.telefone,
        whatsapp=lead.whatsapp,
        observacoes=observacoes_origem,
        unidade_id=lead.unidade_id,
        criado_por_id=current_user.id,
    )
    db.session.add(cliente)
    db.session.flush()

    lead.cliente_id = cliente.id
    lead.etapa = Lead.ETAPA_CONVERTIDO
    lead.atualizado_em = datetime.utcnow()

    registrar_log(current_user, "converteu_lead", "Lead", lead.id, f"{lead.nome} -> Cliente #{cliente.id}")
    registrar_log(current_user, "criou_via_lead", "Cliente", cliente.id, cliente.nome)
    db.session.commit()

    flash(f'"{lead.nome}" convertido em cliente — revise/complete o cadastro (CPF/CNPJ, endereço etc.) '
          f"abaixo.", "success")
    return redirect(url_for("clientes.editar", cliente_id=cliente.id))
