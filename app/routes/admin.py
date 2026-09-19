from flask import Blueprint, render_template, request, redirect, url_for, flash
from datetime import datetime, timedelta
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.models import Unidade, Usuario, Processo, Cliente, Lancamento, LogAtividade, Empresa
from app.utils.acesso import apenas_admin, login_papel_requerido, checar_acesso_unidade_ou_403, usuarios_do_escopo
from app.utils.notificacoes import registrar_log
from app.utils.rede import resumir_user_agent
from app.utils.financeiro_util import filtro_conta_terceiros
from app.utils.desligamento import itens_em_aberto, tem_itens_em_aberto, reatribuir_itens_em_aberto
from app.utils import totp as totp_utils
from decimal import Decimal, InvalidOperation

admin_bp = Blueprint("admin", __name__)


# ---------------------- Unidades (somente admin) ----------------------

def _contexto_unidades():
    """
    Reúne o contexto da listagem de unidades num dict — extraído de
    `unidades()` pra ser reaproveitado também pelo hub `admin.
    minha_empresa()` (menu simplificado: "Minha empresa" virou uma tela só
    com abas, mesma ideia de app/routes/rotina.py e app/routes/conta.py::
    hub). "+ Nova unidade" e "Editar" continuam abrindo `admin.
    nova_unidade`/`admin.editar_unidade` — telas de formulário completas,
    de fora do hub, mesmo padrão de "Nova tarefa" dentro do hub "Rotina".
    """
    query = Unidade.query
    if not current_user.is_admin_desenvolvedor:
        query = query.filter_by(empresa_id=current_user.empresa_id_atual)
    lista = query.order_by(Unidade.nome).all()
    return dict(unidades=lista)


@admin_bp.route("/unidades")
@login_required
@apenas_admin
def unidades():
    return render_template("admin/unidades.html", **_contexto_unidades())


@admin_bp.route("/unidades/nova", methods=["GET", "POST"])
@login_required
@apenas_admin
def nova_unidade():
    empresas = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all() if current_user.is_admin_desenvolvedor else None
    if request.method == "POST":
        empresa_id = int(request.form["empresa_id"]) if current_user.is_admin_desenvolvedor else current_user.empresa_id_atual
        unidade = Unidade(
            empresa_id=empresa_id,
            nome=request.form["nome"],
            codigo=request.form["codigo"].upper(),
            cidade=request.form.get("cidade"),
            estado=request.form.get("estado"),
            endereco=request.form.get("endereco"),
            telefone=request.form.get("telefone"),
            email=request.form.get("email"),
            responsavel=request.form.get("responsavel"),
        )
        db.session.add(unidade)
        db.session.flush()
        registrar_log(current_user, "criou", "Unidade", unidade.id, unidade.nome)
        db.session.commit()
        flash("Unidade cadastrada com sucesso.", "success")
        return redirect(url_for("admin.unidades"))
    return render_template("admin/unidade_form.html", unidade=None, empresas=empresas)


