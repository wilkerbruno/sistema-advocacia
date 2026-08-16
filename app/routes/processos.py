import os
import uuid
from datetime import datetime, date
from decimal import Decimal
from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, current_app, send_from_directory, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.extensions import db
from app.models import Processo, Cliente, Unidade, Usuario, Andamento, Prazo, Audiencia, Documento, Movimentacao, AnaliseProcessoIA
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo
from app.utils.notificacoes import registrar_log, notificar
from app.utils import tribunais_datajud, ia_local
from app.utils.analise_processo_ia import gerar_analise

processos_bp = Blueprint("processos", __name__)


def _arquivo_permitido(nome):
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def _parse_data(valor):
    """Converte string 'YYYY-MM-DD' vinda de <input type=date> em date, ou None."""
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


@processos_bp.route("/")
@login_required
def listar():
    query = aplicar_escopo_unidade(Processo.query, Processo)

    status = request.args.get("status")
    area = request.args.get("area")
    termo = request.args.get("q", "").strip()
    unidade_filtro = request.args.get("unidade_id")

    if status:
        query = query.filter(Processo.status == status)
    if area:
        query = query.filter(Processo.area_direito == area)
    if termo:
        like = f"%{termo}%"
        query = query.join(Cliente).filter(
            db.or_(Processo.numero_processo.ilike(like),
                   Processo.numero_interno.ilike(like),
                   Cliente.nome.ilike(like))
        )
    if current_user.is_admin and unidade_filtro:
        query = query.filter(Processo.unidade_id == int(unidade_filtro))

    processos = query.order_by(Processo.atualizado_em.desc()).all()
    unidades = unidades_do_escopo() if current_user.is_admin else None
    return render_template("processos/listar.html", processos=processos, unidades=unidades,
                            status=status, area=area, termo=termo)


@processos_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    unidades = aplicar_escopo_unidade(Unidade.query, Unidade, "id").filter_by(ativa=True).all() if current_user.is_admin else None
    minha_unidade_id = None if current_user.is_admin else current_user.unidade_id
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        processo = Processo(
            numero_processo=request.form.get("numero_processo"),
            numero_interno=request.form.get("numero_interno"),
            area_direito=request.form["area_direito"],
            tipo_acao=request.form.get("tipo_acao"),
            fase=request.form.get("fase"),
            instancia=request.form.get("instancia"),
            comarca=request.form.get("comarca"),
            vara=request.form.get("vara"),
            tribunal=request.form.get("tribunal"),
            tribunal_datajud=request.form.get("tribunal_datajud") or None,
            polo_cliente=request.form.get("polo_cliente"),
            parte_contraria=request.form.get("parte_contraria"),
            advogado_contrario=request.form.get("advogado_contrario"),
            valor_causa=request.form.get("valor_causa") or None,
            data_distribuicao=_parse_data(request.form.get("data_distribuicao")),
            descricao=request.form.get("descricao"),
            segredo_justica=bool(request.form.get("segredo_justica")),
            cliente_id=request.form["cliente_id"],
            responsavel_id=request.form.get("responsavel_id") or current_user.id,
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
        )
        db.session.add(processo)
        db.session.flush()

        db.session.add(Andamento(
            processo_id=processo.id, tipo="movimentacao",
            descricao="Processo cadastrado no sistema.",
            registrado_por_id=current_user.id,
        ))

        registrar_log(current_user, "criou", "Processo", processo.id,
                       processo.numero_processo or processo.numero_interno)
        db.session.commit()
        flash("Processo cadastrado com sucesso.", "success")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    responsaveis = Usuario.query.filter_by(
        unidade_id=minha_unidade_id, ativo=True
    ).all() if minha_unidade_id else usuarios_do_escopo()

    return render_template("processos/form.html", processo=None, unidades=unidades,
                            clientes=clientes, responsaveis=responsaveis,
                            tribunais_datajud=tribunais_datajud.TODOS)


@processos_bp.route("/<int:processo_id>")
@login_required
def detalhe(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)
    from app.models import RegraProximaAcao
    regras_ativas = RegraProximaAcao.query.filter_by(ativo=True).order_by(RegraProximaAcao.ato_capturado).all()
    analises_ia = AnaliseProcessoIA.query.filter_by(processo_id=processo.id) \
        .order_by(AnaliseProcessoIA.criado_em.desc()).all()
    return render_template("processos/detalhe.html", processo=processo, hoje=datetime.utcnow().date(),
                            regras_ativas=regras_ativas, analises_ia=analises_ia,
                            ia_configurada=ia_local.modelo_disponivel())


