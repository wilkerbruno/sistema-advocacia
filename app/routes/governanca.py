"""
Rotas de governança de carteira processual — implementação das seções
5 a 10 do briefing que dependiam só de código (sem provedor externo).

Ingestão automática de verdade (seção 5.0/5.2) continua bloqueada por
depender de um provedor de dados processuais contratado (ver
app/utils/captura_conectores.py) — aqui o cadastro por CNJ e a
importação em lote fazem a parte que dá para fazer sem isso: validar o
número, cadastrar o processo e marcar honestamente como "não
monitorável automaticamente" até um conector real existir (seção 5.1,
3º caminho: "o sistema nunca deixa buraco silencioso na carteira").
"""
import csv
import hashlib
import io
from datetime import datetime, timedelta, date
from decimal import Decimal

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, Response, current_app)
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models import (Processo, Cliente, Unidade, Movimentacao, Publicacao, Decisao,
                         Prazo, HistoricoEstadoProcesso, SenhaProcesso, LogCaptura,
                         MapaEstadoTPU, RegraProximaAcao)
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo
from app.utils.notificacoes import registrar_log, notificar
from app.utils.cnj import validar_numero_cnj, somente_digitos
from app.utils.cofre import cifrar_senha_processo, decifrar_senha_processo, CofreNaoConfiguradoError
from app.utils.captura_conectores import obter_conector, ConectorNaoConfiguradoError
from app.utils.conector_datajud import TribunalNaoIdentificadoError, ConexaoDataJudError
from app.utils.captura_pipeline import aplicar_carga_inicial, registrar_movimentacoes_capturadas
from app.utils.estado_processual_engine import traduzir_movimentacao
from app.utils.prazos_engine import aplicar_regra_proxima_acao
from app.utils import tribunais_datajud

governanca_bp = Blueprint("governanca", __name__)


# ---------- Cadastro por número CNJ (seção 5.0) ----------

@governanca_bp.route("/processos/novo-por-cnj", methods=["GET", "POST"])
@login_required
def novo_por_cnj():
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()
    unidades = unidades_do_escopo() if current_user.is_admin else None

    if request.method == "POST":
        numero = request.form.get("numero_cnj", "")
        resultado = validar_numero_cnj(numero)
        if not resultado["valido"]:
            flash(f"Número CNJ inválido: {resultado['motivo']}", "danger")
            return redirect(url_for("governanca.novo_por_cnj"))

        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)
        partes = resultado["partes"]
        tribunal_hint = request.form.get("tribunal_datajud") or None

        # Tenta a ingestão automática real via DataJud (gratuito, ver
        # app/utils/conector_datajud.py) — cai para "não monitorável" de
        # forma honesta se a chave não estiver configurada, se o tribunal
        # não puder ser identificado, ou se o processo ainda não estiver
        # indexado (segredo de justiça, ou defasagem do próprio DataJud).
        dados_capturados = None
        try:
            conector = obter_conector("padrao")
            dados_capturados = conector.consultar_processo(partes["formatado"], tribunal_hint=tribunal_hint)
            forma_acompanhamento, monitoravel, motivo = "automatico", True, None
        except ConectorNaoConfiguradoError as e:
            forma_acompanhamento, monitoravel, motivo = "nao_monitoravel", False, str(e)
        except TribunalNaoIdentificadoError as e:
            forma_acompanhamento, monitoravel, motivo = "nao_monitoravel", False, str(e)
        except ConexaoDataJudError as e:
            forma_acompanhamento, monitoravel, motivo = "nao_monitoravel", False, str(e)

        processo = Processo(
            numero_processo=partes["formatado"],
            area_direito=request.form.get("area_direito") or "Não classificada",
            cliente_id=request.form["cliente_id"],
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
            responsavel_id=current_user.id,
            forma_acompanhamento=forma_acompanhamento,
            monitoravel=monitoravel,
            motivo_nao_monitoravel=motivo,
            tribunal_datajud=(dados_capturados["tribunal_slug"] if dados_capturados else tribunal_hint),
            segredo_justica=bool(request.form.get("segredo_justica")),
        )
        db.session.add(processo)
        db.session.flush()

        qtd_movimentacoes_novas = 0
        if dados_capturados:
            aplicar_carga_inicial(processo, dados_capturados)
            qtd_movimentacoes_novas = registrar_movimentacoes_capturadas(
                processo, dados_capturados["movimentacoes"]
            )
            db.session.add(LogCaptura(
                fonte="datajud", processo_id=processo.id, tribunal=dados_capturados["tribunal_slug"],
                status="sucesso", mensagem=f"{qtd_movimentacoes_novas} movimentação(ões) capturada(s).",
            ))
        elif motivo:
            db.session.add(LogCaptura(
                fonte="datajud", processo_id=processo.id, tribunal=tribunal_hint,
                status="falha", mensagem=motivo[:500],
            ))

        registrar_log(current_user, "cadastro_por_cnj", "Processo", processo.id, processo.numero_processo)
        db.session.commit()

        if not monitoravel:
            flash(f"Processo {processo.numero_processo} cadastrado, mas marcado como NÃO monitorável "
                  f"automaticamente: {motivo}", "warning")
        else:
            flash(f"Processo {processo.numero_processo} cadastrado e em monitoramento automático "
                  f"({qtd_movimentacoes_novas} movimentação(ões) já capturada(s) do DataJud).", "success")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    return render_template("governanca/novo_por_cnj.html", clientes=clientes, unidades=unidades,
                            tribunais_datajud=tribunais_datajud.TODOS)


