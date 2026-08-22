import os
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, current_app, send_from_directory, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app.extensions import db
from sqlalchemy import func
from app.models import (
    Processo, Cliente, Unidade, Usuario, Andamento, Prazo, Audiencia, Documento,
    Movimentacao, AnaliseProcessoIA, LogCaptura, ProcessoAcessoRestrito, LogAtividade,
)
from app.utils.acesso import (
    aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403,
    unidades_do_escopo, usuarios_do_escopo, checar_acesso_processo_ou_403, filtrar_processos_visiveis,
)
from app.utils.notificacoes import registrar_log, notificar
from app.utils import tribunais_datajud, agente_ia_router
from app.utils.analise_processo_ia import gerar_analise
from app.utils.fila import enfileirar
from app.utils.cnj import validar_numero_cnj
from app.utils.captura_conectores import obter_conector, ConectorNaoConfiguradoError
from app.utils.conector_datajud import TribunalNaoIdentificadoError, ConexaoDataJudError
from app.utils.captura_pipeline import aplicar_carga_inicial, registrar_movimentacoes_capturadas
from app.utils.conflito_interesse import conflitos_para_parte_contraria
from app.utils.paginacao import paginar
from app.utils.rede import resumir_user_agent
from app.utils.extracao_documento import extrair_texto_documento, ExtracaoNaoSuportadaError

processos_bp = Blueprint("processos", __name__)


def _arquivo_permitido(nome):
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    return ext in current_app.config["ALLOWED_EXTENSIONS"]


def _parse_data(valor):
    """Converte string 'YYYY-MM-DD' vinda de <input type=date> em date, ou None."""
    if not valor:
        return None
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _tentar_captura_automatica_no_cadastro(processo, empresa):
    """
    Tenta a captura automática via DataJud no MOMENTO do cadastro/edição
    manual de um processo (telas "Novo processo"/"Editar processo") —
    reaproveita exatamente o mesmo pipeline usado no cadastro por CNJ
    (governanca.novo_por_cnj) e no botão "Tentar captura automática"
    (governanca.tentar_captura), pra não importar qual tela o usuário usa
    pra cadastrar: digitar o CNJ e salvar já busca os dados sozinho, sem
    precisar ir na tela separada "Cadastrar por CNJ" nem clicar em mais
    nada depois.

    Só tenta quando `numero_processo` tem o FORMATO de um CNJ (20 dígitos,
    segmento de Justiça reconhecido) — número em branco ou com formato
    claramente errado (menos de 20 dígitos, por exemplo) vira
    acompanhamento manual (`forma_acompanhamento` = "manual"), sem tentar
    nada e sem alarme. Já um dígito verificador que não bate com a fórmula
    oficial (módulo 97) NÃO bloqueia mais a tentativa — processos antigos
    às vezes têm número assim mesmo, e é o próprio DataJud (não esse
    cálculo) quem decide se o processo existe de verdade (ver
    `validar_numero_cnj(..., exigir_dv=False)` em app/utils/cnj.py).

    Efeitos colaterais: ajusta processo.monitoravel,
    processo.forma_acompanhamento, processo.motivo_nao_monitoravel e
    processo.tribunal_datajud; quando encontra o processo, também aplica a
    carga inicial e registra as movimentações (idempotente — seguro
    chamar de novo). NÃO faz commit — quem chama decide isso (precisa que
    `processo.id` já exista, ou seja, chamar depois de um `db.session.flush()`
    num cadastro novo).

    Devolve o `aviso_dv` (string) quando encontrou o processo mas o dígito
    verificador não batia — ou None quando não há aviso pra mostrar (não
    achou nada, ou achou e o número era válido normalmente).
    """
    numero = processo.numero_processo
    if not numero:
        processo.forma_acompanhamento = "manual"
        processo.monitoravel = False
        processo.motivo_nao_monitoravel = None
        return None

    resultado = validar_numero_cnj(numero, exigir_dv=False)
    if not resultado["valido"]:
        processo.forma_acompanhamento = "manual"
        processo.monitoravel = False
        processo.motivo_nao_monitoravel = f"Número fora do padrão CNJ: {resultado['motivo']}"
        return None

    tribunal_hint = processo.tribunal_datajud or None
    dados_capturados, motivo = None, None
    try:
        conector = obter_conector("padrao", empresa=empresa)
        dados_capturados = conector.consultar_processo(resultado["partes"]["formatado"], tribunal_hint=tribunal_hint)
    except ConectorNaoConfiguradoError as e:
        motivo = str(e)
    except TribunalNaoIdentificadoError as e:
        motivo = str(e)
    except ConexaoDataJudError as e:
        motivo = str(e)

    if dados_capturados:
        processo.tribunal_datajud = dados_capturados["tribunal_slug"]
        aplicar_carga_inicial(processo, dados_capturados)
        novas = registrar_movimentacoes_capturadas(
            processo, dados_capturados["movimentacoes"], captura_inicial=True
        )
        processo.monitoravel = True
        processo.forma_acompanhamento = "automatico"
        processo.motivo_nao_monitoravel = None
        db.session.add(LogCaptura(
            fonte="datajud", processo_id=processo.id, tribunal=dados_capturados["tribunal_slug"],
            status="sucesso", mensagem=f"{novas} movimentação(ões) capturada(s).",
        ))
        return dados_capturados.get("aviso_dv")
    else:
        processo.forma_acompanhamento = "nao_monitoravel"
        processo.monitoravel = False
        processo.motivo_nao_monitoravel = motivo
        db.session.add(LogCaptura(
            fonte="datajud", processo_id=processo.id, tribunal=tribunal_hint,
            status="falha", mensagem=(motivo or "")[:500],
        ))
        return None


