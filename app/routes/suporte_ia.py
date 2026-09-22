"""
Chat de suporte flutuante — pedido explícito do usuário: "quero que tenha
um botão flutuante com icone de suporte com um chat aonde o cliente
pergunta algo sobre o sistema e uma ia que responde tudo sobre o
juscontrol, como tudo funciona, e tudo mais".

Visível para QUALQUER usuário logado (decisão explícita do usuário, não
restrito a admin/gestor) — ver o botão/painel em
app/templates/_suporte_widget.html, incluído uma vez em base.html.

Diferente do Agente de IA de portfólio (app/routes/agente_ia.py): não
existe aqui uma "conversa" persistida que o usuário reabre depois — o
histórico de uma sessão do widget vive só na memória do navegador
(reenviado a cada pergunta nova pra dar contexto, ver `historico` abaixo)
e cada pergunta grava só UMA linha (MensagemSuporteIA), sem vínculo entre
si no banco — é auditoria/observabilidade, não uma tela de histórico.
Também não tem ferramentas (tool-calling) nem contexto pré-carregado: o
agente de suporte só conhece o CONTEÚDO DE AJUDA estático (ver
app/utils/juscontrol_manual.py), nunca dado real do escritório de quem
pergunta.
"""
from flask import Blueprint, request, jsonify, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.models import MensagemSuporteIA
from app.utils import suporte_ia
from app.utils.fila import enfileirar

suporte_ia_bp = Blueprint("suporte_ia", __name__)

MAX_TAMANHO_PERGUNTA = 2000


@suporte_ia_bp.route("/perguntar", methods=["POST"])
@login_required
def perguntar():
    corpo = request.get_json(silent=True) or {}
    pergunta = (corpo.get("pergunta") or "").strip()
    historico = corpo.get("historico") or []

    if not pergunta:
        return jsonify(erro="Digite uma pergunta."), 400
    if len(pergunta) > MAX_TAMANHO_PERGUNTA:
        return jsonify(erro="Pergunta muito longa — tente resumir."), 400
    if not isinstance(historico, list):
        historico = []

    mensagem = MensagemSuporteIA(usuario_id=current_user.id, pergunta=pergunta, status="processando")
    db.session.add(mensagem)
    db.session.commit()

    system = suporte_ia.montar_system_prompt(pergunta)
    mensagens_api = suporte_ia.montar_mensagens_api(pergunta, historico=historico)

    enfileirar(
        "app.jobs.ia_jobs.processar_mensagem_suporte_ia",
        mensagem.id, current_user.empresa_id_atual, system, mensagens_api,
        job_timeout=420,
    )

    return jsonify(mensagem_id=mensagem.id)


@suporte_ia_bp.route("/mensagens/<int:mensagem_id>/status")
@login_required
def status_mensagem(mensagem_id):
    mensagem = db.get_or_404(MensagemSuporteIA, mensagem_id)
    if mensagem.usuario_id != current_user.id:
        abort(403)
    pronta = (mensagem.status or "pronta") != "processando"
    return jsonify(
        status=mensagem.status or "pronta",
        resposta=mensagem.resposta if pronta else None,
    )
