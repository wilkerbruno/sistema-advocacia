"""
Captura da intimação por OAB do escritório (item 1 da lista de pipeline de
IA jurídica — PENDENCIAS.md, seção -102): "o input humano do onboarding é a
OAB, não o processo". Aditivo ao cadastro por CNJ (governanca.novo_por_cnj)
e ao cadastro manual completo (processos.novo) — os três convivem.

Duas telas:
- `/captacao-oab/` — cadastro das OABs monitoradas do escritório (a "OAB do
  escritório" do item 1 — na prática, uma lista, porque um escritório real
  tem vários advogados, cada um com a própria OAB).
- `/captacao-oab/triagem` — intimações capturadas cujo número de processo
  não bateu com nenhum processo já cadastrado nesta empresa (ver
  app/utils/captura_djen_pipeline.py) — aqui um humano decide: vincular a
  um processo já cadastrado (número divergente/truncado), cadastrar um
  processo novo a partir do número desta intimação, ou ignorar.
"""
import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, jsonify
from flask_login import login_required, current_user

from app.extensions import db, csrf
from app.models import OabMonitorada, IntimacaoCapturada, Processo, Unidade
from app.utils.acesso import (aplicar_escopo_unidade, unidade_id_para_novo_registro,
                               checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo)
from app.utils.notificacoes import registrar_log
from app.utils.captura_conectores import PublicacaoCapturada
from app.utils.captura_djen_pipeline import processar_publicacao_capturada, vincular_intimacao_a_processo
from app.utils.cnj import somente_digitos

captacao_oab_bp = Blueprint("captacao_oab", __name__, url_prefix="/captacao-oab")


@captacao_oab_bp.route("/")
@login_required
def index():
    oabs = aplicar_escopo_unidade(OabMonitorada.query, OabMonitorada).order_by(OabMonitorada.criado_em.desc()).all()
    total_pendente_triagem = aplicar_escopo_unidade(
        IntimacaoCapturada.query, IntimacaoCapturada
    ).filter_by(status="pendente_triagem").count()
    usuarios = usuarios_do_escopo()
    unidades = unidades_do_escopo() if current_user.is_admin else None
    return render_template("captacao_oab/index.html", oabs=oabs, total_pendente_triagem=total_pendente_triagem,
                            usuarios=usuarios, unidades=unidades)


@captacao_oab_bp.route("/nova", methods=["POST"])
@login_required
def nova():
    numero = somente_digitos(request.form.get("numero", ""))
    uf = (request.form.get("uf") or "").strip().upper()
    if not numero:
        flash("Informe o número da OAB.", "danger")
        return redirect(url_for("captacao_oab.index"))
    if len(uf) != 2:
        flash("Informe a UF da OAB (2 letras, ex: SP).", "danger")
        return redirect(url_for("captacao_oab.index"))

    unidade_id = unidade_id_para_novo_registro()
    checar_acesso_unidade_ou_403(unidade_id)

    if OabMonitorada.query.filter_by(unidade_id=unidade_id, numero=numero, uf=uf).first():
        flash(f"OAB {numero}/{uf} já está cadastrada nesta unidade.", "warning")
        return redirect(url_for("captacao_oab.index"))

    oab = OabMonitorada(
        unidade_id=unidade_id, numero=numero, uf=uf,
        nome_advogado=request.form.get("nome_advogado") or None,
        usuario_id=request.form.get("usuario_id") or None,
        criado_por_id=current_user.id,
        token_webhook=secrets.token_hex(24),
    )
    db.session.add(oab)
    registrar_log(current_user, "cadastrou_oab_monitorada", "OabMonitorada", None, f"{numero}/{uf}")
    db.session.commit()
    flash(f"OAB {numero}/{uf} cadastrada — a próxima captura periódica já vai incluí-la. "
          "Também dá para rodar a captura manualmente pelo servidor "
          "(\"python capturar_intimacoes_oab.py --oab " + str(oab.id) + "\").", "success")
    return redirect(url_for("captacao_oab.index"))


@captacao_oab_bp.route("/<int:oab_id>/alternar-ativo", methods=["POST"])
@login_required
def alternar_ativo(oab_id):
    oab = db.get_or_404(OabMonitorada, oab_id)
    checar_acesso_unidade_ou_403(oab.unidade_id)
    oab.ativo = not oab.ativo
    registrar_log(current_user, "ativou_oab_monitorada" if oab.ativo else "desativou_oab_monitorada",
                   "OabMonitorada", oab.id, f"{oab.numero}/{oab.uf}")
    db.session.commit()
    flash(f"OAB {oab.numero}/{oab.uf} {'reativada' if oab.ativo else 'desativada'}.", "success")
    return redirect(url_for("captacao_oab.index"))


@captacao_oab_bp.route("/triagem")
@login_required
def triagem():
    itens = (
        aplicar_escopo_unidade(IntimacaoCapturada.query, IntimacaoCapturada)
        .filter_by(status="pendente_triagem")
        .order_by(IntimacaoCapturada.criado_em.desc())
        .all()
    )
    return render_template("captacao_oab/triagem.html", itens=itens)


@captacao_oab_bp.route("/triagem/<int:intimacao_id>")
@login_required
def triagem_detalhe(intimacao_id):
    intimacao = db.get_or_404(IntimacaoCapturada, intimacao_id)
    checar_acesso_unidade_ou_403(intimacao.unidade_id)

    processos_candidatos = []
    if intimacao.numero_processo:
        processos_candidatos = [
            p for p in aplicar_escopo_unidade(Processo.query, Processo).all()
            if somente_digitos(p.numero_processo or "").endswith(intimacao.numero_processo[-6:])
        ] if len(intimacao.numero_processo) >= 6 else []

    return render_template("captacao_oab/triagem_detalhe.html", intimacao=intimacao,
                            processos_candidatos=processos_candidatos)