@admin_bp.route("/unidades/<int:unidade_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_unidade(unidade_id):
    unidade = db.get_or_404(Unidade, unidade_id)
    checar_acesso_unidade_ou_403(unidade.id)
    empresas = Empresa.query.filter_by(ativa=True).order_by(Empresa.nome).all() if current_user.is_admin_desenvolvedor else None
    if request.method == "POST":
        unidade.nome = request.form["nome"]
        unidade.codigo = request.form["codigo"].upper()
        unidade.cidade = request.form.get("cidade")
        unidade.estado = request.form.get("estado")
        unidade.endereco = request.form.get("endereco")
        unidade.telefone = request.form.get("telefone")
        unidade.email = request.form.get("email")
        unidade.responsavel = request.form.get("responsavel")
        unidade.ativa = bool(request.form.get("ativa"))
        if current_user.is_admin_desenvolvedor:
            unidade.empresa_id = int(request.form["empresa_id"])
        registrar_log(current_user, "editou", "Unidade", unidade.id, unidade.nome)
        db.session.commit()
        flash("Unidade atualizada com sucesso.", "success")
        return redirect(url_for("admin.unidades"))
    return render_template("admin/unidade_form.html", unidade=unidade, empresas=empresas)


# ---------------------- Usuários ----------------------

def _contexto_usuarios():
    """
    Mesma extração de `_contexto_unidades()` (ver acima) — pra reaproveitar
    em `admin.minha_empresa()` sem duplicar a query/regra de negócio. Único
    item de "Minha empresa" visível pra GESTOR também (não só admin — ver
    `login_papel_requerido` abaixo), então é o único tab que aparece pra
    todo mundo que enxerga o hub.
    """
    query = Usuario.query.join(Unidade)
    if current_user.is_admin_desenvolvedor:
        pass  # vê todos, de todas as empresas
    elif current_user.is_admin:
        query = query.filter(Unidade.empresa_id == current_user.empresa_id_atual)
    else:
        query = query.filter(Usuario.unidade_id == current_user.unidade_id)
    lista = query.order_by(Usuario.nome).all()

    if current_user.is_admin_desenvolvedor:
        unidades = Unidade.query.filter_by(ativa=True).all()
    else:
        unidades = Unidade.query.filter_by(ativa=True, empresa_id=current_user.empresa_id_atual).all()
    return dict(usuarios=lista, unidades=unidades)


@admin_bp.route("/usuarios")
@login_required
@login_papel_requerido("admin", "gestor")
def usuarios():
    return render_template("admin/usuarios.html", **_contexto_usuarios())


@admin_bp.route("/usuarios/novo", methods=["GET", "POST"])
@login_required
@login_papel_requerido("admin", "gestor")
def novo_usuario():
    if current_user.is_admin_desenvolvedor:
        unidades = Unidade.query.filter_by(ativa=True).all()
    elif current_user.is_admin:
        unidades = Unidade.query.filter_by(ativa=True, empresa_id=current_user.empresa_id_atual).all()
    else:
        unidades = Unidade.query.filter_by(id=current_user.unidade_id).all()

    if request.method == "POST":
        papel = request.form.get("papel", "funcionario")
        unidade_id = request.form.get("unidade_id") or None

        # gestor só pode criar usuários da própria unidade e nunca cria admin
        if not current_user.is_admin:
            papel = "funcionario" if papel not in ("advogado", "funcionario") else papel
            unidade_id = current_user.unidade_id
        elif not unidade_id:
            flash("Selecione a unidade do usuário.", "danger")
            return render_template("admin/usuario_form.html", usuario=None, unidades=unidades)

        # empresa admin (não-dev) só pode atribuir unidade da própria empresa,
        # mesmo manipulando o formulário
        checar_acesso_unidade_ou_403(int(unidade_id))

        if Usuario.query.filter_by(email=request.form["email"].strip().lower()).first():
            flash("Já existe um usuário com este e-mail.", "danger")
            return render_template("admin/usuario_form.html", usuario=None, unidades=unidades)

        usuario = Usuario(
            nome=request.form["nome"],
            email=request.form["email"].strip().lower(),
            papel=papel,
            oab=request.form.get("oab"),
            telefone=request.form.get("telefone"),
            whatsapp=request.form.get("whatsapp"),
            unidade_id=unidade_id,
            acesso_financeiro=bool(request.form.get("acesso_financeiro")),
        )
        usuario.set_senha(request.form["senha"])
        db.session.add(usuario)
        db.session.flush()
        registrar_log(current_user, "criou", "Usuario", usuario.id, usuario.email)
        db.session.commit()
        flash("Usuário cadastrado com sucesso.", "success")
        return redirect(url_for("admin.usuarios"))

    return render_template("admin/usuario_form.html", usuario=None, unidades=unidades)


@admin_bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@login_required
@login_papel_requerido("admin", "gestor")
def editar_usuario(usuario_id):
    usuario = db.get_or_404(Usuario, usuario_id)

    # nunca deixa uma empresa cliente enxergar/editar um admin desenvolvedor
    if usuario.is_admin_desenvolvedor and not current_user.is_admin_desenvolvedor:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("admin.usuarios"))

    if not current_user.is_admin_desenvolvedor:
        if current_user.is_admin:
            if usuario.empresa_id_atual != current_user.empresa_id_atual:
                flash("Você não pode editar usuários de outra empresa.", "danger")
                return redirect(url_for("admin.usuarios"))
        elif usuario.unidade_id != current_user.unidade_id:
            flash("Você não pode editar usuários de outra unidade.", "danger")
            return redirect(url_for("admin.usuarios"))

    if current_user.is_admin_desenvolvedor:
        unidades = Unidade.query.filter_by(ativa=True).all()
    elif current_user.is_admin:
        unidades = Unidade.query.filter_by(ativa=True, empresa_id=current_user.empresa_id_atual).all()
    else:
        unidades = Unidade.query.filter_by(id=current_user.unidade_id).all()

    if request.method == "POST":
        usuario.nome = request.form["nome"]
        usuario.oab = request.form.get("oab")
        usuario.telefone = request.form.get("telefone")
        usuario.whatsapp = request.form.get("whatsapp")
        usuario.acesso_financeiro = bool(request.form.get("acesso_financeiro"))

        # Desligamento por aqui (desmarcar "Usuário ativo") só é permitido
        # direto quando o usuário não tem NENHUM item em aberto sob a
        # responsabilidade dele (ver PENDENCIAS.md, seção -46) — senão
        # ficaria processo/prazo/audiência/tarefa/compromisso "órfão", sem
        # ninguém pra ser notificado. Reativar (False -> True) continua
        # direto, sem nenhuma checagem — não implica reatribuir nada.
        ativo_solicitado = bool(request.form.get("ativo"))
        desligamento_bloqueado = False
        if usuario.ativo and not ativo_solicitado:
            if tem_itens_em_aberto(usuario.id):
                desligamento_bloqueado = True
            else:
                usuario.ativo = False
        else:
            usuario.ativo = ativo_solicitado

        if current_user.is_admin:
            usuario.papel = request.form.get("papel", usuario.papel)
            unidade_id = request.form.get("unidade_id") or None
            if unidade_id:
                checar_acesso_unidade_ou_403(int(unidade_id))
                usuario.unidade_id = unidade_id

        nova_senha = request.form.get("senha")
        if nova_senha:
            usuario.set_senha(nova_senha)

        # Resetar autenticador (2FA — PENDENCIAS.md, seção -104): a única
        # forma de destravar quem perdeu o celular/app autenticador e ficou
        # sem conseguir logar sozinho, já que o próprio usuário não
        # consegue entrar pra fazer isso na própria tela (ver
        # conta.reconfigurar_totp, que exige a senha ATUAL — inútil pra
        # quem só perdeu o segundo fator). Limpa o autenticador confirmado
        # (se houver); no próximo login, o usuário cai de novo no fluxo de
        # "ainda não configurado" e escaneia um QR novo.
        if request.form.get("resetar_totp") and totp_utils.totp_disponivel():
            totp_utils.resetar(usuario)
            registrar_log(current_user, "resetou_autenticador_de_outro_usuario", "Usuario", usuario.id,
                          usuario.email)

        registrar_log(current_user, "editou", "Usuario", usuario.id, usuario.email)
        db.session.commit()

        if desligamento_bloqueado:
            flash(
                "Os demais dados foram salvos, mas este usuário tem processo/prazo/audiência/tarefa/"
                "compromisso em aberto sob a responsabilidade dele — para desativá-lo, use \"Desligar "
                "usuário\" abaixo, que reatribui esses itens antes de desativar.", "warning",
            )
            return redirect(url_for("admin.editar_usuario", usuario_id=usuario.id))

        flash("Usuário atualizado com sucesso.", "success")
        return redirect(url_for("admin.usuarios"))

    pendencias = itens_em_aberto(usuario.id) if usuario.ativo else None
    return render_template("admin/usuario_form.html", usuario=usuario, unidades=unidades, pendencias=pendencias)


