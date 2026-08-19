"""
Licenciamento do lado da empresa cliente: ver o status da própria
licença e pagar via Mercado Pago. Mostra SÓ o valor negociado com essa
empresa — nunca uma "tabela de preços" que deixaria óbvio que o valor é
negociável entre empresas.
"""
from datetime import date, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Licenca, Pagamento, Empresa, Unidade, Usuario, EmpresaModulo
from app.utils.acesso import apenas_admin
from app.utils.notificacoes import registrar_log, notificar
from app.utils.mercadopago import criar_preferencia_pagamento, consultar_pagamento
from app.utils.modulos import catalogo_ativo, solicitar_modulo as solicitar_modulo_util

licenciamento_bp = Blueprint("licenciamento", __name__)


@licenciamento_bp.route("/minha-licenca")
@login_required
@apenas_admin
def minha_licenca():
    empresa = current_user.empresa
    if empresa is None or empresa.dono_da_plataforma:
        flash("Esta área é só para empresas clientes.", "warning")
        return redirect(url_for("dashboard.index"))
    licenca = empresa.licenca
    return render_template("licenciamento/minha_licenca.html", empresa=empresa, licenca=licenca)


@licenciamento_bp.route("/minha-licenca/pagar", methods=["POST"])
@login_required
@apenas_admin
def pagar_licenca():
    empresa = current_user.empresa
    if empresa is None or empresa.dono_da_plataforma:
        flash("Esta área é só para empresas clientes.", "warning")
        return redirect(url_for("dashboard.index"))

    licenca = empresa.licenca
    if licenca is None:
        flash("Sua empresa ainda não tem um plano definido. Entre em contato com o suporte.", "danger")
        return redirect(url_for("licenciamento.minha_licenca"))

    pagamento = Pagamento(
        licenca_id=licenca.id, valor=licenca.valor_negociado, plano=licenca.plano, status="pendente",
    )
    db.session.add(pagamento)
    db.session.flush()

    try:
        preferencia = criar_preferencia_pagamento(
            titulo=f"Licença {licenca.plano} — {empresa.nome}",
            valor=licenca.valor_negociado,
            referencia_externa=str(pagamento.id),
            email_pagador=current_user.email,
            url_sucesso=url_for("licenciamento.pagamento_retorno", status="sucesso", _external=True),
            url_falha=url_for("licenciamento.pagamento_retorno", status="falha", _external=True),
            url_pendente=url_for("licenciamento.pagamento_retorno", status="pendente", _external=True),
            url_notificacao=url_for("licenciamento.webhook_mercadopago", _external=True),
        )
    except Exception as e:
        db.session.rollback()
        flash(f"Não foi possível iniciar o pagamento agora: {e}", "danger")
        return redirect(url_for("licenciamento.minha_licenca"))

    pagamento.mercadopago_preference_id = preferencia.get("id")
    registrar_log(current_user, "iniciou_pagamento", "Licenca", licenca.id, str(pagamento.id))
    db.session.commit()

    url_checkout = preferencia.get("init_point") or preferencia.get("sandbox_init_point")
    return redirect(url_checkout)


@licenciamento_bp.route("/minha-licenca/retorno/<status>")
@login_required
def pagamento_retorno(status):
    """Página de volta do Checkout Pro. O status real da licença só é
    confirmado pelo webhook — esta tela é só feedback visual imediato."""
    mensagens = {
        "sucesso": ("Pagamento concluído! Pode levar alguns instantes para confirmarmos.", "success"),
        "falha": ("O pagamento não foi concluído.", "danger"),
        "pendente": ("Pagamento em processamento.", "warning"),
    }
    texto, categoria = mensagens.get(status, ("Status desconhecido.", "warning"))
    flash(texto, categoria)
    return redirect(url_for("licenciamento.minha_licenca"))


@licenciamento_bp.route("/modulos")
@login_required
@apenas_admin
def modulos():
    """
    Módulos contratados pela própria empresa + catálogo do que mais dá
    pra pedir. Diferente do catálogo que o admin desenvolvedor vê em
    /plataforma/modulos: aqui não aparece preço sugerido nem quem é
    "obrigatório" — a empresa só vê o que já tem e o que pode solicitar
    (mesmo espírito de nunca expor "tabela de preços", ver
    app/models/licenca.py).
    """
    empresa = current_user.empresa
    if empresa is None or empresa.dono_da_plataforma:
        flash("Esta área é só para empresas clientes.", "warning")
        return redirect(url_for("dashboard.index"))

    associacoes = {a.modulo_id: a for a in EmpresaModulo.query.filter_by(empresa_id=empresa.id).all()}
    catalogo = catalogo_ativo()
    linhas = [
        dict(modulo=m, associacao=associacoes.get(m.id))
        for m in catalogo
        if not m.obrigatorio  # obrigatório nem aparece — já vem sempre, não é algo pra "pedir"
    ]
    return render_template("licenciamento/modulos.html", empresa=empresa, linhas=linhas)


