from datetime import datetime
from flask import request
from app.extensions import db
from app.models import LogAtividade, Notificacao
from app.utils.rede import obter_mac_por_ip


def registrar_log(usuario, acao, entidade, entidade_id=None, detalhes=None):
    ip = request.remote_addr if request else None
    log = LogAtividade(
        usuario_id=usuario.id if usuario else None,
        unidade_id=getattr(usuario, "unidade_id", None),
        acao=acao,
        entidade=entidade,
        entidade_id=entidade_id,
        detalhes=detalhes,
        ip=ip,
        mac_address=obter_mac_por_ip(ip) if ip else None,
    )
    db.session.add(log)


def notificar(usuario_id, titulo, mensagem=None, tipo="info", link=None):
    notif = Notificacao(usuario_id=usuario_id, titulo=titulo, mensagem=mensagem, tipo=tipo, link=link)
    db.session.add(notif)
    return notif


def contar_notificacoes_nao_lidas(usuario):
    if not usuario or not usuario.is_authenticated:
        return 0
    return Notificacao.query.filter_by(usuario_id=usuario.id, lida=False).count()