@admin_bp.route("/usuarios/<int:usuario_id>/desligar", methods=["GET", "POST"])
@login_required
@login_papel_requerido("admin", "gestor")
def desligar_usuario(usuario_id):
    """
    Tela dedicada de desligamento com reatribuição (PENDENCIAS.md, seção
    -46) — separada do formulário geral de edição de propósito: exige um
    passo explícito (escolher o substituto + marcar ciência) antes de
    mexer em qualquer processo/prazo/audiência/tarefa/compromisso, nunca
    reatribui nada sozinho.
    """
    usuario = db.get_or_404(Usuario, usuario_id)

    if usuario.is_admin_desenvolvedor and not current_user.is_admin_desenvolvedor:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("admin.usuarios"))

    if not current_user.is_admin_desenvolvedor:
        if current_user.is_admin:
            if usuario.empresa_id_atual != current_user.empresa_id_atual:
                flash("Você não pode desligar usuários de outra empresa.", "danger")
                return redirect(url_for("admin.usuarios"))
        elif usuario.unidade_id != current_user.unidade_id:
            flash("Você não pode desligar usuários de outra unidade.", "danger")
            return redirect(url_for("admin.usuarios"))

    if usuario.id == current_user.id:
        flash("Você não pode desligar o próprio usuário por aqui.", "danger")
        return redirect(url_for("admin.editar_usuario", usuario_id=usuario.id))

    if not usuario.ativo:
        flash("Este usuário já está inativo.", "info")
        return redirect(url_for("admin.editar_usuario", usuario_id=usuario.id))

    pendencias = itens_em_aberto(usuario.id)
    total_pendencias = sum(pendencias.values())

    # Substitutos possíveis: qualquer usuário ativo dentro do MESMO escopo
    # de quem está desligando (gestor só vê a própria unidade; admin, a
    # própria empresa; admin desenvolvedor, todo mundo) — nunca o próprio
    # usuário sendo desligado.
    # Reforço deliberado além de usuarios_do_escopo(): mesmo pro admin
    # desenvolvedor (que enxerga usuário de QUALQUER empresa cliente),
    # o substituto só pode ser alguém da MESMA empresa de quem está
    # sendo desligado — sem isso, um processo/prazo de uma empresa
    # cliente poderia acabar reatribuído pra usuário de outra empresa
    # cliente, o que quebraria o isolamento multi-tenant (ver
    # app/utils/acesso.py).
    candidatos = [
        u for u in usuarios_do_escopo(apenas_ativos=True)
        if u.id != usuario.id and u.empresa_id_atual == usuario.empresa_id_atual
    ]

    if request.method == "POST":
        novo_responsavel_id = request.form.get("novo_responsavel_id")
        ciente = bool(request.form.get("ciente"))

        if total_pendencias > 0:
            if not novo_responsavel_id:
                flash("Escolha quem vai assumir os itens em aberto antes de desligar.", "danger")
                return render_template("admin/desligar_usuario.html", usuario=usuario,
                                        pendencias=pendencias, total_pendencias=total_pendencias,
                                        candidatos=candidatos)
            novo_responsavel = next((u for u in candidatos if u.id == int(novo_responsavel_id)), None)
            if not novo_responsavel:
                flash("Substituto inválido — escolha alguém da lista.", "danger")
                return render_template("admin/desligar_usuario.html", usuario=usuario,
                                        pendencias=pendencias, total_pendencias=total_pendencias,
                                        candidatos=candidatos)
            if not ciente:
                flash("Marque a caixa de ciência antes de confirmar o desligamento.", "danger")
                return render_template("admin/desligar_usuario.html", usuario=usuario,
                                        pendencias=pendencias, total_pendencias=total_pendencias,
                                        candidatos=candidatos)

            movidos = reatribuir_itens_em_aberto(usuario.id, novo_responsavel.id)
            registrar_log(
                current_user, "reatribuiu_itens_desligamento", "Usuario", usuario.id,
                f"de {usuario.email} para {novo_responsavel.email}: "
                f"{movidos['processos']} processo(s), {movidos['prazos']} prazo(s), "
                f"{movidos['audiencias']} audiência(s), {movidos['tarefas']} tarefa(s), "
                f"{movidos['compromissos']} compromisso(s)",
            )
        else:
            movidos = None

        usuario.ativo = False
        registrar_log(current_user, "desligou", "Usuario", usuario.id, usuario.email)
        db.session.commit()

        if movidos:
            flash(
                f"Usuário desligado. Reatribuído para {novo_responsavel.nome}: "
                f"{movidos['processos']} processo(s), {movidos['prazos']} prazo(s), "
                f"{movidos['audiencias']} audiência(s), {movidos['tarefas']} tarefa(s) e "
                f"{movidos['compromissos']} compromisso(s).", "success",
            )
        else:
            flash("Usuário desligado — não havia item em aberto sob a responsabilidade dele.", "success")
        return redirect(url_for("admin.usuarios"))

    return render_template("admin/desligar_usuario.html", usuario=usuario,
                            pendencias=pendencias, total_pendencias=total_pendencias,
                            candidatos=candidatos)