@processos_bp.route("/")
@login_required
def listar():
    query = filtrar_processos_visiveis(aplicar_escopo_unidade(Processo.query, Processo))

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

    # Paginação (PENDENCIAS.md, seção -47) — antes disto, esta tela carregava
    # TODOS os processos do escopo numa `.all()` só, o que em escritório de
    # grande porte (milhares de processos) deixa a página lenta pra carregar
    # e pesada pro banco a cada filtro. `paginar()` lê "pagina"/"por_pagina"
    # da própria URL e nunca deixa passar de POR_PAGINA_MAXIMO.
    paginacao = paginar(query.order_by(Processo.atualizado_em.desc()))
    unidades = unidades_do_escopo() if current_user.is_admin else None
    return render_template("processos/listar.html", processos=paginacao.items, paginacao=paginacao,
                            unidades=unidades, status=status, area=area, termo=termo)


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

        # Tenta buscar os dados automaticamente no DataJud já no cadastro —
        # mesmo comportamento de "Cadastrar por CNJ", só que nesta tela com
        # todos os campos (ver _tentar_captura_automatica_no_cadastro acima).
        empresa_do_cadastro = db.session.get(Unidade, unidade_id).empresa
        aviso_dv = _tentar_captura_automatica_no_cadastro(processo, empresa_do_cadastro)

        db.session.add(Andamento(
            processo_id=processo.id, tipo="movimentacao",
            descricao="Processo cadastrado no sistema.",
            registrado_por_id=current_user.id,
        ))

        registrar_log(current_user, "criou", "Processo", processo.id,
                       processo.numero_processo or processo.numero_interno)
        db.session.commit()

        # Verificação de conflito de interesses (PENDENCIAS.md, seção -42):
        # avisa já no cadastro se a parte contrária deste processo já é
        # cliente do escritório em outro caso. Nunca bloqueia o cadastro —
        # só avisa (fica também visível permanentemente no detalhe do
        # processo, pra quem não estava olhando nesse momento).
        if processo.parte_contraria:
            conflitos = conflitos_para_parte_contraria(
                empresa_do_cadastro.id if empresa_do_cadastro else None,
                processo.parte_contraria, cliente_id_do_processo=processo.cliente_id,
            )
            if conflitos:
                nomes = ", ".join(c.nome for c in conflitos)
                flash(f"⚠️ Possível conflito de interesses: a parte contrária ({processo.parte_contraria}) "
                      f"já é cliente do escritório em outro caso ({nomes}). Revise antes de prosseguir.", "danger")

        if processo.forma_acompanhamento == "automatico" and processo.monitoravel:
            qtd = len(processo.movimentacoes)
            flash(f"Processo cadastrado e em monitoramento automático — dados encontrados no "
                  f"DataJud ({qtd} movimentação(ões))."
                  + (f" Atenção: {aviso_dv}" if aviso_dv else ""), "success")
        elif processo.motivo_nao_monitoravel:
            flash(f"Processo cadastrado, mas não foi possível buscar automaticamente no DataJud: "
                  f"{processo.motivo_nao_monitoravel}", "warning")
        else:
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
    checar_acesso_processo_ou_403(processo)
    from app.models import RegraProximaAcao
    regras_ativas = RegraProximaAcao.query.filter_by(ativo=True).order_by(RegraProximaAcao.ato_capturado).all()
    analises_ia = AnaliseProcessoIA.query.filter_by(processo_id=processo.id) \
        .order_by(AnaliseProcessoIA.criado_em.desc()).all()
    prazos_historico = prazos_historico_elegiveis(processo)
    # Sugestão automática de evidência (assistida — ver PENDENCIAS.md, seção
    # -35): só computada pros prazos históricos elegíveis, que são justamente
    # os que geram trabalho manual de "abrir um por um" hoje. dict prazo_id
    # -> Movimentacao sugerida (só entra quando achou algo plausível).
    sugestoes_evidencia = {
        p.id: sugerir_evidencia_historica(p) for p in prazos_historico
    }
    sugestoes_evidencia = {k: v for k, v in sugestoes_evidencia.items() if v is not None}

    # Verificação de conflito de interesses (PENDENCIAS.md, seção -42): a
    # parte contrária deste processo já é cliente do escritório em outro
    # caso? Checagem ao vivo (não fica salva em lugar nenhum) — sempre
    # reflete o cadastro atual, então também pega conflito que só passou a
    # existir depois deste processo já criado (ex: um novo cliente
    # cadastrado depois com o mesmo nome desta parte contrária).
    conflitos_interesse = conflitos_para_parte_contraria(
        processo.unidade.empresa_id if processo.unidade else None,
        processo.parte_contraria, cliente_id_do_processo=processo.cliente_id,
    ) if processo.parte_contraria else []

    # Auditoria de acesso a documentos (PENDENCIAS.md, seção -51): quantas
    # vezes cada documento já foi baixado, pra mostrar o número junto do
    # botão "Histórico" sem precisar de uma consulta por documento.
    contagem_downloads_documentos = dict(
        db.session.query(LogAtividade.entidade_id, func.count(LogAtividade.id))
        .filter(LogAtividade.entidade == "Documento", LogAtividade.acao == "baixou_documento",
                LogAtividade.entidade_id.in_([d.id for d in processo.documentos]))
        .group_by(LogAtividade.entidade_id)
        .all()
    ) if processo.documentos else {}

    return render_template("processos/detalhe.html", processo=processo, hoje=datetime.utcnow().date(),
                            regras_ativas=regras_ativas, analises_ia=analises_ia,
                            ia_configurada=agente_ia_router.provedor_disponivel(processo.unidade.empresa if processo.unidade else None),
                            tribunais_datajud=tribunais_datajud.TODOS,
                            prazos_historico=prazos_historico,
                            sugestoes_evidencia=sugestoes_evidencia,
                            conflitos_interesse=conflitos_interesse,
                            contagem_downloads_documentos=contagem_downloads_documentos)


