from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Lancamento, Processo, Cliente, Unidade, Apontamento
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo
from app.utils.notificacoes import registrar_log

financeiro_bp = Blueprint("financeiro", __name__)


def _parse_data(valor):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _filtro_conta_terceiros(eh_terceiros):
    # Lancamento.conta_terceiros é nullable=True de propósito (ver comentário
    # no modelo) — lançamentos antigos, criados antes desta coluna existir,
    # ficam com NULL depois do ALTER TABLE em produção. Em SQL, `NULL = 0`
    # não é verdadeiro, então comparar direto com `== False` faria esses
    # lançamentos antigos sumirem tanto da visão operacional quanto da de
    # terceiros. Por isso NULL é sempre tratado como "não é de terceiros"
    # (comportamento antigo, antes de existir a distinção).
    if eh_terceiros:
        return Lancamento.conta_terceiros.is_(True)
    return db.or_(Lancamento.conta_terceiros.is_(False), Lancamento.conta_terceiros.is_(None))


@financeiro_bp.route("/")
@login_required
def listar():
    # "conta" (ver PENDENCIAS.md, seção -39): separa o caixa OPERACIONAL do
    # escritório (receita/despesa de verdade — o padrão, é o que esta tela
    # sempre mostrou) da conta de TERCEIROS (valor que só passa pelo
    # escritório, ex: depósito judicial, sem ser receita/despesa própria —
    # nunca deve ser somado junto no mesmo total, isso mascararia o caixa
    # real do escritório). Default é sempre "operacional".
    conta = request.args.get("conta", "operacional")
    eh_terceiros = conta == "terceiros"

    query = aplicar_escopo_unidade(Lancamento.query, Lancamento).filter(_filtro_conta_terceiros(eh_terceiros))

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

    base_totais = aplicar_escopo_unidade(Lancamento.query, Lancamento).filter(_filtro_conta_terceiros(eh_terceiros))
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

    # Saldo em trânsito na conta de terceiros — mostrado sempre (mesmo
    # olhando "operacional"), como lembrete visual de que existe dinheiro
    # de cliente retido, sem misturar no cálculo dos totais operacionais.
    saldo_terceiros = aplicar_escopo_unidade(Lancamento.query, Lancamento).filter(
        Lancamento.conta_terceiros.is_(True), Lancamento.status == "pendente"
    )
    if current_user.is_admin and unidade_filtro:
        saldo_terceiros = saldo_terceiros.filter(Lancamento.unidade_id == int(unidade_filtro))
    saldo_terceiros = saldo_terceiros.with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()

    unidades = unidades_do_escopo() if current_user.is_admin else None

    return render_template("financeiro/listar.html", lancamentos=lancamentos, unidades=unidades,
                            total_a_receber=total_a_receber, total_recebido_mes=total_recebido_mes,
                            total_atrasado=total_atrasado, saldo_terceiros=saldo_terceiros,
                            conta=conta, status=status, natureza=natureza)


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
            conta_terceiros=bool(request.form.get("conta_terceiros")),
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