# ---------------------- Relatórios consolidados (somente admin) ----------------------

def _contexto_relatorios():
    """Mesma extração de `_contexto_unidades()` — pra reaproveitar em
    `admin.minha_empresa()`."""
    query_unidades = Unidade.query
    if not current_user.is_admin_desenvolvedor:
        query_unidades = query_unidades.filter_by(empresa_id=current_user.empresa_id_atual)

    por_unidade = []
    for u in query_unidades.order_by(Unidade.nome).all():
        por_unidade.append(dict(
            unidade=u,
            processos_ativos=Processo.query.filter_by(unidade_id=u.id, status="ativo").count(),
            processos_encerrados=Processo.query.filter_by(unidade_id=u.id, status="encerrado").count(),
            clientes=Cliente.query.filter_by(unidade_id=u.id).count(),
            # ⚠️ filtra fora conta_terceiros (ver PENDENCIAS.md, seção -39 e
            # -41): sem isso, depósito judicial/valor de repasse de cliente
            # inflaria a receita "própria" do escritório aqui, mesmo já
            # segregado corretamente na tela Financeiro.
            receita_pendente=db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).filter(
                Lancamento.unidade_id == u.id, Lancamento.natureza == "receita",
                Lancamento.status == "pendente", filtro_conta_terceiros(False)).scalar(),
            receita_recebida=db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).filter(
                Lancamento.unidade_id == u.id, Lancamento.natureza == "receita",
                Lancamento.status == "pago", filtro_conta_terceiros(False)).scalar(),
        ))

    query_processos = Processo.query
    if not current_user.is_admin_desenvolvedor:
        ids_unidades = [u.id for u in query_unidades.all()]
        query_processos = query_processos.filter(Processo.unidade_id.in_(ids_unidades))

    por_area = dict(
        query_processos.with_entities(Processo.area_direito, func.count(Processo.id)).group_by(Processo.area_direito).all()
    )

    # Segmentação financeira por área do direito (ver PENDENCIAS.md, seção
    # -41): só entra na conta o lançamento vinculado a um processo (área
    # vem do processo, não existe "área" de um lançamento solto) — receita
    # própria do escritório (conta_terceiros excluído, mesmo motivo do
    # bloco acima), separada em recebida vs. pendente, igual ao resto do
    # painel financeiro.
    query_lancamentos_area = db.session.query(
        Processo.area_direito,
        Lancamento.status,
        func.coalesce(func.sum(Lancamento.valor), 0),
    ).join(Lancamento, Lancamento.processo_id == Processo.id).filter(
        Lancamento.natureza == "receita", filtro_conta_terceiros(False),
    )
    if not current_user.is_admin_desenvolvedor:
        query_lancamentos_area = query_lancamentos_area.filter(Processo.unidade_id.in_(ids_unidades))
    query_lancamentos_area = query_lancamentos_area.group_by(Processo.area_direito, Lancamento.status)

    financeiro_por_area = {}
    for area, status, total in query_lancamentos_area.all():
        registro = financeiro_por_area.setdefault(area, {"recebido": 0, "pendente": 0})
        if status == "pago":
            registro["recebido"] += total
        elif status == "pendente":
            registro["pendente"] += total

    return dict(por_unidade=por_unidade, por_area=por_area, financeiro_por_area=financeiro_por_area)