@processos_bp.route("/<int:processo_id>/editar", methods=["GET", "POST"])
@login_required
def editar(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)
    unidades = unidades_do_escopo() if current_user.is_admin else None
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).order_by(Cliente.nome).all()
    responsaveis = Usuario.query.filter_by(unidade_id=processo.unidade_id, ativo=True).all()

    if request.method == "POST":
        processo.numero_processo = request.form.get("numero_processo")
        processo.numero_interno = request.form.get("numero_interno")
        processo.area_direito = request.form["area_direito"]
        processo.tipo_acao = request.form.get("tipo_acao")
        processo.fase = request.form.get("fase")
        processo.instancia = request.form.get("instancia")
        processo.comarca = request.form.get("comarca")
        processo.vara = request.form.get("vara")
        processo.tribunal = request.form.get("tribunal")
        processo.tribunal_datajud = request.form.get("tribunal_datajud") or None
        processo.status = request.form.get("status", processo.status)
        processo.polo_cliente = request.form.get("polo_cliente")
        processo.parte_contraria = request.form.get("parte_contraria")
        processo.advogado_contrario = request.form.get("advogado_contrario")
        processo.valor_causa = request.form.get("valor_causa") or None
        processo.data_distribuicao = _parse_data(request.form.get("data_distribuicao"))
        processo.descricao = request.form.get("descricao")
        processo.segredo_justica = bool(request.form.get("segredo_justica"))
        processo.cliente_id = request.form["cliente_id"]
        processo.responsavel_id = request.form.get("responsavel_id") or processo.responsavel_id
        if current_user.is_admin and request.form.get("unidade_id"):
            processo.unidade_id = int(request.form["unidade_id"])

        # Risco / contingenciamento / desfecho (BI e paridade — só chega aqui
        # quando o processo já existe, o form de criação não expõe esses campos)
        processo.classificacao_risco = request.form.get("classificacao_risco") or None
        classificacao_contingencia = request.form.get("classificacao_contingencia") or None
        if classificacao_contingencia and classificacao_contingencia not in Processo.CLASSIFICACOES_CONTINGENCIA:
            classificacao_contingencia = None
        processo.classificacao_contingencia = classificacao_contingencia
        percentual = request.form.get("percentual_provisionamento")
        processo.percentual_provisionamento = Decimal(percentual.replace(",", ".")) if percentual else None

        desfecho = request.form.get("desfecho") or None
        if desfecho and desfecho not in Processo.DESFECHOS:
            desfecho = None
        processo.desfecho = desfecho
        processo.data_encerramento = _parse_data(request.form.get("data_encerramento"))
        processo.observacao_desfecho = request.form.get("observacao_desfecho") or None
        # marcação honesta: se o usuário encerrou o processo mas esqueceu a data,
        # assume hoje em vez de deixar a métrica de duração sem dado
        if processo.status == "encerrado" and processo.desfecho and not processo.data_encerramento:
            processo.data_encerramento = date.today()

        registrar_log(current_user, "editou", "Processo", processo.id, processo.numero_processo)
        db.session.commit()
        flash("Processo atualizado com sucesso.", "success")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    return render_template("processos/form.html", processo=processo, unidades=unidades,
                            clientes=clientes, responsaveis=responsaveis,
                            tribunais_datajud=tribunais_datajud.TODOS)


# ---------- Andamentos (linha do tempo) ----------

@processos_bp.route("/<int:processo_id>/andamentos", methods=["POST"])
@login_required
def add_andamento(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    andamento = Andamento(
        processo_id=processo.id,
        tipo=request.form.get("tipo", "movimentacao"),
        descricao=request.form["descricao"],
        registrado_por_id=current_user.id,
    )
    db.session.add(andamento)
    processo.atualizado_em = datetime.utcnow()
    registrar_log(current_user, "add_andamento", "Processo", processo.id)
    db.session.commit()
    flash("Andamento registrado.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


# ---------- Prazos ----------

@processos_bp.route("/<int:processo_id>/prazos", methods=["POST"])
@login_required
def add_prazo(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    responsavel_id = request.form.get("responsavel_id") or current_user.id
    prazo = Prazo(
        processo_id=processo.id,
        descricao=request.form["descricao"],
        data_vencimento=_parse_data(request.form["data_vencimento"]),
        prioridade=request.form.get("prioridade", "normal"),
        observacoes=request.form.get("observacoes"),
        responsavel_id=responsavel_id,
    )
    db.session.add(prazo)
    db.session.flush()
    notificar(responsavel_id, "Novo prazo atribuído",
              f"{prazo.descricao} — vence em {prazo.data_vencimento.strftime('%d/%m/%Y')}",
              tipo="prazo", link=url_for("processos.detalhe", processo_id=processo.id))
    registrar_log(current_user, "add_prazo", "Processo", processo.id, prazo.descricao)
    db.session.commit()
    flash("Prazo adicionado.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@processos_bp.route("/prazos/<int:prazo_id>/status", methods=["POST"])
@login_required
def atualizar_status_prazo(prazo_id):
    """
    Governança central do projeto (seção 7.2 do briefing): "o prazo só
    fecha como cumprido quando o sistema encontra a movimentação de
    protocolo correspondente no andamento do processo, ou quando é
    anexado o comprovante de protocolo. Marcar como 'feito' no botão não
    fecha o prazo sozinho."

    Por isso este endpoint aceita qualquer status EXCETO "cumprido" —
    fechar como cumprido só é permitido pela rota dedicada
    `cumprir_prazo_com_evidencia`, que exige evidência.
    """
    prazo = db.get_or_404(Prazo, prazo_id)
    checar_acesso_unidade_ou_403(prazo.processo.unidade_id)
    novo_status = request.form.get("status")

    if novo_status == "cumprido":
        flash("Prazo não pode ser marcado como cumprido sem evidência. "
              "Anexe o comprovante de protocolo ou vincule a movimentação correspondente.", "warning")
        return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))

    if novo_status in Prazo.STATUS:
        prazo.status = novo_status
        registrar_log(current_user, "status_prazo", "Prazo", prazo.id, novo_status)
        db.session.commit()
        flash("Status do prazo atualizado.", "info")
    return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))