@processos_bp.route("/<int:processo_id>/editar", methods=["GET", "POST"])
@login_required
def editar(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)
    unidades = unidades_do_escopo() if current_user.is_admin else None
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).order_by(Cliente.nome).all()
    responsaveis = Usuario.query.filter_by(unidade_id=processo.unidade_id, ativo=True).all()

    if request.method == "POST":
        numero_anterior = processo.numero_processo
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

        # Lista de acesso explícita para processo sigiloso (ver
        # app/models/processo.py::ProcessoAcessoRestrito e
        # app/utils/acesso.py::usuario_pode_ver_processo) — só admin
        # gerencia quem entra na lista, e o campo só é enviado pelo form
        # quando quem está editando é admin (ver processos/form.html).
        if current_user.is_admin:
            ids_marcados = {int(v) for v in request.form.getlist("acesso_usuario_ids") if v.isdigit()}
            ids_atuais = {a.usuario_id for a in processo.acessos_restritos}
            for usuario_id in ids_marcados - ids_atuais:
                db.session.add(ProcessoAcessoRestrito(
                    processo_id=processo.id, usuario_id=usuario_id, concedido_por_id=current_user.id,
                ))
            if ids_atuais - ids_marcados:
                ProcessoAcessoRestrito.query.filter(
                    ProcessoAcessoRestrito.processo_id == processo.id,
                    ProcessoAcessoRestrito.usuario_id.in_(ids_atuais - ids_marcados),
                ).delete(synchronize_session=False)

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

        # Se o número do processo mudou (ex.: corrigindo um dígito digitado
        # errado), tenta a captura automática de novo com o número novo —
        # mesmo comportamento do cadastro (novo() acima) e do botão "Tentar
        # captura automática". Só quando o número muda de verdade: editar
        # outros campos não deve sair rebuscando/re-classificando o
        # acompanhamento de um processo que o usuário já configurou.
        numero_mudou = processo.numero_processo != numero_anterior
        aviso_dv = None
        if numero_mudou:
            empresa_da_edicao = processo.unidade.empresa if processo.unidade else None
            aviso_dv = _tentar_captura_automatica_no_cadastro(processo, empresa_da_edicao)

        registrar_log(current_user, "editou", "Processo", processo.id, processo.numero_processo)
        db.session.commit()

        if numero_mudou and processo.forma_acompanhamento == "automatico" and processo.monitoravel:
            qtd = len(processo.movimentacoes)
            flash(f"Processo atualizado — número novo encontrado no DataJud e em monitoramento "
                  f"automático ({qtd} movimentação(ões))."
                  + (f" Atenção: {aviso_dv}" if aviso_dv else ""), "success")
        elif numero_mudou and processo.motivo_nao_monitoravel:
            flash(f"Processo atualizado, mas não foi possível buscar automaticamente no DataJud com "
                  f"o número novo: {processo.motivo_nao_monitoravel}", "warning")
        else:
            flash("Processo atualizado com sucesso.", "success")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    usuarios_para_acesso = usuarios_do_escopo() if current_user.is_admin else []
    usuarios_com_acesso_ids = {a.usuario_id for a in processo.acessos_restritos}
    return render_template("processos/form.html", processo=processo, unidades=unidades,
                            clientes=clientes, responsaveis=responsaveis,
                            tribunais_datajud=tribunais_datajud.TODOS,
                            usuarios_para_acesso=usuarios_para_acesso,
                            usuarios_com_acesso_ids=usuarios_com_acesso_ids)