@admin_bp.route("/relatorios")
@login_required
@apenas_admin
def relatorios():
    return render_template("admin/relatorios.html", **_contexto_relatorios())


def _contexto_auditoria(pagina=1, usuario_id=None, data_inicio=None, data_fim=None, ip_filtro="",
                         dispositivo_filtro=""):
    """
    Mesma extração de `_contexto_unidades()` — parâmetros explícitos (em
    vez de ler `request.args` direto aqui dentro) pelo mesmo motivo de
    `_contexto_tarefas()` em app/routes/tarefas.py: quem chama escolhe o
    nome do campo de formulário, sem risco de colisão com outro tab do
    hub "Minha empresa".
    """
    query = LogAtividade.query
    if not current_user.is_admin_desenvolvedor:
        # empresa admin só vê auditoria de usuários da própria empresa
        query = query.join(Usuario, LogAtividade.usuario_id == Usuario.id).join(
            Unidade, Usuario.unidade_id == Unidade.id
        ).filter(Unidade.empresa_id == current_user.empresa_id_atual)
    if usuario_id:
        query = query.filter(LogAtividade.usuario_id == usuario_id)
    if data_inicio:
        query = query.filter(LogAtividade.criado_em >= datetime.strptime(data_inicio, "%Y-%m-%d"))
    if data_fim:
        query = query.filter(LogAtividade.criado_em < datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1))
    if ip_filtro:
        query = query.filter(LogAtividade.ip.ilike(f"%{ip_filtro}%"))
    if dispositivo_filtro:
        query = query.filter(LogAtividade.dispositivo_id == dispositivo_filtro)

    logs = query.order_by(LogAtividade.criado_em.desc()).paginate(page=pagina, per_page=50)
    if current_user.is_admin_desenvolvedor:
        usuarios = Usuario.query.order_by(Usuario.nome).all()
    else:
        usuarios = Usuario.query.join(Unidade).filter(Unidade.empresa_id == current_user.empresa_id_atual).order_by(Usuario.nome).all()
    return dict(
        logs=logs, usuarios=usuarios,
        filtro_usuario_id=usuario_id, filtro_data_inicio=data_inicio, filtro_data_fim=data_fim,
        filtro_ip=ip_filtro, filtro_dispositivo=dispositivo_filtro, resumir_user_agent=resumir_user_agent,
    )