@licenciamento_bp.route("/modulos/<int:modulo_id>/solicitar", methods=["POST"])
@login_required
@apenas_admin
def solicitar_modulo(modulo_id):
    from app.models import Modulo

    empresa = current_user.empresa
    if empresa is None or empresa.dono_da_plataforma:
        flash("Esta área é só para empresas clientes.", "warning")
        return redirect(url_for("dashboard.index"))

    modulo = db.get_or_404(Modulo, modulo_id)
    if modulo.obrigatorio:
        flash("Esse módulo já está sempre incluído — não precisa solicitar.", "info")
        return redirect(url_for("licenciamento.modulos"))

    assoc = EmpresaModulo.query.filter_by(empresa_id=empresa.id, modulo_id=modulo.id).first()
    if assoc is not None and assoc.esta_liberado():
        flash(f"Sua empresa já tem o módulo \"{modulo.nome}\" ativo.", "info")
        return redirect(url_for("licenciamento.modulos"))
    if assoc is not None and assoc.status == "solicitado":
        flash(f"Já existe um pedido em aberto para \"{modulo.nome}\" — aguarde nosso retorno.", "info")
        return redirect(url_for("licenciamento.modulos"))

    solicitar_modulo_util(empresa, modulo, solicitado_por=current_user)
    registrar_log(current_user, "solicitou_modulo", "Empresa", empresa.id, modulo.chave)

    # avisa quem pode aprovar (admins da empresa dona da plataforma) — sem
    # isso o pedido só apareceria se alguém entrasse em
    # /plataforma/empresas/<id>/modulos por conta própria.
    admins_dev = (
        Usuario.query.join(Unidade).join(Empresa)
        .filter(Empresa.dono_da_plataforma.is_(True), Usuario.papel == "admin", Usuario.ativo.is_(True))
        .all()
    )
    for admin_dev in admins_dev:
        notificar(
            admin_dev.id,
            titulo=f"Pedido de módulo — {empresa.nome}",
            mensagem=f"{current_user.nome} solicitou o módulo \"{modulo.nome}\" para \"{empresa.nome}\".",
            tipo="info",
            link=url_for("plataforma.modulos_empresa", empresa_id=empresa.id),
        )

    db.session.commit()
    flash(f"Pedido enviado! Assim que aprovarmos o módulo \"{modulo.nome}\", ele aparece liberado aqui.", "success")
    return redirect(url_for("licenciamento.modulos"))


@licenciamento_bp.route("/webhooks/mercadopago", methods=["POST"])
def webhook_mercadopago():
    """
    Notificação assíncrona do Mercado Pago. Formato oficial: querystring
    ou corpo JSON com `type`/`topic` e `data.id` (ID do pagamento).
    Sem login — o Mercado Pago chama isso diretamente. A validação de
    autenticidade é feita consultando o próprio pagamento de volta na API
    (com nosso Access Token) em vez de confiar no corpo da notificação.
    """
    payload = request.get_json(silent=True) or {}
    tipo = request.args.get("type") or payload.get("type") or payload.get("topic")
    payment_id = request.args.get("data.id") or (payload.get("data") or {}).get("id")

    if tipo != "payment" or not payment_id:
        return "", 200  # notificação de outro tipo (ex: merchant_order) — ignorada

    try:
        dados_pagamento = consultar_pagamento(payment_id)
    except Exception:
        return "", 200  # não derruba o webhook; Mercado Pago tenta de novo depois

    referencia = dados_pagamento.get("external_reference")
    status_mp = dados_pagamento.get("status")  # approved, pending, rejected, cancelled, refunded

    pagamento = Pagamento.query.get(int(referencia)) if referencia else None
    if pagamento is None:
        return "", 200

    pagamento.mercadopago_payment_id = str(payment_id)
    pagamento.mercadopago_status_detail = dados_pagamento.get("status_detail")

    mapa_status = {
        "approved": "aprovado", "pending": "pendente", "in_process": "pendente",
        "rejected": "rejeitado", "cancelled": "cancelado", "refunded": "estornado",
    }
    pagamento.status = mapa_status.get(status_mp, pagamento.status)

    if pagamento.status == "aprovado" and pagamento.licenca.status != "ativa":
        from datetime import datetime
        pagamento.pago_em = datetime.utcnow()
        licenca = pagamento.licenca
        licenca.status = "ativa"
        licenca.plano = pagamento.plano
        base = licenca.data_fim if (licenca.data_fim and licenca.data_fim >= date.today()) else date.today()
        licenca.data_inicio = licenca.data_inicio or date.today()
        licenca.data_fim = base + timedelta(days=Licenca.DIAS_POR_PLANO[pagamento.plano])

    db.session.commit()
    return "", 200