# ---------- Andamentos (linha do tempo) ----------

@processos_bp.route("/<int:processo_id>/andamentos", methods=["POST"])
@login_required
def add_andamento(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

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
    checar_acesso_processo_ou_403(processo)

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
    `cumprir_prazo_com_evidencia`, que exige evidência. Pelo mesmo motivo,
    também não aceita "historico_anterior" aqui — só pela rota dedicada
    `regularizar_prazos_historico`, que exige um motivo registrado.
    """
    prazo = db.get_or_404(Prazo, prazo_id)
    checar_acesso_processo_ou_403(prazo.processo)
    novo_status = request.form.get("status")

    if novo_status == "cumprido":
        flash("Prazo não pode ser marcado como cumprido sem evidência. "
              "Anexe o comprovante de protocolo ou vincule a movimentação correspondente.", "warning")
        return redirect(url_for("processos.detalhe", processo_id=prazo.processo_id))

    if novo_status == "historico_anterior":
        flash("Use a ação \"Regularizar prazos anteriores ao cadastro\" (no topo da aba Prazos) para "
              "este status — ela registra o motivo da regularização.", "warning")
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
    checar_acesso_processo_ou_403(prazo.processo)

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


def prazos_historico_elegiveis(processo):
    """
    Prazos "pendentes" com vencimento anterior à data em que ESTE processo
    foi cadastrado no sistema — o sinal de que vieram de uma movimentação
    histórica capturada na carga inicial (ver PENDENCIAS.md, seção -33),
    não de um prazo de verdade em aberto hoje. Reaproveitada tanto pra
    mostrar a contagem/aviso na aba Prazos quanto pela rota que regulariza.
    """
    data_cadastro = processo.criado_em.date() if processo.criado_em else date.today()
    return [
        p for p in processo.prazos
        if p.status == "pendente" and p.data_vencimento and p.data_vencimento < data_cadastro
    ]


# Palavras que, no texto de uma movimentação POSTERIOR à data de início do
# prazo, sugerem que a parte respondeu/protocolou algo (ver
# sugerir_evidencia_historica abaixo, PENDENCIAS.md seção -35).
_PALAVRAS_POSITIVAS_EVIDENCIA = (
    "contestação", "contestacao", "manifestação", "manifestacao", "petição", "peticao",
    "protocolo", "protocolado", "recurso", "embargos", "agravo", "réplica", "replica",
    "tréplica", "treplica", "alegações finais", "alegacoes finais", "cumprimento de",
    "impugnação", "impugnacao", "defesa apresentada", "razões", "razoes", "resposta apresentada",
)
# Palavras que indicam o CONTRÁRIO — a parte ficou em silêncio/o prazo
# correu sem manifestação — nunca sugerir uma movimentação assim como
# evidência de cumprimento, mesmo que também contenha uma palavra positiva.
_PALAVRAS_NEGATIVAS_EVIDENCIA = (
    "decurso de prazo", "decurso do prazo", "certidão de decurso", "certidao de decurso",
    "prazo decorrido", "decorreu o prazo", "sem manifestação", "sem manifestacao",
    "ausência de manifestação", "ausencia de manifestacao", "silêncio da parte", "silencio da parte",
)

# Até quantos dias após o início do prazo a busca por uma movimentação de
# resposta ainda faz sentido — sem esse limite, um processo de décadas
# poderia "achar" uma petição de um capítulo totalmente diferente do caso,
# anos depois, e sugerir errado. Folga generosa (~6 meses) acima de
# qualquer prazo processual comum, pra cobrir tribunal lento sem virar
# risco de casar movimentação errada.
_LIMITE_DIAS_BUSCA_EVIDENCIA = 180


def sugerir_evidencia_historica(prazo):
    """
    Sugestão automática ASSISTIDA (ver PENDENCIAS.md, seção -35) de qual
    movimentação capturada provavelmente corresponde ao cumprimento de um
    prazo histórico — nunca fecha nada sozinha, só pré-preenche o
    formulário "Fechar com evidência" pra o usuário revisar e confirmar
    com um clique, em vez de caçar a movimentação certa manualmente no
    meio de décadas de andamento.

    Só tenta quando o prazo tem `regra_aplicada_id` (veio de uma
    RegraProximaAcao cadastrada, ex: "Citação para contestar" -> ação
    exigida "Apresentar contestação") — prazos genéricos ("Análise
    necessária — ato sem regra cadastrada") NÃO têm uma ação exigida
    definida, então não há o que provar como "cumprimento": não são
    elegíveis pra esta sugestão, só pra regularizar_prazos_historico.

    Procura, entre as movimentações do MESMO processo posteriores à data de
    início do prazo (ordem cronológica, limitadas a
    _LIMITE_DIAS_BUSCA_EVIDENCIA dias — ver constante acima, pra não casar
    com uma petição de anos depois, de um capítulo totalmente diferente do
    caso), a primeira que contém uma palavra-chave de resposta/protocolo.
    Se a primeira movimentação candidata (a mais próxima no tempo) contiver
    um sinal do CONTRÁRIO (certidão de decurso de prazo, silêncio da
    parte), a busca PARA ali e devolve None — esse sinal é justamente o
    registro de que a parte não respondeu naquele período, então nada
    depois dele nesta janela deveria ser oferecido como se fosse a resposta
    a este prazo.

    Isto é heurística por palavra-chave sobre o texto do ato, não uma
    prova jurídica — por isso nunca fecha o prazo sozinha (ver
    processos.cumprir_prazo_com_evidencia: fechamento como "cumprido"
    sempre exige confirmação humana, governança central do projeto).
    Devolve a Movimentacao sugerida, ou None quando não achou nada
    plausível (nesse caso a única opção continua sendo
    regularizar_prazos_historico ou fechar manualmente sem sugestão).
    """
    if not prazo.regra_aplicada_id or not prazo.data_inicial:
        return None

    inicio = datetime.combine(prazo.data_inicial, datetime.min.time())
    limite = inicio + timedelta(days=_LIMITE_DIAS_BUSCA_EVIDENCIA)
    candidatas = (
        Movimentacao.query
        .filter(Movimentacao.processo_id == prazo.processo_id,
                Movimentacao.data > inicio, Movimentacao.data <= limite)
        .order_by(Movimentacao.data.asc())
        .all()
    )
    for mov in candidatas:
        texto = (mov.texto_integral or "").lower()
        if any(neg in texto for neg in _PALAVRAS_NEGATIVAS_EVIDENCIA):
            return None
        if any(pos in texto for pos in _PALAVRAS_POSITIVAS_EVIDENCIA):
            return mov
    return None


@processos_bp.route("/<int:processo_id>/prazos/regularizar-historico", methods=["POST"])
@login_required
def regularizar_prazos_historico(processo_id):
    """
    Ação em lote (ver PENDENCIAS.md, seção -33): quando um processo é
    cadastrado pelo CNJ, o sistema já traz o histórico completo de
    movimentações do tribunal (ver app/utils/captura_pipeline.py) — e
    movimentações antigas que batem com uma regra cadastrada geram um
    Prazo com vencimento no passado. Esse prazo nunca teve chance de ser
    fechado com evidência real (o escritório não estava usando o sistema
    na época), mas normalmente também não foi "perdido" de verdade — o
    processo simplesmente seguiu tramitando. Sem esta ação, alguém teria
    que abrir cada um desses prazos antigos um por um e decidir "perdido"
    ou "cumprido" manualmente.

    Marca todos de uma vez como "historico_anterior" (nunca "cumprido" —
    isso exigiria evidência real, que não existe aqui) — status neutro que
    some da contagem de pendentes/perdidos, mas continua visível e
    auditável na aba Prazos (com o motivo, quem aplicou e quando).
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Descreva o motivo da regularização (ex.: \"histórico anterior ao cadastro deste processo no "
              "sistema — processo seguiu tramitando normalmente\").", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    elegiveis = prazos_historico_elegiveis(processo)
    if not elegiveis:
        flash("Nenhum prazo pendente anterior ao cadastro deste processo no sistema foi encontrado.", "info")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    agora = datetime.utcnow()
    for prazo in elegiveis:
        prazo.status = "historico_anterior"
        prazo.motivo_regularizacao = motivo
        prazo.regularizado_em = agora
        prazo.regularizado_por_id = current_user.id

    registrar_log(current_user, "regularizou_prazos_historico", "Processo", processo.id,
                  f"{len(elegiveis)} prazo(s)")
    db.session.commit()
    flash(f"{len(elegiveis)} prazo(s) anterior(es) ao cadastro deste processo marcados como histórico — "
          "não contam mais como pendentes/perdidos nos painéis, mas continuam visíveis e auditáveis aqui.",
          "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


# ---------- Audiências ----------

@processos_bp.route("/<int:processo_id>/audiencias", methods=["POST"])
@login_required
def add_audiencia(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

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
    checar_acesso_processo_ou_403(audiencia.processo)
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
    checar_acesso_processo_ou_403(processo)

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
    checar_acesso_processo_ou_403(doc.processo)
    # Auditoria de acesso a documentos (PENDENCIAS.md, seção -51): registra
    # QUEM baixou QUAL documento e QUANDO — antes disso, baixar um documento
    # não deixava rastro nenhum, só upload e exclusão eram auditados.
    # entidade_id aqui é o id do próprio Documento (não do Processo, como
    # nos outros dois logs desta tela), de propósito: permite achar o
    # histórico de UM documento específico sem ambiguidade, mesmo que dois
    # documentos do mesmo processo tenham o mesmo nome original.
    registrar_log(current_user, "baixou_documento", "Documento", doc.id, doc.nome_original)
    db.session.commit()
    pasta_processo = os.path.join(current_app.config["UPLOAD_FOLDER"], str(doc.processo_id))
    return send_from_directory(pasta_processo, doc.nome_arquivo,
                                as_attachment=True, download_name=doc.nome_original)


@processos_bp.route("/documentos/<int:documento_id>/historico")
@login_required
def historico_documento(documento_id):
    doc = db.get_or_404(Documento, documento_id)
    checar_acesso_processo_ou_403(doc.processo)
    # Ferramenta de governança/auditoria — mesmo critério de acesso já usado
    # pra aprovação de alçada financeira (só admin ou gestor), não qualquer
    # pessoa com acesso ao processo: ver quem baixou o quê é informação
    # sobre a ATIVIDADE de outros usuários, não sobre o processo em si.
    if not (current_user.is_admin or current_user.is_gestor):
        abort(403)
    logs = LogAtividade.query.filter_by(
        entidade="Documento", entidade_id=doc.id, acao="baixou_documento"
    ).order_by(LogAtividade.criado_em.desc()).all()
    return render_template("processos/historico_documento.html", doc=doc, logs=logs,
                            resumir_user_agent=resumir_user_agent)


@processos_bp.route("/documentos/<int:documento_id>/excluir", methods=["POST"])
@login_required
def excluir_documento(documento_id):
    doc = db.get_or_404(Documento, documento_id)
    checar_acesso_processo_ou_403(doc.processo)
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
    checar_acesso_processo_ou_403(processo)

    tipo = request.form.get("tipo")
    instrucao = request.form.get("instrucao", "").strip()

    if tipo not in AnaliseProcessoIA.TIPOS:
        flash("Tipo de análise inválido.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))
    if tipo == "rascunho_peticao" and not instrucao:
        flash("Descreva o que a petição precisa fazer (ex.: \"contestação alegando decadência\").", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    empresa_do_processo = processo.unidade.empresa if processo.unidade else None
    if not agente_ia_router.provedor_disponivel(empresa_do_processo):
        flash("Agente de IA indisponível para esta empresa no momento (modelo local não baixado, ou "
              "chave da API do Claude não cadastrada — confira em \"Minhas Integrações\").", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    # Documento de referência de estilo (PENDENCIAS.md, seção -53) — opcional,
    # só faz sentido em rascunho_peticao. A extração acontece AQUI (síncrona,
    # só leitura de arquivo local, rápida) em vez de dentro do job de
    # segundo plano, pra já validar/avisar antes de enfileirar. Falha na
    # extração NUNCA bloqueia a geração — só segue sem referência, com um
    # aviso claro do motivo (mesmo princípio de degradação graciosa usado
    # em toda integração opcional deste sistema).
    documento_referencia_id = request.form.get("documento_referencia_id", type=int)
    texto_referencia = None
    if tipo == "rascunho_peticao" and documento_referencia_id:
        doc_referencia = db.session.get(Documento, documento_referencia_id)
        if doc_referencia is None or doc_referencia.processo_id != processo.id:
            flash("Documento de referência inválido — a geração vai seguir sem referência de estilo.", "warning")
        else:
            try:
                texto_referencia, _ = extrair_texto_documento(doc_referencia, current_app.config["UPLOAD_FOLDER"])
            except (ExtracaoNaoSuportadaError, ValueError) as e:
                flash(f"Não usei \"{doc_referencia.nome_original}\" como referência de estilo: {e}", "warning")

    # A geração em si (chamada ao modelo, pode levar minutos) roda em
    # segundo plano — ver app/jobs/ia_jobs.py e PENDENCIAS.md, seção -32.
    # Aqui só cria o registro como "processando" e devolve a tela na hora;
    # a aba Análise IA se atualiza sozinha quando terminar.
    analise = AnaliseProcessoIA(
        processo_id=processo.id, solicitado_por_id=current_user.id, tipo=tipo,
        instrucao=instrucao or None, resultado="", status="processando",
        # Só grava a referência quando o texto realmente foi extraído — se a
        # extração falhou (não suportado/ilegível), fica None: mais honesto
        # do que mostrar "baseado no estilo de X" pra algo que não influenciou
        # a geração de verdade.
        documento_referencia_id=(doc_referencia.id if texto_referencia else None),
    )
    db.session.add(analise)
    registrar_log(current_user, "gerou_analise_ia", "Processo", processo.id, tipo)
    db.session.commit()

    enfileirar("app.jobs.ia_jobs.processar_analise_processo_ia", analise.id, processo.id, tipo, instrucao,
               texto_referencia)

    flash("Gerando análise em segundo plano — acompanhe na aba \"Análise IA\" (atualiza sozinha; "
          "pode levar alguns minutos no modelo local). É sempre um rascunho para conferência humana.",
          "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@processos_bp.route("/analises-ia/<int:analise_id>/excluir", methods=["POST"])
@login_required
def excluir_analise_ia(analise_id):
    analise = db.get_or_404(AnaliseProcessoIA, analise_id)
    checar_acesso_processo_ou_403(analise.processo)
    processo_id = analise.processo_id
    db.session.delete(analise)
    registrar_log(current_user, "excluiu_analise_ia", "Processo", processo_id, analise.tipo)
    db.session.commit()
    flash("Análise removida.", "info")
    return redirect(url_for("processos.detalhe", processo_id=processo_id))