@admin_bp.route("/auditoria")
@login_required
@apenas_admin
def auditoria():
    return render_template("admin/auditoria.html", **_contexto_auditoria(
        pagina=request.args.get("pagina", 1, type=int),
        usuario_id=request.args.get("usuario_id", type=int),
        data_inicio=request.args.get("data_inicio"),
        data_fim=request.args.get("data_fim"),
        ip_filtro=request.args.get("ip", "").strip(),
        dispositivo_filtro=request.args.get("dispositivo_id", "").strip(),
    ))


# ---------------------- Alçada de aprovação (financeiro) ----------------------
# Ver app/utils/alcada.py e PENDENCIAS.md, seção -50. Cada empresa
# configura os próprios valores (nunca imposto pela plataforma) — mesmo
# padrão de app/routes/integracoes.py: acessível a qualquer admin,
# inclusive o admin desenvolvedor (configura a alçada da própria empresa
# dona da plataforma, se um dia fizer sentido usar o módulo Financeiro
# por lá também).

def _parse_decimal_alcada(valor):
    if valor is None or str(valor).strip() == "":
        return None
    try:
        return Decimal(str(valor).replace(",", "."))
    except InvalidOperation:
        return None


def _contexto_alcada():
    """Contexto (só de leitura) da tela de alçada, pro hub `admin.
    minha_empresa()` — o formulário em si continua enviando pra rota
    `admin.alcada_aprovacao` de sempre (única que sabe validar/gravar)."""
    return dict(empresa=current_user.empresa)


@admin_bp.route("/alcada-aprovacao", methods=["GET", "POST"])
@login_required
@apenas_admin
def alcada_aprovacao():
    empresa = current_user.empresa
    if empresa is None:
        flash("Seu usuário não está vinculado a uma empresa.", "warning")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        nivel1 = _parse_decimal_alcada(request.form.get("alcada_nivel1_valor"))
        nivel2 = _parse_decimal_alcada(request.form.get("alcada_nivel2_valor"))

        if nivel2 is not None and nivel1 is None:
            flash("Pra configurar o nível 2 (2 aprovações), o nível 1 precisa estar preenchido também.", "danger")
            return redirect(request.referrer or url_for("admin.alcada_aprovacao"))
        if nivel1 is not None and nivel2 is not None and nivel2 <= nivel1:
            flash("O valor do nível 2 precisa ser maior que o do nível 1.", "danger")
            return redirect(request.referrer or url_for("admin.alcada_aprovacao"))

        empresa.alcada_nivel1_valor = nivel1
        empresa.alcada_nivel2_valor = nivel2
        registrar_log(current_user, "editou_alcada_aprovacao", "Empresa", empresa.id,
                      f"nível 1: {nivel1}, nível 2: {nivel2}")
        db.session.commit()
        if nivel1 is None:
            flash("Alçada de aprovação desligada — nenhuma despesa vai precisar de aprovação.", "success")
        else:
            flash("Alçada de aprovação atualizada.", "success")
        return redirect(request.referrer or url_for("admin.alcada_aprovacao"))

    return render_template("admin/alcada_aprovacao.html", empresa=empresa)