@captacao_oab_bp.route("/triagem/<int:intimacao_id>/vincular", methods=["POST"])
@login_required
def vincular(intimacao_id):
    intimacao = db.get_or_404(IntimacaoCapturada, intimacao_id)
    checar_acesso_unidade_ou_403(intimacao.unidade_id)
    if intimacao.status != "pendente_triagem":
        flash("Esta intimação já foi triada.", "warning")
        return redirect(url_for("captacao_oab.triagem"))

    processo_id = request.form.get("processo_id", type=int)
    processo = db.session.get(Processo, processo_id) if processo_id else None
    if processo is None:
        flash("Escolha um processo válido para vincular.", "danger")
        return redirect(url_for("captacao_oab.triagem_detalhe", intimacao_id=intimacao.id))
    checar_acesso_unidade_ou_403(processo.unidade_id)

    vincular_intimacao_a_processo(intimacao, processo, usuario=current_user)
    registrar_log(current_user, "vinculou_intimacao_capturada", "Processo", processo.id,
                   f"intimação #{intimacao.id}")
    db.session.commit()
    flash("Intimação vinculada ao processo — prazo gerado (confira na aba Prazos).", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@captacao_oab_bp.route("/triagem/<int:intimacao_id>/ignorar", methods=["POST"])
@login_required
def ignorar(intimacao_id):
    intimacao = db.get_or_404(IntimacaoCapturada, intimacao_id)
    checar_acesso_unidade_ou_403(intimacao.unidade_id)
    if intimacao.status != "pendente_triagem":
        flash("Esta intimação já foi triada.", "warning")
        return redirect(url_for("captacao_oab.triagem"))

    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Descreva o motivo de ignorar esta intimação (fica registrado no histórico).", "danger")
        return redirect(url_for("captacao_oab.triagem_detalhe", intimacao_id=intimacao.id))

    intimacao.status = "ignorada"
    intimacao.motivo_ignorada = motivo
    intimacao.vinculado_por_id = current_user.id
    intimacao.vinculado_em = datetime.utcnow()
    registrar_log(current_user, "ignorou_intimacao_capturada", "IntimacaoCapturada", intimacao.id, motivo)
    db.session.commit()
    flash("Intimação marcada como ignorada.", "info")
    return redirect(url_for("captacao_oab.triagem"))


# ---------- Push do tribunal, onde houver (item 1 — "mais push do tribunal onde houver") ----------
#
# ⚠️ Ver aviso completo em app/utils/conector_djen.py: nenhum tribunal
# brasileiro oferece hoje um padrão de push registrável para comunicações
# processuais — a API Comunica do CNJ é sempre consulta periódica
# (polling), que é o caminho principal (ver capturar_intimacoes_oab.py).
# Este endpoint existe PRONTO para quando/se algum tribunal específico
# oferecer isso a este escritório (alguns sistemas próprios de tribunal já
# têm webhook para outras finalidades) — mas não foi validado contra
# nenhum tribunal real. O formato do payload aceito é o "genérico" deste
# sistema (mesmos nomes de campo do resto do captura_djen_pipeline); se o
# tribunal específico exigir outro formato, ajuste o mapeamento abaixo.

@csrf.exempt  # chamador é o sistema do tribunal (sem cookie de sessão nosso) —
              # autenticado pelo token secreto na própria URL, não por CSRF/sessão
              # (mesmo raciocínio de api_integracao_bp/agente_local_api_bp em app/__init__.py).
@captacao_oab_bp.route("/webhook/<token>", methods=["POST"])
def webhook_comunicacao(token):
    oab = OabMonitorada.query.filter_by(token_webhook=token, ativo=True).first()
    if oab is None:
        abort(404)

    corpo = request.get_json(silent=True) or {}
    id_fonte = str(corpo.get("id_comunicacao") or corpo.get("id") or corpo.get("hash") or "")
    if not id_fonte:
        return jsonify(erro="Payload sem identificador único da comunicação (id_comunicacao/id/hash)."), 400

    from app.utils.conector_djen import _parse_data as parse_data_djen  # reaproveita o mesmo parser tolerante
    from app.utils.prazos_engine import proxima_data_util

    data_disp = parse_data_djen(corpo.get("data_disponibilizacao"))
    tribunal = corpo.get("tribunal") or corpo.get("siglaTribunal")
    data_pub = proxima_data_util(data_disp, tribunal=tribunal) if data_disp else None

    publicacao_capturada = PublicacaoCapturada(
        diario="DJEN", data_disponibilizacao=data_disp, data_publicacao=data_pub,
        teor=corpo.get("texto") or "", oab_destinataria=f"{oab.numero}/{oab.uf}",
        hash_dedup=id_fonte,
        numero_processo=somente_digitos(corpo.get("numero_processo") or ""),
        numero_processo_mascara=corpo.get("numero_processo_mascara"),
        tribunal=tribunal, orgao=corpo.get("orgao"),
        tipo_comunicacao=corpo.get("tipo_comunicacao"), tipo_documento=corpo.get("tipo_documento"),
        meio=corpo.get("meio"), destinatarios_texto=corpo.get("destinatarios_texto"),
        link_certidao=corpo.get("link_certidao"), id_comunicacao_fonte=id_fonte,
    )
    intimacao = processar_publicacao_capturada(oab, publicacao_capturada, origem="push_tribunal")
    db.session.commit()
    if intimacao is None:
        return jsonify(status="duplicada"), 200
    return jsonify(status="recebida", intimacao_status=intimacao.status), 201
