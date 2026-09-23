"""
Vigilância do Diário Oficial da União (DOU) — pedido do usuário (2026-09):
"seria possível a gente vincular o diário oficial da união no sistema".
Ver PENDENCIAS.md (seção mais recente) para a pesquisa completa e
app/models/captacao_dou.py para o porquê disto nunca vincular a um
Processo (diferente de captacao_oab.py — DJEN é intimação processual, DOU
é diário do governo federal).

Uma tela só: cadastro de palavras-chave extras (o monitoramento por nome/
CNPJ de cliente é automático, não precisa de tela) + lista do que foi
capturado, pra revisão humana (marcar como lida, ou ignorar com motivo).
"""
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import PalavraChaveDou, PublicacaoDouCapturada
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo
from app.utils.notificacoes import registrar_log
from app.utils.conector_inlabs_dou import login_configurado

captacao_dou_bp = Blueprint("captacao_dou", __name__, url_prefix="/captacao-dou")


@captacao_dou_bp.route("/")
@login_required
def index():
    palavras = aplicar_escopo_unidade(PalavraChaveDou.query, PalavraChaveDou) \
        .order_by(PalavraChaveDou.criado_em.desc()).all()
    capturadas = aplicar_escopo_unidade(PublicacaoDouCapturada.query, PublicacaoDouCapturada) \
        .filter_by(status="pendente_revisao") \
        .order_by(PublicacaoDouCapturada.data_publicacao.desc(), PublicacaoDouCapturada.criado_em.desc()) \
        .limit(200).all()
    total_ja_revisadas = aplicar_escopo_unidade(PublicacaoDouCapturada.query, PublicacaoDouCapturada) \
        .filter(PublicacaoDouCapturada.status != "pendente_revisao").count()
    unidades = unidades_do_escopo() if current_user.is_admin else None
    return render_template(
        "captacao_dou/index.html", palavras=palavras, capturadas=capturadas,
        total_ja_revisadas=total_ja_revisadas, configurado=login_configurado(),
        unidades=unidades,
    )


@captacao_dou_bp.route("/palavra-chave/nova", methods=["POST"])
@login_required
def nova_palavra_chave():
    termo = (request.form.get("termo") or "").strip()
    if not termo:
        flash("Informe uma palavra-chave.", "danger")
        return redirect(url_for("captacao_dou.index"))

    unidade_id = unidade_id_para_novo_registro()
    checar_acesso_unidade_ou_403(unidade_id)

    if PalavraChaveDou.query.filter_by(unidade_id=unidade_id, termo=termo).first():
        flash(f'"{termo}" já está cadastrada nesta unidade.', "warning")
        return redirect(url_for("captacao_dou.index"))

    palavra = PalavraChaveDou(unidade_id=unidade_id, termo=termo, criado_por_id=current_user.id)
    db.session.add(palavra)
    registrar_log(current_user, "cadastrou_palavra_chave_dou", "PalavraChaveDou", None, termo)
    db.session.commit()
    flash(f'"{termo}" cadastrada — a próxima captura diária do DOU já passa a considerar este termo.', "success")
    return redirect(url_for("captacao_dou.index"))


@captacao_dou_bp.route("/palavra-chave/<int:palavra_id>/alternar-ativo", methods=["POST"])
@login_required
def alternar_ativo_palavra_chave(palavra_id):
    palavra = db.get_or_404(PalavraChaveDou, palavra_id)
    checar_acesso_unidade_ou_403(palavra.unidade_id)
    palavra.ativa = not palavra.ativa
    registrar_log(current_user, "ativou_palavra_chave_dou" if palavra.ativa else "desativou_palavra_chave_dou",
                   "PalavraChaveDou", palavra.id, palavra.termo)
    db.session.commit()
    flash(f'"{palavra.termo}" {"reativada" if palavra.ativa else "desativada"}.', "success")
    return redirect(url_for("captacao_dou.index"))


@captacao_dou_bp.route("/<int:publicacao_id>/marcar-lida", methods=["POST"])
@login_required
def marcar_lida(publicacao_id):
    publicacao = db.get_or_404(PublicacaoDouCapturada, publicacao_id)
    checar_acesso_unidade_ou_403(publicacao.unidade_id)
    if publicacao.status != "pendente_revisao":
        flash("Esta publicação já foi revisada.", "warning")
        return redirect(url_for("captacao_dou.index"))

    publicacao.status = "lida"
    publicacao.revisado_por_id = current_user.id
    publicacao.revisado_em = datetime.utcnow()
    registrar_log(current_user, "marcou_lida_publicacao_dou", "PublicacaoDouCapturada", publicacao.id,
                   publicacao.termo_encontrado)
    db.session.commit()
    flash("Marcada como lida.", "success")
    return redirect(url_for("captacao_dou.index"))


@captacao_dou_bp.route("/<int:publicacao_id>/ignorar", methods=["POST"])
@login_required
def ignorar(publicacao_id):
    publicacao = db.get_or_404(PublicacaoDouCapturada, publicacao_id)
    checar_acesso_unidade_ou_403(publicacao.unidade_id)
    if publicacao.status != "pendente_revisao":
        flash("Esta publicação já foi revisada.", "warning")
        return redirect(url_for("captacao_dou.index"))

    motivo = (request.form.get("motivo") or "").strip()
    if not motivo:
        flash("Descreva o motivo de ignorar esta publicação (ex.: homônimo, não é o mesmo cliente).", "danger")
        return redirect(url_for("captacao_dou.index"))

    publicacao.status = "ignorada"
    publicacao.motivo_ignorada = motivo
    publicacao.revisado_por_id = current_user.id
    publicacao.revisado_em = datetime.utcnow()
    registrar_log(current_user, "ignorou_publicacao_dou", "PublicacaoDouCapturada", publicacao.id, motivo)
    db.session.commit()
    flash("Publicação marcada como ignorada.", "info")
    return redirect(url_for("captacao_dou.index"))