# ---------------------- Hub "Minha empresa" ----------------------
# Menu simplificado (mesmo pedido explícito do usuário que já resultou nos
# hubs "Rotina" e "Minha conta" — ver app/routes/rotina.py e
# app/routes/conta.py::hub, e antes disso "Entrada de processos" em
# app/routes/governanca.py): reúne em abas de uma tela só os 8 itens que
# antes ficavam soltos num acordeão de 2º nível (ver PENDENCIAS.md, seção
# -110) dentro de "Minha empresa" — Unidades, Equipe, Relatórios,
# Auditoria, Alçada de aprovação, Minha licença, Módulos e Integrações.
# Cada aba reaproveita o contexto já extraído da tela antiga (helpers
# `_contexto_*` acima, mais os de app/routes/licenciamento.py e
# app/routes/integracoes.py, importados localmente — mesmo padrão de
# `conta.hub()` importando de agente_local.py; nenhum dos três módulos se
# importa entre si em outro lugar, então não há risco de import circular).
# Nenhuma consulta/regra de negócio foi duplicada, e as oito telas antigas
# (admin.unidades, admin.usuarios, admin.relatorios, admin.auditoria,
# admin.alcada_aprovacao, licenciamento.minha_licenca, licenciamento.
# modulos, integracoes.minhas_integracoes) continuam existindo e
# funcionando normalmente pra quem chegar direto por um link salvo — assim
# como "+ Nova unidade"/"+ Novo usuário"/"Editar"/"Gerenciar credenciais"
# continuam abrindo uma tela de formulário própria, fora do hub (mesmo
# padrão de "Nova tarefa" dentro do hub "Rotina") — só as LISTAS/painéis
# viraram abas.
#
# Visibilidade por ABA, não só da tela toda: "Equipe" aparece pra admin OU
# gestor (mesmo escopo de sempre — é o único item de "Minha empresa" que
# já não era exclusivo de admin); as outras sete exigem admin; "Minha
# licença" e "Módulos" ficam de fora também pro admin desenvolvedor
# (empresa dona da plataforma não tem licença — mesma regra de sempre em
# licenciamento.py). `abas_visiveis[0]` cobre tanto quem só vê "Equipe"
# quanto quem vê tudo, então a aba inicial nunca fica em branco.

@admin_bp.route("/minha-empresa")
@login_required
@login_papel_requerido("admin", "gestor")
def minha_empresa():
    from app.routes.licenciamento import _contexto_minha_licenca, _contexto_modulos
    from app.routes.integracoes import _contexto_minhas_integracoes

    is_admin = current_user.is_admin
    is_dev = current_user.is_admin_desenvolvedor

    contexto = {}
    abas_visiveis = []

    if is_admin:
        contexto["unidades_ctx"] = _contexto_unidades()
        abas_visiveis.append("unidades")

    contexto["usuarios_ctx"] = _contexto_usuarios()
    abas_visiveis.append("equipe")

    if is_admin:
        contexto["relatorios_ctx"] = _contexto_relatorios()
        abas_visiveis.append("relatorios")

        contexto["auditoria_ctx"] = _contexto_auditoria(
            pagina=request.args.get("pagina", 1, type=int),
            usuario_id=request.args.get("usuario_id", type=int),
            data_inicio=request.args.get("data_inicio"),
            data_fim=request.args.get("data_fim"),
            ip_filtro=request.args.get("ip", "").strip(),
            dispositivo_filtro=request.args.get("dispositivo_id", "").strip(),
        )
        abas_visiveis.append("auditoria")

        contexto["alcada_ctx"] = _contexto_alcada()
        abas_visiveis.append("alcada")

        if not is_dev:
            licenca_ctx = _contexto_minha_licenca()
            if licenca_ctx is not None:
                contexto["licenca_ctx"] = licenca_ctx
                abas_visiveis.append("licenca")

            modulos_ctx = _contexto_modulos()
            if modulos_ctx is not None:
                contexto["modulos_ctx"] = modulos_ctx
                abas_visiveis.append("modulos")

        integracoes_ctx = _contexto_minhas_integracoes()
        if integracoes_ctx is not None:
            contexto["integracoes_ctx"] = integracoes_ctx
            abas_visiveis.append("integracoes")

    aba_pedida = request.args.get("tab")
    aba_inicial = aba_pedida if aba_pedida in abas_visiveis else abas_visiveis[0]

    return render_template(
        "admin/minha_empresa_hub.html",
        aba_inicial=aba_inicial, abas_visiveis=abas_visiveis, is_admin=is_admin, is_dev=is_dev,
        **contexto,
    )
