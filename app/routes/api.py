from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Notificacao, Cliente, Processo
from app.utils.acesso import aplicar_escopo_unidade
from app.utils.cep import consultar_cep, CepInvalidoError, CepNaoEncontradoError

api_bp = Blueprint("api", __name__)


@api_bp.route("/cep/<cep>")
@login_required
def cep(cep):
    """Autofill de endereço a partir do CEP (ViaCEP) — usado no formulário
    de cliente (app/templates/clientes/form.html). Ver app/utils/cep.py."""
    try:
        dados = consultar_cep(cep)
    except CepInvalidoError as e:
        return jsonify(erro=str(e)), 400
    except CepNaoEncontradoError as e:
        return jsonify(erro=str(e)), 404
    return jsonify(dados)


@api_bp.route("/notificacoes")
@login_required
def notificacoes():
    itens = Notificacao.query.filter_by(usuario_id=current_user.id) \
        .order_by(Notificacao.criado_em.desc()).limit(15).all()
    return jsonify([
        dict(id=n.id, titulo=n.titulo, mensagem=n.mensagem, tipo=n.tipo,
             link=n.link, lida=n.lida, criado_em=n.criado_em.strftime("%d/%m %H:%M"))
        for n in itens
    ])


@api_bp.route("/notificacoes/<int:notif_id>/marcar-lida", methods=["POST"])
@login_required
def marcar_lida(notif_id):
    notif = db.get_or_404(Notificacao, notif_id)
    if notif.usuario_id != current_user.id:
        return jsonify(erro="não autorizado"), 403
    notif.lida = True
    db.session.commit()
    return jsonify(ok=True)


@api_bp.route("/busca-rapida")
@login_required
def busca_rapida():
    termo = request.args.get("q", "").strip()
    if len(termo) < 2:
        return jsonify(clientes=[], processos=[])

    like = f"%{termo}%"
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter(Cliente.nome.ilike(like)).limit(5).all()
    processos = aplicar_escopo_unidade(Processo.query, Processo).filter(
        db.or_(Processo.numero_processo.ilike(like), Processo.numero_interno.ilike(like))
    ).limit(5).all()

    return jsonify(
        clientes=[dict(id=c.id, nome=c.nome) for c in clientes],
        processos=[dict(id=p.id, numero=p.numero_processo or p.numero_interno) for p in processos],
    )