@processos_bp.route("/prazos/<int:prazo_id>/cumprir-com-evidencia", methods=["POST"])
@login_required
def cumprir_prazo_com_evidencia(prazo_id):
    """
    Único caminho para fechar um prazo como cumprido (seção 7.2):
    exige `evidencia_movimentacao_id` (uma Movimentacao já capturada/
    registrada para o processo) OU `evidencia_documento_id` (um Documento
    já anexado ao processo, ex: comprovante de protocolo).
    """
    prazo = db.get_or_404(Prazo, prazo_id)
    checar_acesso_unidade_ou_403(prazo.processo.unidade_id)

    evidencia_mov_id = request.form.get("evidencia_movimentacao_id") or None
    evidencia_doc_id = request.form.get("evidencia_documento_id") or None

    if not evidencia_mov_id and not evidencia_doc_id:
        flash("Selecione uma movimentação capturada ou um documento comprobatório para fechar o prazo.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))

    if evidencia_mov_id:
        mov = db.session.get(Movimentacao, int(evidencia_mov_id))
        if not mov or mov.processo_id != prazo.processo_id:
            flash("Movimentação de evidência inválida para este processo.", "danger")
            return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))
        prazo.evidencia_movimentacao_id = mov.id

    if evidencia_doc_id:
        doc = db.get_or_404(Documento, int(evidencia_doc_id))
        if doc.processo_id != prazo.processo_id:
            flash("Documento de evidência inválido para este processo.", "danger")
            return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))
        prazo.evidencia_documento_id = doc.id

    prazo.status = "cumprido"
    prazo.cumprido_em = datetime.utcnow()
    registrar_log(current_user, "cumprir_prazo_com_evidencia", "Prazo", prazo.id,
                  f"mov={evidencia_mov_id} doc={evidencia_doc_id}")
    db.session.commit()
    flash("Prazo fechado como cumprido, com evidência registrada.", "success")
    return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))


# ---------- Audiências ----------