# ---------- Importação em lote (CSV) ----------

@governanca_bp.route("/processos/importar-lote", methods=["GET", "POST"])
@login_required
def importar_lote():
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()

    if request.method == "POST":
        arquivo = request.files.get("arquivo_csv")
        cliente_id = request.form.get("cliente_id")
        area_direito = request.form.get("area_direito") or "Não classificada"
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        if not arquivo or not cliente_id:
            flash("Selecione um arquivo CSV (uma coluna com números CNJ, um por linha) e o cliente.", "danger")
            return redirect(url_for("governanca.importar_lote"))

        conteudo = arquivo.read().decode("utf-8-sig", errors="ignore")
        linhas = [l.strip() for l in conteudo.splitlines() if l.strip()]

        relatorio = []
        criados = 0
        for linha in linhas:
            numero = linha.split(",")[0].strip()
            if somente_digitos(numero) == "" or len(somente_digitos(numero)) < 20 and numero.lower() in ("numero_cnj", "numero", "cnj"):
                continue  # ignora possível cabeçalho
            resultado = validar_numero_cnj(numero)
            if not resultado["valido"]:
                relatorio.append({"numero": numero, "sucesso": False, "motivo": resultado["motivo"]})
                continue

            partes = resultado["partes"]
            existente = Processo.query.filter_by(numero_processo=partes["formatado"]).first()
            if existente:
                relatorio.append({"numero": partes["formatado"], "sucesso": False, "motivo": "Já cadastrado."})
                continue

            processo = Processo(
                numero_processo=partes["formatado"],
                area_direito=area_direito,
                cliente_id=cliente_id,
                unidade_id=unidade_id,
                criado_por_id=current_user.id,
                responsavel_id=current_user.id,
                forma_acompanhamento="nao_monitoravel",
                monitoravel=False,
                motivo_nao_monitoravel="Importado em lote — sem provedor de captura configurado (seção 5.2).",
            )
            db.session.add(processo)
            criados += 1
            relatorio.append({"numero": partes["formatado"], "sucesso": True, "motivo": None})

        registrar_log(current_user, "importacao_lote", "Processo", None,
                      f"{criados} processos criados de {len(linhas)} linhas")
        db.session.commit()

        # Nota importante de honestidade: sem fila assíncrona (Celery/RQ) provisionada,
        # este processamento é síncrono — para lotes grandes (ex: os 500 do briefing),
        # a requisição HTTP pode demorar. Ver README para o que falta de infraestrutura.
        return render_template("governanca/importar_lote_resultado.html", relatorio=relatorio, criados=criados,
                                total=len(linhas))

    return render_template("governanca/importar_lote.html", clientes=clientes)


# ---------- Cofre de senha de processo (seção 5.1) ----------