@financeiro_bp.route("/gerar-cobranca-horas", methods=["GET", "POST"])
@login_required
def gerar_cobranca_horas():
    """
    Fecha a lacuna apontada no relatório de avaliação de 20/08/2026 ("Vínculo
    timesheet → faturamento"): até aqui, apontamento de hora (Apontamento) e
    lançamento financeiro (Lancamento) existiam lado a lado sem nenhuma
    ligação — transformar horas trabalhadas em cobrança era um cálculo
    manual, por fora do sistema.

    Fluxo em duas etapas na mesma rota: (1) GET só com `processo_id`
    escolhido lista os apontamentos FATURÁVEIS e AINDA NÃO FATURADOS
    (`lancamento_id is None`) desse processo, com uma sugestão de valor
    (soma das horas × `Cliente.valor_hora_padrao`, quando cadastrado — nunca
    obrigatório, sempre editável); (2) POST cria o Lancamento com o valor
    que o usuário confirmar (não necessariamente a sugestão) e vincula cada
    apontamento selecionado a ele — dali em diante esse apontamento some da
    lista de "não faturados" e nunca pode ser cobrado de novo por engano.
    """
    processos = aplicar_escopo_unidade(Processo.query, Processo).order_by(Processo.numero_processo).all()

    if request.method == "POST":
        processo = db.get_or_404(Processo, request.form.get("processo_id"))
        checar_acesso_unidade_ou_403(processo.unidade_id)

        ids_selecionados = request.form.getlist("apontamento_ids")
        if not ids_selecionados:
            flash("Selecione ao menos um apontamento de hora para gerar a cobrança.", "danger")
            return redirect(url_for("financeiro.gerar_cobranca_horas", processo_id=processo.id))

        # Revalida cada apontamento no servidor (nunca confia só no que veio
        # do form) — precisa pertencer a este MESMO processo, estar
        # faturável e ainda não ter sido faturado; qualquer um que não bater
        # (ex: já foi faturado em outra aba por outra pessoa nesse meio
        # tempo) é silenciosamente ignorado, nunca cobrado duas vezes.
        apontamentos = Apontamento.query.filter(
            Apontamento.id.in_(ids_selecionados), Apontamento.processo_id == processo.id,
            Apontamento.faturavel.is_(True), Apontamento.lancamento_id.is_(None),
        ).all()
        if not apontamentos:
            flash("Os apontamentos selecionados não estão mais disponíveis para faturar (podem já ter sido faturados).", "danger")
            return redirect(url_for("financeiro.gerar_cobranca_horas", processo_id=processo.id))

        try:
            valor = Decimal(request.form.get("valor", "0").replace(",", "."))
        except (InvalidOperation, ValueError):
            valor = Decimal("0")
        if valor <= 0:
            flash("Informe um valor válido para a cobrança.", "danger")
            return redirect(url_for("financeiro.gerar_cobranca_horas", processo_id=processo.id))

        total_horas = sum((a.horas for a in apontamentos), Decimal("0"))
        lancamento = Lancamento(
            descricao=request.form.get("descricao") or f"Honorários — {total_horas}h apontadas em {processo.numero_processo or processo.numero_interno}",
            tipo="honorario", natureza="receita", valor=valor, status="pendente",
            data_vencimento=_parse_data(request.form.get("data_vencimento")),
            observacoes=f"Gerado a partir de {len(apontamentos)} apontamento(s) de hora ({total_horas}h no total).",
            unidade_id=processo.unidade_id, processo_id=processo.id, cliente_id=processo.cliente_id,
            criado_por_id=current_user.id,
        )
        db.session.add(lancamento)
        db.session.flush()
        for a in apontamentos:
            a.lancamento_id = lancamento.id
        registrar_log(current_user, "gerou_cobranca_horas", "Lancamento", lancamento.id,
                      f"{len(apontamentos)} apontamento(s), {total_horas}h, processo {processo.id}")
        db.session.commit()
        flash(f"Cobrança gerada a partir de {len(apontamentos)} apontamento(s) de hora ({total_horas}h).", "success")
        return redirect(url_for("financeiro.listar"))

    processo_id = request.args.get("processo_id")
    processo_selecionado = None
    apontamentos_elegiveis = []
    total_horas = Decimal("0")
    sugestao_valor = None
    if processo_id:
        processo_selecionado = db.get_or_404(Processo, processo_id)
        checar_acesso_unidade_ou_403(processo_selecionado.unidade_id)
        apontamentos_elegiveis = Apontamento.query.filter(
            Apontamento.processo_id == processo_selecionado.id,
            Apontamento.faturavel.is_(True), Apontamento.lancamento_id.is_(None),
        ).order_by(Apontamento.data).all()
        total_horas = sum((a.horas for a in apontamentos_elegiveis), Decimal("0"))
        cliente = processo_selecionado.cliente
        if cliente and cliente.valor_hora_padrao:
            sugestao_valor = total_horas * cliente.valor_hora_padrao

    return render_template(
        "financeiro/gerar_cobranca_horas.html", processos=processos,
        processo_selecionado=processo_selecionado, apontamentos_elegiveis=apontamentos_elegiveis,
        total_horas=total_horas, sugestao_valor=sugestao_valor,
    )


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
