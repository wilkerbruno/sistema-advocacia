import io
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Lancamento, Processo, Cliente, Unidade, Apontamento
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo, requer_acesso_financeiro
from app.utils.notificacoes import registrar_log
from app.utils.financeiro_util import filtro_conta_terceiros as _filtro_conta_terceiros

financeiro_bp = Blueprint("financeiro", __name__)

_MESES_PT = ("janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")


def _parse_data(valor):
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _somar_um_mes(d):
    # Sem dateutil no projeto (ver requirements.txt) — soma um mês na mão,
    # com cuidado pro caso de dia que não existe no mês seguinte (ex: 31 de
    # janeiro vira 28/29 de fevereiro, não "3 de março").
    if d.month == 12:
        ano, mes = d.year + 1, 1
    else:
        ano, mes = d.year, d.month + 1
    ultimo_dia_mes_seguinte = monthrange(ano, mes)[1]
    return date(ano, mes, min(d.day, ultimo_dia_mes_seguinte))


def _parse_decimal(valor):
    # Aceita tanto "1234.56" quanto "1234,56" (o <input type="number"> do
    # navegador sempre manda ponto, mas alguém pode digitar/colar com
    # vírgula em algum client HTTP direto) — mesmo padrão usado em
    # clientes.py::_parse_valor_hora e financeiro.gerar_cobranca_horas.
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return Decimal(str(valor).replace(",", "."))
    except InvalidOperation:
        return None


@financeiro_bp.route("/")
@login_required
@requer_acesso_financeiro
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
@requer_acesso_financeiro
def novo():
    unidades = unidades_do_escopo() if current_user.is_admin else None
    processos = aplicar_escopo_unidade(Processo.query, Processo).order_by(Processo.numero_processo).all()
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).order_by(Cliente.nome).all()

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        modelo_cobranca = request.form.get("modelo_cobranca") or "fixo"
        if modelo_cobranca not in Lancamento.MODELOS_COBRANCA:
            modelo_cobranca = "fixo"

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
            modelo_cobranca=modelo_cobranca,
            # percentual/valor-base só fazem sentido pra "exito" — fora
            # desse modelo ficam None, mesmo que o form mande algo (ex:
            # usuário trocou de "exito" pra "fixo" sem limpar os campos).
            percentual_exito=_parse_decimal(request.form.get("percentual_exito")) if modelo_cobranca == "exito" else None,
            valor_base_exito=_parse_decimal(request.form.get("valor_base_exito")) if modelo_cobranca == "exito" else None,
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

    # Valor da causa de cada processo, pra tela sugerir automaticamente o
    # "valor-base" ao escolher modelo de cobrança "êxito" (ver
    # PENDENCIAS.md, seção -40) — só uma sugestão via JS, nunca aplicada
    # sozinha no servidor; quem lança sempre pode editar antes de salvar.
    valores_causa_processos = {
        p.id: str(p.valor_causa) for p in processos if p.valor_causa is not None
    }

    return render_template("financeiro/form.html", unidades=unidades, processos=processos, clientes=clientes,
                            valores_causa_processos=valores_causa_processos)


@financeiro_bp.route("/gerar-cobranca-horas", methods=["GET", "POST"])
@login_required
@requer_acesso_financeiro
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
@requer_acesso_financeiro
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


@financeiro_bp.route("/<int:lancamento_id>/duplicar-retainer", methods=["POST"])
@login_required
@requer_acesso_financeiro
def duplicar_retainer(lancamento_id):
    """
    Fecha a parte "retainer" do item "Modelos de cobrança" do relatório de
    20/08/2026 (PENDENCIAS.md, seção -40). Não existe fila/agendador neste
    projeto pra gerar cobrança recorrente sozinha (e não seria prudente
    criar uma cobrança financeira automaticamente sem revisão humana) —
    então "recorrente" aqui é: um botão que duplica o lançamento pro mês
    seguinte, sempre uma ação explícita de quem está usando o sistema,
    nunca algo que roda escondido.
    """
    original = db.get_or_404(Lancamento, lancamento_id)
    checar_acesso_unidade_ou_403(original.unidade_id)
    if original.modelo_cobranca != "retainer":
        abort(400)

    base = original.data_vencimento or date.today()
    novo = Lancamento(
        descricao=original.descricao,
        tipo=original.tipo,
        natureza=original.natureza,
        valor=original.valor,
        status="pendente",
        data_vencimento=_somar_um_mes(base),
        forma_pagamento=original.forma_pagamento,
        observacoes=original.observacoes,
        conta_terceiros=original.conta_terceiros,
        modelo_cobranca="retainer",
        unidade_id=original.unidade_id,
        processo_id=original.processo_id,
        cliente_id=original.cliente_id,
        criado_por_id=current_user.id,
    )
    db.session.add(novo)
    db.session.flush()
    registrar_log(current_user, "duplicou_retainer", "Lancamento", novo.id,
                  f"a partir do lançamento #{original.id}")
    db.session.commit()
    flash(f"Cobrança do mês seguinte gerada (vencimento {novo.data_vencimento.strftime('%d/%m/%Y')}).", "success")
    return redirect(url_for("financeiro.listar", conta="terceiros" if original.conta_terceiros else "operacional"))