@governanca_bp.route("/processos/<int:processo_id>/senha", methods=["POST"])
@login_required
def cadastrar_senha_processo(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    valor = request.form.get("valor")
    tribunal = request.form.get("tribunal")
    if not valor:
        flash("Informe a senha do processo.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        cifrado = cifrar_senha_processo(valor)
    except CofreNaoConfiguradoError as e:
        flash(str(e), "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    existente = SenhaProcesso.query.filter_by(processo_id=processo.id).first()
    if existente:
        existente.valor_criptografado = cifrado
        existente.tribunal = tribunal
        existente.cadastrado_por_id = current_user.id
        existente.cadastrado_em = datetime.utcnow()
    else:
        db.session.add(SenhaProcesso(
            processo_id=processo.id, tribunal=tribunal, valor_criptografado=cifrado,
            cadastrado_por_id=current_user.id,
        ))

    processo.forma_acompanhamento = "senha_processo"
    processo.monitoravel = True
    processo.motivo_nao_monitoravel = None

    registrar_log(current_user, "cadastrar_senha_processo", "Processo", processo.id, "senha do processo cadastrada")
    db.session.commit()
    flash("Senha do processo cadastrada no cofre (criptografada).", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/senha/ver", methods=["POST"])
@login_required
def ver_senha_processo(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    senha = SenhaProcesso.query.filter_by(processo_id=processo.id).first()
    if not senha:
        flash("Nenhuma senha cadastrada para este processo.", "warning")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    # Acesso restrito ao usuário que cadastrou (seção 5.1), exceto admin.
    if senha.cadastrado_por_id != current_user.id and not current_user.is_admin:
        flash("Só quem cadastrou a senha (ou um admin) pode visualizá-la.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        valor = decifrar_senha_processo(senha.valor_criptografado)
    except (CofreNaoConfiguradoError, ValueError) as e:
        flash(str(e), "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    senha.ultimo_acesso_em = datetime.utcnow()
    senha.ultimo_acesso_por_id = current_user.id
    registrar_log(current_user, "ver_senha_processo", "Processo", processo.id, "leitura da senha do processo")
    db.session.commit()

    return render_template("governanca/senha_visualizar.html", processo=processo, senha=senha, valor=valor)


# ---------- Registro manual de movimentação (stand-in para captura automática) ----------

@governanca_bp.route("/processos/<int:processo_id>/movimentacoes/nova", methods=["POST"])
@login_required
def nova_movimentacao(processo_id):
    """
    Enquanto não há conector de captura real (ver captura_conectores.py),
    esta é a porta de entrada para registrar uma movimentação e disparar
    a máquina de estados (seção 6) e o motor de próxima ação (seção 7.1) —
    o mesmo pipeline que rodaria automaticamente quando o conector existir.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)

    data_str = request.form.get("data")
    codigo_tpu = request.form.get("codigo_tpu") or None
    texto = request.form.get("texto_integral", "").strip()
    if not texto:
        flash("Descreva o texto da movimentação.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    data_mov = datetime.strptime(data_str, "%Y-%m-%dT%H:%M") if data_str else datetime.utcnow()
    hash_dedup = hashlib.sha256(f"{processo.id}|{data_mov.isoformat()}|{texto}".encode()).hexdigest()

    if Movimentacao.query.filter_by(hash_dedup=hash_dedup).first():
        flash("Movimentação idêntica já registrada (deduplicação).", "warning")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    mov = Movimentacao(
        processo_id=processo.id, data=data_mov, codigo_tpu=codigo_tpu,
        texto_integral=texto, origem_captura="manual", hash_dedup=hash_dedup,
    )
    db.session.add(mov)
    db.session.flush()

    historico = traduzir_movimentacao(mov)
    if historico:
        db.session.add(historico)

    prazo_gerado = aplicar_regra_proxima_acao(mov)
    if prazo_gerado:
        db.session.add(prazo_gerado)
        db.session.flush()
        if prazo_gerado.responsavel_id:
            notificar(prazo_gerado.responsavel_id, "Novo prazo gerado automaticamente",
                      f"{prazo_gerado.descricao} — vence em {prazo_gerado.data_vencimento.strftime('%d/%m/%Y')}",
                      tipo="prazo", link=url_for("processos.detalhe", processo_id=processo.id))

    registrar_log(current_user, "registrar_movimentacao", "Processo", processo.id,
                  f"triagem_pendente={mov.triagem_pendente}")
    db.session.commit()

    if mov.triagem_pendente:
        flash("Movimentação registrada, mas o código TPU não está mapeado — "
              "caiu na fila de triagem (aba Governança).", "warning")
    else:
        flash(f"Movimentação registrada. Estado atualizado para: {mov.estado_negocio_resultante or '—'}.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/nao-monitoravel", methods=["POST"])
@login_required
def marcar_nao_monitoravel(processo_id):
    """3º caminho da seção 5.1: marcação explícita, nunca buraco silencioso."""
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_unidade_ou_403(processo.unidade_id)
    motivo = request.form.get("motivo", "").strip() or "Motivo não informado"
    processo.forma_acompanhamento = "nao_monitoravel"
    processo.monitoravel = False
    processo.motivo_nao_monitoravel = motivo
    registrar_log(current_user, "marcar_nao_monitoravel", "Processo", processo.id, motivo)
    db.session.commit()
    flash("Processo marcado como não monitorável automaticamente — aparece sinalizado no painel.", "info")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


# ---------- Fila de intimações (seção 7.2) ----------

@governanca_bp.route("/fila-intimacoes")
@login_required
def fila_intimacoes():
    query = Prazo.query.join(Processo).filter(
        Prazo.deletado_em.is_(None),
        Prazo.status != "cumprido",
    )
    if not current_user.is_admin:
        query = query.filter(Processo.unidade_id == current_user.unidade_id)

    prazos = query.order_by(Prazo.data_vencimento).all()
    hoje = date.today()
    return render_template("governanca/fila_intimacoes.html", prazos=prazos, hoje=hoje)


# ---------- Painel de governança (seção 8) ----------

@governanca_bp.route("/painel")
@login_required
def painel():
    hoje = date.today()
    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    if not current_user.is_admin:
        prazos_base = Prazo.query.join(Processo).filter(Processo.unidade_id == current_user.unidade_id)
    else:
        prazos_base = Prazo.query.join(Processo)
    prazos_base = prazos_base.filter(Prazo.deletado_em.is_(None))

    prazos_7d = prazos_base.filter(
        Prazo.status != "cumprido", Prazo.data_vencimento.between(hoje, hoje + timedelta(days=7))
    ).order_by(Prazo.data_vencimento).all()
    prazos_15d = prazos_base.filter(
        Prazo.status != "cumprido",
        Prazo.data_vencimento.between(hoje + timedelta(days=8), hoje + timedelta(days=15)),
    ).order_by(Prazo.data_vencimento).all()
    prazos_vencidos_sem_evidencia = prazos_base.filter(
        Prazo.status != "cumprido", Prazo.data_vencimento < hoje
    ).order_by(Prazo.data_vencimento).all()

    limite_30 = hoje - timedelta(days=30)
    limite_60 = hoje - timedelta(days=60)
    limite_90 = hoje - timedelta(days=90)
    processos_ativos = processos_q.filter(Processo.status == "ativo")
    parados_30 = processos_ativos.filter(Processo.ultima_movimentacao_em.is_(None) | (Processo.ultima_movimentacao_em <= limite_30)).count()
    parados_60 = processos_ativos.filter(Processo.ultima_movimentacao_em.is_(None) | (Processo.ultima_movimentacao_em <= limite_60)).count()
    parados_90 = processos_ativos.filter(Processo.ultima_movimentacao_em.is_(None) | (Processo.ultima_movimentacao_em <= limite_90)).count()

    distribuicao_fase = dict(processos_q.with_entities(Processo.fase, func.count(Processo.id)).group_by(Processo.fase).all())
    distribuicao_area = dict(processos_q.with_entities(Processo.area_direito, func.count(Processo.id)).group_by(Processo.area_direito).all())
    distribuicao_unidade = None
    if current_user.is_admin:
        distribuicao_unidade = dict(
            db.session.query(Unidade.nome, func.count(Processo.id))
            .join(Processo, Processo.unidade_id == Unidade.id).group_by(Unidade.nome).all()
        )

    exposicao_por_fase = dict(
        processos_q.with_entities(Processo.fase, func.coalesce(func.sum(Processo.valor_causa), 0))
        .group_by(Processo.fase).all()
    )
    exposicao_por_risco = dict(
        processos_q.with_entities(Processo.classificacao_risco, func.coalesce(func.sum(Processo.valor_causa), 0))
        .group_by(Processo.classificacao_risco).all()
    )

    limite_24h = datetime.utcnow() - timedelta(hours=24)
    tipos_criticos = ["sentenca", "decisao", "penhora", "bloqueio", "audiencia", "intimacao_pessoal", "auto_de_infracao"]
    movimentacoes_criticas = Movimentacao.query.join(Processo).filter(
        Movimentacao.criado_em >= limite_24h, Movimentacao.deletado_em.is_(None),
    )
    if not current_user.is_admin:
        movimentacoes_criticas = movimentacoes_criticas.filter(Processo.unidade_id == current_user.unidade_id)
    movimentacoes_criticas = movimentacoes_criticas.order_by(Movimentacao.data.desc()).limit(20).all()

    processos_nao_monitoraveis = processos_q.filter(Processo.monitoravel.is_(False)).all()

    return render_template(
        "governanca/painel.html", hoje=hoje,
        prazos_7d=prazos_7d, prazos_15d=prazos_15d, prazos_vencidos_sem_evidencia=prazos_vencidos_sem_evidencia,
        parados_30=parados_30, parados_60=parados_60, parados_90=parados_90,
        distribuicao_fase=distribuicao_fase, distribuicao_area=distribuicao_area, distribuicao_unidade=distribuicao_unidade,
        exposicao_por_fase=exposicao_por_fase, exposicao_por_risco=exposicao_por_risco,
        movimentacoes_criticas=movimentacoes_criticas, processos_nao_monitoraveis=processos_nao_monitoraveis,
    )


# ---------- Métricas de governança (seção 9) ----------

@governanca_bp.route("/metricas")
@login_required
def metricas():
    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    if not current_user.is_admin:
        prazos_q = Prazo.query.join(Processo).filter(Processo.unidade_id == current_user.unidade_id)
    else:
        prazos_q = Prazo.query.join(Processo)
    prazos_q = prazos_q.filter(Prazo.deletado_em.is_(None))

    total_prazos_finalizados = prazos_q.filter(Prazo.status.in_(["cumprido", "perdido"])).count()
    cumpridos = prazos_q.filter(Prazo.status == "cumprido").count()
    perdidos = prazos_q.filter(Prazo.status == "perdido").count()
    taxa_cumprimento = (cumpridos / total_prazos_finalizados * 100) if total_prazos_finalizados else None

    prazos_perdidos_por_processo = (
        prazos_q.filter(Prazo.status == "perdido")
        .with_entities(Processo.numero_processo, Processo.id, func.count(Prazo.id))
        .group_by(Processo.id, Processo.numero_processo)
        .order_by(func.count(Prazo.id).desc()).limit(15).all()
    )

    # Tempo médio entre publicação e protocolo (só calculável quando há
    # publicacao vinculada + prazo cumprido com evidência)
    cumpridos_com_publicacao = prazos_q.filter(
        Prazo.status == "cumprido", Prazo.publicacao_id.isnot(None), Prazo.cumprido_em.isnot(None)
    ).join(Publicacao, Prazo.publicacao_id == Publicacao.id).all()
    if cumpridos_com_publicacao:
        deltas = [
            (p.cumprido_em.date() - p.publicacao.data_publicacao).days
            for p in cumpridos_com_publicacao if p.publicacao.data_publicacao
        ]
        tempo_medio_publicacao_protocolo = sum(deltas) / len(deltas) if deltas else None
    else:
        tempo_medio_publicacao_protocolo = None

    hoje = date.today()
    processos_ativos = processos_q.filter(Processo.status == "ativo").all()
    idades = [(hoje - p.data_distribuicao).days for p in processos_ativos if p.data_distribuicao]
    idade_media_dias = sum(idades) / len(idades) if idades else None

    total_carteira = processos_q.count()
    monitoraveis_automatico = processos_q.filter(
        Processo.forma_acompanhamento == "automatico", Processo.monitoravel.is_(True)
    ).count()
    cobertura_pct = (monitoraveis_automatico / total_carteira * 100) if total_carteira else None

    limite_7d = datetime.utcnow() - timedelta(days=7)
    logs_recentes = LogCaptura.query.filter(LogCaptura.executado_em >= limite_7d).all()
    if logs_recentes:
        sucesso_7d = len([l for l in logs_recentes if l.status == "sucesso"])
        pct_captura_saudavel = sucesso_7d / len(logs_recentes) * 100
    else:
        pct_captura_saudavel = None  # sem execuções de captura ainda — ver captura_conectores.py

    # ---- BI: taxa de sucesso, ganhos/perdas, tempo médio de duração (paridade item 4) ----
    processos_encerrados = processos_q.filter(Processo.status == "encerrado").all()
    distribuicao_desfecho = {}
    for p in processos_encerrados:
        chave = p.desfecho or "sem_desfecho_registrado"
        distribuicao_desfecho[chave] = distribuicao_desfecho.get(chave, 0) + 1

    ganhos = distribuicao_desfecho.get("ganho", 0)
    perdas = distribuicao_desfecho.get("perda", 0)
    acordos = distribuicao_desfecho.get("acordo", 0)
    total_com_desfecho_definido = sum(v for k, v in distribuicao_desfecho.items() if k != "sem_desfecho_registrado")
    # Taxa de sucesso considera ganho + acordo como resultado favorável, sobre
    # o total de processos com desfecho já registrado (nunca sobre a carteira
    # inteira, que incluiria processos ainda em curso e distorceria o número).
    taxa_sucesso = ((ganhos + acordos) / total_com_desfecho_definido * 100) if total_com_desfecho_definido else None

    duracoes = [
        (p.data_encerramento - p.data_distribuicao).days
        for p in processos_encerrados
        if p.data_encerramento and p.data_distribuicao
    ]
    tempo_medio_duracao_dias = sum(duracoes) / len(duracoes) if duracoes else None

    return render_template(
        "governanca/metricas.html",
        taxa_cumprimento=taxa_cumprimento, cumpridos=cumpridos, perdidos=perdidos,
        total_prazos_finalizados=total_prazos_finalizados,
        prazos_perdidos_por_processo=prazos_perdidos_por_processo,
        tempo_medio_publicacao_protocolo=tempo_medio_publicacao_protocolo,
        idade_media_dias=idade_media_dias, total_carteira=total_carteira,
        cobertura_pct=cobertura_pct, monitoraveis_automatico=monitoraveis_automatico,
        pct_captura_saudavel=pct_captura_saudavel,
        taxa_sucesso=taxa_sucesso, ganhos=ganhos, perdas=perdas, acordos=acordos,
        total_processos_encerrados=len(processos_encerrados),
        total_com_desfecho_definido=total_com_desfecho_definido,
        tempo_medio_duracao_dias=tempo_medio_duracao_dias,
    )


# ---------- Produtividade por advogado (item 2 do briefing de paridade) ----------

@governanca_bp.route("/produtividade")
@login_required
def produtividade():
    """
    Ranking de produtividade individual — item 2 ("controle de
    produtividade") do briefing de paridade. Cada linha soma o que está
    sob responsabilidade daquele usuário: prazos, tarefas e horas
    apontadas (quando o timesheet estiver em uso).
    """
    from app.models import Usuario, Tarefa, Apontamento

    if current_user.is_admin_desenvolvedor:
        usuarios_q = Usuario.query.filter_by(ativo=True)
    elif current_user.is_admin:
        usuarios_q = Usuario.query.join(Unidade).filter(
            Unidade.empresa_id == current_user.empresa_id_atual, Usuario.ativo.is_(True)
        )
    else:
        usuarios_q = Usuario.query.filter_by(unidade_id=current_user.unidade_id, ativo=True)

    hoje = date.today()
    linhas = []
    for u in usuarios_q.order_by(Usuario.nome).all():
        prazos_usuario = Prazo.query.filter(Prazo.responsavel_id == u.id, Prazo.deletado_em.is_(None))
        cumpridos = prazos_usuario.filter(Prazo.status == "cumprido").count()
        perdidos = prazos_usuario.filter(Prazo.status == "perdido").count()
        finalizados = cumpridos + perdidos
        taxa = (cumpridos / finalizados * 100) if finalizados else None

        tarefas_concluidas = Tarefa.query.filter(
            Tarefa.responsavel_id == u.id, Tarefa.status == "concluida"
        ).count()
        tarefas_atrasadas = Tarefa.query.filter(
            Tarefa.responsavel_id == u.id, Tarefa.status.in_(["pendente", "em_andamento"]),
            Tarefa.data_vencimento.isnot(None), Tarefa.data_vencimento < hoje,
        ).count()

        horas_total = db.session.query(func.coalesce(func.sum(Apontamento.horas), 0)).filter(
            Apontamento.usuario_id == u.id
        ).scalar()

        if finalizados == 0 and tarefas_concluidas == 0 and tarefas_atrasadas == 0 and not horas_total:
            continue  # não polui o ranking com usuário sem nenhuma atividade registrada

        linhas.append(dict(
            usuario=u, cumpridos=cumpridos, perdidos=perdidos, taxa=taxa,
            tarefas_concluidas=tarefas_concluidas, tarefas_atrasadas=tarefas_atrasadas,
            horas_total=horas_total,
        ))

    linhas.sort(key=lambda l: (l["taxa"] if l["taxa"] is not None else -1), reverse=True)

    return render_template("governanca/produtividade.html", linhas=linhas)


# ---------- Contingenciamento jurídico formal (item 7 do briefing de paridade) ----------

@governanca_bp.route("/contingenciamento")
@login_required
def contingenciamento():
    """
    Provisão de contingência: valor da causa × percentual da classificação
    (provável=100%, possível=50%, remoto=0%, ou percentual manual por
    processo) — não apenas soma bruta por categoria de risco operacional,
    que é o que `classificacao_risco` já fazia no painel de governança.
    """
    processos_q = aplicar_escopo_unidade(Processo.query, Processo).filter(Processo.status == "ativo")

    totais_por_classificacao = {"provavel": Decimal("0"), "possivel": Decimal("0"), "remoto": Decimal("0"), "sem_classificacao": Decimal("0")}
    contagem_por_classificacao = {"provavel": 0, "possivel": 0, "remoto": 0, "sem_classificacao": 0}
    exposicao_total = Decimal("0")
    provisao_total = Decimal("0")
    processos_classificados = []

    for p in processos_q.all():
        if p.valor_causa is None:
            continue
        exposicao_total += p.valor_causa
        chave = p.classificacao_contingencia or "sem_classificacao"
        contagem_por_classificacao[chave] = contagem_por_classificacao.get(chave, 0) + 1
        provisionado = p.valor_provisionado or Decimal("0")
        totais_por_classificacao[chave] = totais_por_classificacao.get(chave, Decimal("0")) + provisionado
        provisao_total += provisionado
        if p.classificacao_contingencia:
            processos_classificados.append(p)

    processos_classificados.sort(key=lambda p: p.valor_provisionado or Decimal("0"), reverse=True)

    return render_template(
        "governanca/contingenciamento.html",
        linhas=processos_classificados[:30],
        totais_por_classificacao=totais_por_classificacao,
        contagem_por_classificacao=contagem_por_classificacao,
        exposicao_total=exposicao_total, provisao_total=provisao_total,
    )


# ---------- Export para Data Lake (seção 12) ----------

@governanca_bp.route("/export/<entidade>.csv")
@login_required
def exportar_csv(entidade):
    """
    Export tabular (CSV) autenticado de processos/movimentações/decisões/
    prazos, para o escritório consumir no próprio Data Lake (seção 12:
    "o sistema deve ser fonte, não ilha"). Exige login (equivalente a
    "API de leitura autenticada" em uma versão simples, síncrona —
    uma API real com token de serviço fica fácil de adicionar depois,
    mas não foi criada agora para não inventar um mecanismo de auth
    novo sem alinhar com você primeiro).
    """
    mapeamento = {
        "processos": (Processo, ["id", "numero_processo", "area_direito", "fase", "estado_negocio_atual",
                                  "status", "status_comercial", "unidade_id", "cliente_id", "valor_causa",
                                  "data_distribuicao", "monitoravel", "forma_acompanhamento"]),
        "movimentacoes": (Movimentacao, ["id", "processo_id", "data", "codigo_tpu", "estado_negocio_resultante",
                                          "origem_captura", "triagem_pendente"]),
        "decisoes": (Decisao, ["id", "processo_id", "tipo", "orgao_julgador", "magistrado_relator", "data", "resultado"]),
        "prazos": (Prazo, ["id", "processo_id", "descricao", "data_vencimento", "status", "calculo_automatico",
                            "responsavel_id"]),
    }
    if entidade not in mapeamento:
        flash("Entidade de export desconhecida.", "danger")
        return redirect(url_for("governanca.painel"))

    modelo, campos = mapeamento[entidade]
    query = modelo.query
    if hasattr(modelo, "processo") and not current_user.is_admin:
        query = query.join(Processo).filter(Processo.unidade_id == current_user.unidade_id)
    elif modelo is Processo and not current_user.is_admin:
        query = query.filter(Processo.unidade_id == current_user.unidade_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(campos)
    for registro in query.limit(20000).all():
        writer.writerow([getattr(registro, campo) for campo in campos])

    registrar_log(current_user, "export_datalake", entidade, None, f"{query.count()} registros")
    db.session.commit()

    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={entidade}.csv"},
    )


# ---------- Relatório semanal (seção 10) ----------
# Geração de conteúdo pronta. O ENVIO automático por e-mail (ou WhatsApp via
# Evolution API) está bloqueado — depende de credenciais SMTP/instância que
# não temos aqui (ver README, seção "Bloqueado"). Este preview permite ver
# exatamente o que seria enviado, e serve de base para plugar o envio assim
# que houver credencial e um agendador (cron/Celery beat) no servidor.

@governanca_bp.route("/relatorio-semanal/preview")
@login_required
def relatorio_semanal_preview():
    hoje = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    semana_passada_inicio = inicio_semana - timedelta(days=7)

    prazos_q = Prazo.query.join(Processo).filter(Prazo.deletado_em.is_(None))
    if not current_user.is_admin:
        prazos_q = prazos_q.filter(Processo.unidade_id == current_user.unidade_id)

    prazos_da_semana = prazos_q.filter(
        Prazo.data_vencimento.between(inicio_semana, fim_semana), Prazo.status != "cumprido"
    ).order_by(Prazo.data_vencimento).all()

    prazos_perdidos_semana_passada = prazos_q.filter(
        Prazo.status == "perdido",
        Prazo.data_vencimento.between(semana_passada_inicio, inicio_semana - timedelta(days=1)),
    ).all()

    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    limite_30 = datetime.utcnow() - timedelta(days=30)
    processos_parados = processos_q.filter(
        Processo.status == "ativo",
        or_(Processo.ultima_movimentacao_em.is_(None), Processo.ultima_movimentacao_em <= limite_30),
    ).limit(20).all()

    inicio_semana_dt = datetime.combine(inicio_semana, datetime.min.time())
    movimentacoes_semana = Movimentacao.query.join(Processo).filter(
        Movimentacao.criado_em >= inicio_semana_dt, Movimentacao.deletado_em.is_(None),
    )
    if not current_user.is_admin:
        movimentacoes_semana = movimentacoes_semana.filter(Processo.unidade_id == current_user.unidade_id)
    movimentacoes_semana = movimentacoes_semana.order_by(Movimentacao.data.desc()).limit(30).all()

    return render_template(
        "governanca/relatorio_semanal_preview.html",
        inicio_semana=inicio_semana, fim_semana=fim_semana,
        prazos_da_semana=prazos_da_semana, prazos_perdidos_semana_passada=prazos_perdidos_semana_passada,
        processos_parados=processos_parados, movimentacoes_semana=movimentacoes_semana,
    )