@processos_bp.route("/<int:processo_id>/audiencias", methods=["POST"])
@login_required
def add_audiencia(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    data_str = f"{request.form['data']} {request.form['hora']}"
    audiencia = Audiencia(
        processo_id=processo.id,
        tipo=request.form.get("tipo"),
        data_hora=datetime.strptime(data_str, "%Y-%m-%d %H:%M"),
        local=request.form.get("local"),
        modalidade=request.form.get("modalidade", "presencial"),
        link_virtual=request.form.get("link_virtual"),
        observacoes=request.form.get("observacoes"),
        responsavel_id=request.form.get("responsavel_id") or current_user.id,
    )
    db.session.add(audiencia)
    registrar_log(current_user, "add_audiencia", "Processo", processo.id)
    db.session.commit()
    flash("Audiência agendada.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@processos_bp.route("/audiencias/<int:audiencia_id>/status", methods=["POST"])
@login_required
def atualizar_status_audiencia(audiencia_id):
    audiencia = db.get_or_404(Audiencia, audiencia_id)
    checar_acesso_unidade_ou_403(audiencia.processo.unidade_id)
    novo_status = request.form.get("status")
    audiencia.status = novo_status
    db.session.commit()
    flash("Status da audiência atualizado.", "info")
    return redirect(url_for("processos.detalhe", processo_id=audiencia.processo_id))


# ---------- Documentos ----------

@processos_bp.route("/<int:processo_id>/documentos", methods=["POST"])
@login_required
def add_documento(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    arquivo = request.files.get("arquivo")
    if not arquivo or arquivo.filename == "":
        flash("Selecione um arquivo.", "warning")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    if not _arquivo_permitido(arquivo.filename):
        flash("Tipo de arquivo não permitido.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    nome_original = secure_filename(arquivo.filename)
    ext = nome_original.rsplit(".", 1)[-1].lower()
    nome_salvo = f"{uuid.uuid4().hex}.{ext}"
    pasta_processo = os.path.join(current_app.config["UPLOAD_FOLDER"], str(processo.id))
    os.makedirs(pasta_processo, exist_ok=True)
    caminho_completo = os.path.join(pasta_processo, nome_salvo)
    arquivo.save(caminho_completo)

    doc = Documento(
        processo_id=processo.id,
        nome_original=nome_original,
        nome_arquivo=nome_salvo,
        categoria=request.form.get("categoria", "outros"),
        tamanho_kb=round(os.path.getsize(caminho_completo) / 1024),
        enviado_por_id=current_user.id,
    )
    db.session.add(doc)
    registrar_log(current_user, "upload_documento", "Processo", processo.id, nome_original)
    db.session.commit()
    flash("Documento enviado.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@processos_bp.route("/documentos/<int:documento_id>/baixar")
@login_required
def baixar_documento(documento_id):
    doc = db.get_or_404(Documento, documento_id)
    checar_acesso_unidade_ou_403(doc.processo.unidade_id)
    pasta_processo = os.path.join(current_app.config["UPLOAD_FOLDER"], str(doc.processo_id))
    return send_from_directory(pasta_processo, doc.nome_arquivo,
                                as_attachment=True, download_name=doc.nome_original)


@processos_bp.route("/documentos/<int:documento_id>/excluir", methods=["POST"])
@login_required
def excluir_documento(documento_id):
    doc = db.get_or_404(Documento, documento_id)
    checar_acesso_unidade_ou_403(doc.processo.unidade_id)
    processo_id = doc.processo_id
    caminho = os.path.join(current_app.config["UPLOAD_FOLDER"], str(processo_id), doc.nome_arquivo)
    if os.path.exists(caminho):
        os.remove(caminho)
    db.session.delete(doc)
    registrar_log(current_user, "excluiu_documento", "Processo", processo_id, doc.nome_original)
    db.session.commit()
    flash("Documento removido.", "info")
    return redirect(url_for("processos.detalhe", processo_id=processo_id))


# ---------- Análise com Agente de IA (resumo dos autos / rascunho de petição) ----------
# Ver app/utils/analise_processo_ia.py para o motor (mesmo modelo local
# gratuito do Agente de IA de portfólio) e app/models/agente_ia.py::
# AnaliseProcessoIA para o histórico persistido. Sempre lembrar: é rascunho
# para revisão humana, nunca texto pronto para uso sem conferência.

@processos_bp.route("/<int:processo_id>/analise-ia", methods=["POST"])
@login_required
def gerar_analise_ia(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    tipo = request.form.get("tipo")
    instrucao = request.form.get("instrucao", "").strip()

    if not ia_local.modelo_disponivel():
        flash("Agente de IA local indisponível neste servidor (modelo não baixado/configurado — "
              "ver PENDENCIAS.md).", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        resultado, truncado = gerar_analise(processo, tipo, instrucao)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))
    except ia_local.ModeloIndisponivelError as e:
        flash(f"Agente de IA indisponível: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))
    except Exception as e:  # nunca deixa a tela do processo travada por erro do modelo local
        flash(f"Não foi possível gerar a análise agora: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    analise = AnaliseProcessoIA(
        processo_id=processo.id, solicitado_por_id=current_user.id, tipo=tipo,
        instrucao=instrucao or None, resultado=resultado, digest_truncado=truncado,
    )
    db.session.add(analise)
    registrar_log(current_user, "gerou_analise_ia", "Processo", processo.id, tipo)
    db.session.commit()
    flash("Análise gerada — revise com atenção antes de usar; é sempre um rascunho para conferência humana.",
          "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@processos_bp.route("/analises-ia/<int:analise_id>/excluir", methods=["POST"])
@login_required
def excluir_analise_ia(analise_id):
    analise = db.get_or_404(AnaliseProcessoIA, analise_id)
    checar_acesso_unidade_ou_403(analise.processo.unidade_id)
    processo_id = analise.processo_id
    db.session.delete(analise)
    registrar_log(current_user, "excluiu_analise_ia", "Processo", processo_id, analise.tipo)
    db.session.commit()
    flash("Análise removida.", "info")
    return redirect(url_for("processos.detalhe", processo_id=processo_id))