@financeiro_bp.route("/<int:lancamento_id>/recibo")
@login_required
@requer_acesso_financeiro
def recibo(lancamento_id):
    """
    Gera um recibo em PDF pra um lançamento já pago — parte "PDF de
    recibo" do item "Modelos de cobrança" (PENDENCIAS.md, seção -40).
    Só faz sentido pra lançamento PAGO (recibo é comprovante de que algo
    foi recebido/pago — gerar um pra um lançamento ainda pendente seria
    emitir um comprovante de algo que não aconteceu).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    lancamento = db.get_or_404(Lancamento, lancamento_id)
    checar_acesso_unidade_ou_403(lancamento.unidade_id)
    if lancamento.status != "pago":
        flash("Só é possível gerar recibo de um lançamento já marcado como pago.", "danger")
        return redirect(url_for("financeiro.listar"))

    unidade = lancamento.unidade
    empresa = unidade.empresa if unidade else None

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4
    margem = 2.5 * cm
    y = altura - margem

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margem, y, empresa.nome if empresa else "Escritório de advocacia")
    y -= 0.6 * cm
    c.setFont("Helvetica", 9)
    if empresa and empresa.cnpj:
        c.drawString(margem, y, f"CNPJ: {empresa.cnpj}")
        y -= 0.45 * cm
    if unidade and unidade.endereco:
        partes_endereco = unidade.endereco
        if unidade.cidade:
            partes_endereco += f" — {unidade.cidade}/{unidade.estado or ''}"
        c.drawString(margem, y, partes_endereco)
        y -= 0.45 * cm

    y -= 0.8 * cm
    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(largura / 2, y, "RECIBO")
    y -= 1.2 * cm

    c.setFont("Helvetica-Bold", 20)
    valor_formatado = f"R$ {lancamento.valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    c.drawCentredString(largura / 2, y, valor_formatado)
    y -= 1.2 * cm

    c.setFont("Helvetica", 11)
    linhas = []
    if lancamento.cliente:
        linhas.append(f"Recebemos de {lancamento.cliente.nome} a quantia acima referente a:")
    else:
        linhas.append("Recebemos a quantia acima referente a:")
    linhas.append(lancamento.descricao)
    if lancamento.processo:
        linhas.append(f"Processo: {lancamento.processo.numero_processo or lancamento.processo.numero_interno}")
    if lancamento.parcela:
        linhas.append(f"Parcela: {lancamento.parcela}")
    data_pgto = lancamento.data_pagamento.strftime("%d/%m/%Y") if lancamento.data_pagamento else "—"
    linhas.append(f"Data do pagamento: {data_pgto}")
    if lancamento.forma_pagamento:
        linhas.append(f"Forma de pagamento: {lancamento.forma_pagamento}")

    for linha in linhas:
        c.drawString(margem, y, linha)
        y -= 0.55 * cm

    y -= 1.5 * cm
    hoje = date.today()
    data_por_extenso = f"{hoje.day} de {_MESES_PT[hoje.month - 1]} de {hoje.year}"
    cidade_data = unidade.cidade if unidade and unidade.cidade else ""
    c.drawString(margem, y, f"{cidade_data}, {data_por_extenso}." if cidade_data else f"{data_por_extenso}.")
    y -= 2 * cm
    c.line(margem, y, margem + 8 * cm, y)
    y -= 0.5 * cm
    c.setFont("Helvetica", 9)
    c.drawString(margem, y, empresa.nome if empresa else "Escritório de advocacia")

    c.setFont("Helvetica", 7)
    c.setFillGray(0.5)
    c.drawString(margem, 1.2 * cm,
                 f"Gerado por JusControl em {datetime.now().strftime('%d/%m/%Y %H:%M')} — lançamento #{lancamento.id}")

    c.showPage()
    c.save()
    buffer.seek(0)

    registrar_log(current_user, "gerou_recibo", "Lancamento", lancamento.id, lancamento.descricao)

    nome_arquivo = f"recibo_lancamento_{lancamento.id}.pdf"
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=nome_arquivo)
