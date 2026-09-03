"""
Tela "Meu agente local" — cada advogado gera e revoga, para si mesmo,
os pareamentos do Agente Local (agente_local_jc/, roda na própria
máquina do advogado — ver app/models/agente_local.py e PENDENCIAS.md
seção -56) usados para buscar autos completos de processos com o
próprio certificado digital, sem que o certificado nunca chegue ao
servidor da JusControl.

Pessoal, não administrativo: qualquer usuário logado (não só admin/
gestor) pode gerenciar o(s) próprio(s) pareamento(s) — o certificado
digital é do advogado, não do escritório, então só ele decide instalar
o agente e em qual máquina.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import AgenteLocalPareado
from app.utils.notificacoes import registrar_log

agente_local_bp = Blueprint("agente_local", __name__)


@agente_local_bp.route("/agente-local")
@login_required
def meu_agente():
    pareamentos = (
        AgenteLocalPareado.query.filter_by(usuario_id=current_user.id)
        .order_by(AgenteLocalPareado.criado_em.desc()).all()
    )
    return render_template("agente_local/meu_agente.html", pareamentos=pareamentos, token_novo=None)


@agente_local_bp.route("/agente-local/parear", methods=["POST"])
@login_required
def parear():
    apelido = request.form.get("apelido", "").strip() or "Agente local"
    registro, valor_puro = AgenteLocalPareado.emitir_para(current_user, apelido)
    registrar_log(current_user, "pareou_agente_local", "AgenteLocalPareado", None, apelido)
    db.session.commit()

    pareamentos = (
        AgenteLocalPareado.query.filter_by(usuario_id=current_user.id)
        .order_by(AgenteLocalPareado.criado_em.desc()).all()
    )
    flash("Agente pareado — copie o token abaixo agora, ele não vai aparecer de novo.", "success")
    return render_template("agente_local/meu_agente.html", pareamentos=pareamentos, token_novo=valor_puro,
                            registro_novo_id=registro.id)


@agente_local_bp.route("/agente-local/<int:pareamento_id>/revogar", methods=["POST"])
@login_required
def revogar(pareamento_id):
    registro = db.get_or_404(AgenteLocalPareado, pareamento_id)
    if registro.usuario_id != current_user.id:
        flash("Você só pode revogar os próprios pareamentos.", "danger")
        return redirect(url_for("agente_local.meu_agente"))

    registro.revogar()
    registrar_log(current_user, "revogou_agente_local", "AgenteLocalPareado", registro.id, registro.apelido)
    db.session.commit()
    flash(f"Pareamento “{registro.apelido}” revogado — esse agente não vai mais conseguir buscar autos.", "info")
    return redirect(url_for("agente_local.meu_agente"))
