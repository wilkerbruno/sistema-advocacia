"""
Administração da plataforma — visível e acessível SOMENTE para admins
desenvolvedores (usuários da empresa dona da plataforma). Nenhuma empresa
cliente tem acesso a nada aqui, nem sabe que essas telas existem.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Empresa, Unidade, Usuario, Licenca, Pagamento, Modulo, EmpresaModulo, ConfiguracaoPlataforma
from app.utils.acesso import apenas_admin_desenvolvedor
from app.utils.notificacoes import registrar_log, notificar
from app.utils.modulos import (
    catalogo_ativo, incluir_modulo_inicial, aprovar_modulo, cancelar_modulo,
)

plataforma_bp = Blueprint("plataforma", __name__)


# ---------------------- Painel de licenças (visão consolidada) ----------------------

MESES_POR_PLANO = {"mensal": 1, "trimestral": 3, "anual": 12}


@plataforma_bp.route("/licencas")
@login_required
@apenas_admin_desenvolvedor
def painel_licencas():
    filtro_status = request.args.get("status")  # ativa | pendente_pagamento | vencida | cancelada | sem_licenca
    busca = request.args.get("busca", "").strip()

    query = Empresa.query.filter_by(dono_da_plataforma=False)
    if busca:
        query = query.filter(Empresa.nome.ilike(f"%{busca}%"))
    empresas_lista = query.order_by(Empresa.nome).all()

    hoje = date.today()
    linhas = []
    totais = {"ativa": 0, "pendente_pagamento": 0, "vencida": 0, "cancelada": 0, "sem_licenca": 0}
    receita_mensal_recorrente = Decimal("0")

    for empresa in empresas_lista:
        licenca = empresa.licenca
        if licenca is None:
            status_calc = "sem_licenca"
        elif licenca.status == "ativa" and (licenca.data_fim is None or licenca.data_fim < hoje):
            status_calc = "vencida"  # estava ativa mas passou da data e ninguém renovou ainda
        else:
            status_calc = licenca.status

        totais[status_calc] = totais.get(status_calc, 0) + 1

        if status_calc == "ativa" and licenca:
            meses = MESES_POR_PLANO.get(licenca.plano, 1)
            receita_mensal_recorrente += (licenca.valor_negociado / meses)

        dias_restantes = (licenca.data_fim - hoje).days if (licenca and licenca.data_fim) else None

        linhas.append(dict(empresa=empresa, licenca=licenca, status_calc=status_calc, dias_restantes=dias_restantes))

    if filtro_status:
        linhas = [l for l in linhas if l["status_calc"] == filtro_status]

    return render_template(
        "plataforma/painel_licencas.html",
        linhas=linhas, totais=totais, total_empresas=len(empresas_lista),
        receita_mensal_recorrente=receita_mensal_recorrente,
        filtro_status=filtro_status, busca=busca,
    )


@plataforma_bp.route("/licencas/<int:empresa_id>/atualizar", methods=["POST"])
@login_required
@apenas_admin_desenvolvedor
def atualizar_licenca_rapido(empresa_id):
    """Edição rápida (valor/plano) direto da linha do painel de licenças,
    sem precisar abrir a tela de detalhe da empresa."""
    empresa = db.get_or_404(Empresa, empresa_id)
    licenca = empresa.licenca

    valor = Decimal(request.form["valor_negociado"].replace(",", "."))
    plano = request.form.get("plano", "mensal")

    if licenca is None:
        licenca = Licenca(empresa_id=empresa.id, status="pendente_pagamento")
        db.session.add(licenca)

    licenca.plano = plano
    licenca.valor_negociado = valor
    licenca.definido_por_id = current_user.id

    if request.form.get("marcar_paga_manualmente"):
        licenca.status = "ativa"
        licenca.data_inicio = date.today()
        licenca.data_fim = date.today() + timedelta(days=Licenca.DIAS_POR_PLANO[plano])

    if request.form.get("cancelar"):
        licenca.status = "cancelada"

    registrar_log(current_user, "definiu_licenca", "Empresa", empresa.id, f"{plano} R${valor}")
    db.session.commit()
    flash(f"Licença de \"{empresa.nome}\" atualizada.", "success")
    return redirect(url_for("plataforma.painel_licencas", **request.args))


@plataforma_bp.route("/empresas")
@login_required
@apenas_admin_desenvolvedor
def empresas():
    lista = Empresa.query.filter_by(dono_da_plataforma=False).order_by(Empresa.nome).all()
    return render_template("plataforma/empresas.html", empresas=lista)


@plataforma_bp.route("/empresas/nova", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def nova_empresa():
    if request.method == "POST":
        empresa = Empresa(
            nome=request.form["nome"],
            cnpj=request.form.get("cnpj"),
            email_contato=request.form.get("email_contato"),
            telefone=request.form.get("telefone"),
        )
        db.session.add(empresa)
        db.session.flush()

        # já cria a primeira unidade e o primeiro admin da empresa, pra
        # não deixar a empresa sem ninguém que consiga entrar
        unidade = Unidade(
            empresa_id=empresa.id,
            nome=request.form.get("unidade_nome") or "Matriz",
            codigo=request.form["unidade_codigo"].upper(),
        )
        db.session.add(unidade)
        db.session.flush()

        admin_empresa = Usuario(
            nome=request.form["admin_nome"],
            email=request.form["admin_email"].strip().lower(),
            papel="admin",
            unidade_id=unidade.id,
        )
        admin_empresa.set_senha(request.form["admin_senha"])
        db.session.add(admin_empresa)
        db.session.flush()

        # licença inicial, pendente de pagamento
        licenca = Licenca(
            empresa_id=empresa.id,
            plano=request.form.get("plano", "mensal"),
            valor_negociado=Decimal(request.form["valor_negociado"].replace(",", ".")),
            status="pendente_pagamento",
            definido_por_id=current_user.id,
        )
        db.session.add(licenca)

        # Módulos escolhidos ANTES do primeiro pagamento (ver
        # app/models/modulo.py) — cada módulo opcional marcado no
        # formulário entra como "incluido_inicial", já com o valor
        # adicional negociado pra essa empresa (pode ser 0, se o admin
        # decidiu embutir no valor da licença em vez de destacar). Módulos
        # obrigatórios não precisam de linha aqui — já ficam liberados
        # sempre (ver modulo_liberado_para).
        for modulo in catalogo_ativo():
            if modulo.obrigatorio:
                continue
            campo = f"modulo_{modulo.id}"
            if not request.form.get(campo):
                continue
            valor_bruto = request.form.get(f"{campo}_valor", "").strip()
            valor = Decimal(valor_bruto.replace(",", ".")) if valor_bruto else Decimal("0")
            incluir_modulo_inicial(empresa, modulo, valor_adicional=valor, definido_por=current_user)

        registrar_log(current_user, "criou", "Empresa", empresa.id, empresa.nome)
        db.session.commit()
        flash(f"Empresa \"{empresa.nome}\" cadastrada, com unidade e admin criados. Licença pendente de pagamento.", "success")
        return redirect(url_for("plataforma.empresas"))

    return render_template("plataforma/empresa_form.html", catalogo=catalogo_ativo())


@plataforma_bp.route("/empresas/<int:empresa_id>")
@login_required
@apenas_admin_desenvolvedor
def detalhe_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)
    unidades = Unidade.query.filter_by(empresa_id=empresa.id).all()
    usuarios = Usuario.query.join(Unidade).filter(Unidade.empresa_id == empresa.id).order_by(Usuario.nome).all()
    modulos_ativos = (
        EmpresaModulo.query.filter_by(empresa_id=empresa.id)
        .filter(EmpresaModulo.status.in_(("incluido_inicial", "ativo")))
        .count()
    )
    pedidos_pendentes = EmpresaModulo.query.filter_by(empresa_id=empresa.id, status="solicitado").count()
    return render_template(
        "plataforma/empresa_detalhe.html", empresa=empresa, unidades=unidades, usuarios=usuarios,
        modulos_ativos=modulos_ativos, pedidos_pendentes=pedidos_pendentes,
    )


@plataforma_bp.route("/empresas/<int:empresa_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def editar_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)
    if request.method == "POST":
        empresa.nome = request.form["nome"]
        empresa.cnpj = request.form.get("cnpj")
        empresa.email_contato = request.form.get("email_contato")
        empresa.telefone = request.form.get("telefone")
        empresa.ativa = bool(request.form.get("ativa"))
        registrar_log(current_user, "editou", "Empresa", empresa.id, empresa.nome)
        db.session.commit()
        flash("Empresa atualizada.", "success")
        return redirect(url_for("plataforma.detalhe_empresa", empresa_id=empresa.id))
    return render_template("plataforma/empresa_form.html", empresa=empresa, catalogo=catalogo_ativo())


# ---------------------- Licenças ----------------------

@plataforma_bp.route("/empresas/<int:empresa_id>/licenca", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def licenca_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)
    licenca = empresa.licenca

    if request.method == "POST":
        valor = Decimal(request.form["valor_negociado"].replace(",", "."))
        plano = request.form.get("plano", "mensal")

        if licenca is None:
            licenca = Licenca(empresa_id=empresa.id)
            db.session.add(licenca)

        licenca.plano = plano
        licenca.valor_negociado = valor
        licenca.definido_por_id = current_user.id

        # ação manual do admin dev: pode também marcar como paga direto
        # (ex: pagamento combinado fora do sistema, boleto avulso etc)
        if request.form.get("marcar_paga_manualmente"):
            licenca.status = "ativa"
            licenca.data_inicio = date.today()
            licenca.data_fim = date.today() + timedelta(days=Licenca.DIAS_POR_PLANO[plano])

        registrar_log(current_user, "definiu_licenca", "Empresa", empresa.id,
                      f"{plano} R${valor}")
        db.session.commit()
        flash("Licença atualizada.", "success")
        return redirect(url_for("plataforma.detalhe_empresa", empresa_id=empresa.id))

    return render_template("plataforma/licenca_form.html", empresa=empresa, licenca=licenca)


# ---------------------- Preços padrão (cadastro público self-service) ----------------------
# Ver app/models/configuracao.py — só afeta o preço de TABELA mostrado em
# /cadastrar-empresa; o valor negociado de cada empresa já cadastrada
# (Licenca.valor_negociado) nunca muda sozinho quando isso é editado aqui.

@plataforma_bp.route("/planos", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def editar_planos():
    config = ConfiguracaoPlataforma.obter()

    if request.method == "POST":
        config.preco_padrao_mensal = Decimal(request.form["preco_padrao_mensal"].replace(",", "."))
        config.preco_padrao_trimestral = Decimal(request.form["preco_padrao_trimestral"].replace(",", "."))
        config.preco_padrao_anual = Decimal(request.form["preco_padrao_anual"].replace(",", "."))
        config.atualizado_por_id = current_user.id
        if config.id is None or db.session.get(ConfiguracaoPlataforma, 1) is None:
            db.session.add(config)
        registrar_log(current_user, "editou", "ConfiguracaoPlataforma", 1,
                      f"mensal R${config.preco_padrao_mensal} / trimestral R${config.preco_padrao_trimestral} / "
                      f"anual R${config.preco_padrao_anual}")
        db.session.commit()
        flash("Preços padrão atualizados — valem a partir do próximo cadastro público. "
              "Empresas já cadastradas não são afetadas.", "success")
        return redirect(url_for("plataforma.editar_planos"))

    return render_template("plataforma/planos_form.html", config=config)


# ---------------------- Catálogo de módulos ----------------------
# Ver app/models/modulo.py e app/utils/modulos.py. Cadastro do que a
# plataforma vende como módulo separado — nada aqui é visível para
# nenhuma empresa cliente (elas só veem o catálogo filtrado em
# /licenciamento/modulos, sem preço sugerido nem o quanto é "obrigatório").

@plataforma_bp.route("/modulos")
@login_required
@apenas_admin_desenvolvedor
def modulos_lista():
    itens = Modulo.query.order_by(Modulo.ativo.desc(), Modulo.ordem_exibicao, Modulo.nome).all()
    return render_template("plataforma/modulos_lista.html", itens=itens)


@plataforma_bp.route("/modulos/novo", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def novo_modulo():
    if request.method == "POST":
        chave = request.form.get("chave", "").strip().lower()
        if not chave:
            flash("Informe a chave do módulo (precisa bater com o nome do blueprint no código).", "danger")
            return redirect(url_for("plataforma.novo_modulo"))
        if Modulo.query.filter_by(chave=chave).first():
            flash(f"Já existe um módulo cadastrado com a chave \"{chave}\".", "danger")
            return redirect(url_for("plataforma.novo_modulo"))

        preco_bruto = request.form.get("preco_sugerido", "").strip()
        item = Modulo(
            chave=chave,
            nome=request.form["nome"].strip(),
            descricao=request.form.get("descricao") or None,
            preco_sugerido=Decimal(preco_bruto.replace(",", ".")) if preco_bruto else None,
            obrigatorio=bool(request.form.get("obrigatorio")),
            ativo=True,
            ordem_exibicao=int(request.form.get("ordem_exibicao") or 0),
        )
        db.session.add(item)
        registrar_log(current_user, "criou", "Modulo", None, item.chave)
        db.session.commit()
        flash(f"Módulo \"{item.nome}\" cadastrado.", "success")
        return redirect(url_for("plataforma.modulos_lista"))

    return render_template("plataforma/modulo_form.html", item=None)


@plataforma_bp.route("/modulos/<int:item_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def editar_modulo(item_id):
    item = db.get_or_404(Modulo, item_id)
    if request.method == "POST":
        # `chave` não é editável depois de criada — é o que liga esse
        # cadastro ao blueprint de verdade (ver docstring de Modulo);
        # mudar silenciosamente quebraria o bloqueio de acesso pra quem já
        # tinha o módulo liberado.
        item.nome = request.form["nome"].strip()
        item.descricao = request.form.get("descricao") or None
        preco_bruto = request.form.get("preco_sugerido", "").strip()
        item.preco_sugerido = Decimal(preco_bruto.replace(",", ".")) if preco_bruto else None
        item.obrigatorio = bool(request.form.get("obrigatorio"))
        item.ordem_exibicao = int(request.form.get("ordem_exibicao") or 0)
        registrar_log(current_user, "editou", "Modulo", item.id, item.chave)
        db.session.commit()
        flash("Módulo atualizado.", "success")
        return redirect(url_for("plataforma.modulos_lista"))

    return render_template("plataforma/modulo_form.html", item=item)


@plataforma_bp.route("/modulos/<int:item_id>/alternar-ativo", methods=["POST"])
@login_required
@apenas_admin_desenvolvedor
def alternar_modulo_ativo(item_id):
    item = db.get_or_404(Modulo, item_id)
    item.ativo = not item.ativo
    registrar_log(current_user, "ativou" if item.ativo else "desativou", "Modulo", item.id, item.chave)
    db.session.commit()
    flash(f"Módulo {'reativado no catálogo' if item.ativo else 'retirado do catálogo'} — "
          f"empresas que já tinham esse módulo liberado não são afetadas.", "info")
    return redirect(url_for("plataforma.modulos_lista"))


# ---------------------- Módulos por empresa ----------------------

@plataforma_bp.route("/empresas/<int:empresa_id>/modulos", methods=["GET", "POST"])
@login_required
@apenas_admin_desenvolvedor
def modulos_empresa(empresa_id):
    empresa = db.get_or_404(Empresa, empresa_id)

    if request.method == "POST":
        acao = request.form.get("acao")
        modulo_id = int(request.form["modulo_id"])
        modulo = db.get_or_404(Modulo, modulo_id)

        assoc = EmpresaModulo.query.filter_by(empresa_id=empresa.id, modulo_id=modulo.id).first()

        if acao == "ativar":
            # cobre tanto aprovar um pedido ("solicitado" -> "ativo") quanto
            # ligar um módulo direto sem a empresa ter pedido nada antes.
            valor_bruto = request.form.get("valor_adicional", "").strip()
            valor = Decimal(valor_bruto.replace(",", ".")) if valor_bruto else Decimal("0")
            if assoc is None:
                assoc = EmpresaModulo(empresa_id=empresa.id, modulo_id=modulo.id)
                db.session.add(assoc)
            aprovar_modulo(assoc, valor_adicional=valor, definido_por=current_user)
            registrar_log(current_user, "ativou_modulo", "Empresa", empresa.id, f"{modulo.chave} R${valor}")
            db.session.commit()
            flash(f"Módulo \"{modulo.nome}\" ativado para \"{empresa.nome}\".", "success")

        elif acao == "cancelar" and assoc is not None:
            cancelar_modulo(assoc, definido_por=current_user, observacao=request.form.get("observacao") or None)
            registrar_log(current_user, "cancelou_modulo", "Empresa", empresa.id, modulo.chave)
            db.session.commit()
            flash(f"Módulo \"{modulo.nome}\" removido de \"{empresa.nome}\".", "info")

        return redirect(url_for("plataforma.modulos_empresa", empresa_id=empresa.id))

    catalogo = catalogo_ativo()
    associacoes = {a.modulo_id: a for a in EmpresaModulo.query.filter_by(empresa_id=empresa.id).all()}
    linhas = [dict(modulo=m, associacao=associacoes.get(m.id)) for m in catalogo]

    return render_template("plataforma/modulos_empresa.html", empresa=empresa, linhas=linhas)
