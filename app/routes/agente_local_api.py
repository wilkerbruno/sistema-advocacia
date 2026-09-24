"""
API que o Agente Local (agente_local_jc/, roda na máquina do próprio
advogado — ver app/models/agente_local.py) usa para: (1) confirmar que
o pareamento (token) ainda é válido a cada polling, (2) buscar as
solicitações de busca de autos pendentes DO PRÓPRIO ADVOGADO dono deste
agente, (3) devolver o resultado (PDF) ou um erro de uma solicitação, e
(4) conferir o código do autenticador (2FA) da conta antes do agente
abrir a própria tela de Configuração (`status_autenticador`/
`verificar_autenticador` abaixo — pedido do usuário, ver
app/models/agente_local.py para a explicação completa das três camadas
de credencial envolvidas neste recurso).

Autenticação: header `Authorization: Bearer <token>` — token gerado na
tela /agente-local (ver app/routes/agente_local.py) e validado por
AgenteLocalPareado.validar. Mesmo padrão de api_integracao.py (token
hasheado, nunca guardado em texto puro), mas por USUÁRIO, não por
empresa: o agente só decide autenticar no tribunal com o certificado do
PRÓPRIO usuário que o instalou, então só pode enxergar/atender pedidos
desse usuário — nunca de outro advogado, mesmo da mesma empresa.

⚠️ Este endpoint só RECEBE o resultado já pronto que o agente local
manda — nenhuma chamada a nenhum tribunal (MNI/SOAP ou outro protocolo)
acontece aqui, nem em nenhum outro lugar do servidor. Ver
app/models/agente_local.py e agente_local_jc/README.md para o desenho
completo, e para o aviso de que a integração de verdade com o tribunal
ainda não foi validada contra nenhum ambiente real — só quem já rodou
esse piloto com credencial/certificado de teste pode confiar no
resultado que este endpoint recebe.
"""
import os
import uuid
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, g, current_app, abort
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import AgenteLocalPareado, SolicitacaoBuscaAutos, Documento
from app.utils.notificacoes import registrar_log
from app.utils.fila import enfileirar
from app.utils import totp as totp_utils

agente_local_api_bp = Blueprint("agente_local_api", __name__)

# Trava de tentativas do autenticador pedida pela tela de Configuração do
# agente local — mesmos números do login web (app/routes/auth.py::
# _MAX_TENTATIVAS_TOTP_LOGIN), por consistência: 5 tentativas erradas
# bloqueiam por 5 minutos. Ver AgenteLocalPareado.registrar_falha_totp.
_MAX_TENTATIVAS_TOTP_AGENTE = 5
_MINUTOS_BLOQUEIO_TOTP_AGENTE = 5


def exige_agente(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        valor_recebido = auth[7:] if auth.startswith("Bearer ") else None

        agente = AgenteLocalPareado.validar(valor_recebido)
        if agente is None:
            return jsonify(erro="Token de agente local inválido, ausente ou revogado."), 401

        agente.ultimo_contato_em = datetime.utcnow()
        db.session.commit()
        g.agente_local = agente
        return f(*args, **kwargs)
    return decorado


def _tarefa_do_agente_ou_404(tarefa_id):
    """Só devolve a solicitação se ela pertencer ao MESMO usuário dono do
    agente autenticado nesta requisição — nunca deixa um agente ver ou
    responder tarefa de outro advogado."""
    tarefa = db.session.get(SolicitacaoBuscaAutos, tarefa_id)
    if tarefa is None or tarefa.solicitado_por_id != g.agente_local.usuario_id:
        abort(404)
    return tarefa


@agente_local_api_bp.route("/ping")
@exige_agente
def ping():
    """Usado pelo agente local só para confirmar que o token ainda é
    válido e medir latência — não devolve nem recebe nada além disso."""
    return jsonify(ok=True, usuario=g.agente_local.usuario.nome, apelido=g.agente_local.apelido)


@agente_local_api_bp.route("/status-autenticador")
@exige_agente
def status_autenticador():
    """
    Usado pela tela de Configuração do agente local (agente_local_jc/
    autenticador_local.py) pra decidir, ANTES de pedir um código, se vale
    a pena pedir alguma coisa: se este sistema não tem a funcionalidade
    de autenticador ligada (`TOTP_CIFRA_KEY` não configurada — ver
    app/utils/totp.py) OU se o próprio usuário dono deste agente ainda
    não confirmou o autenticador da conta dele, não faz sentido o agente
    local exigir algo que a própria conta web ainda não exige pra logar
    — a Configuração abre direto nesse caso.
    """
    usuario = g.agente_local.usuario
    exigido = bool(totp_utils.totp_disponivel() and usuario.totp_configurado)
    return jsonify(exigido=exigido)


@agente_local_api_bp.route("/verificar-autenticador", methods=["POST"])
@exige_agente
def verificar_autenticador():
    """
    Confere o código de 6 dígitos do app autenticador contra o MESMO
    segredo TOTP já configurado na conta JusControl deste usuário (nunca
    um segredo separado só pro agente) — pedido do usuário: "para o cara
    acessar a configuração do aplicativo ele tem que autenticar com
    autenticador igual funciona hoje para logar no sistema web". O
    servidor já sabe de qual usuário é o pedido através do próprio token
    de pareamento (Bearer, ver `exige_agente`) — o agente local nunca
    precisa mandar e-mail nem senha de novo pra isso.

    Se a conta não tem autenticador exigível agora (ver
    `status_autenticador` acima), devolve sucesso direto — mesma lógica,
    nunca duplicada.
    """
    agente = g.agente_local
    usuario = agente.usuario

    if not (totp_utils.totp_disponivel() and usuario.totp_configurado):
        return jsonify(ok=True, exigido=False)

    if agente.totp_bloqueado():
        restante_segundos = max(1, int((agente.totp_bloqueado_ate - datetime.utcnow()).total_seconds()))
        return jsonify(
            ok=False,
            erro=f"Muitas tentativas erradas — tente de novo em {restante_segundos} segundo(s).",
        ), 429

    codigo = (request.get_json(silent=True) or {}).get("codigo", "")
    secret = totp_utils.obter_secret_pendente_ou_confirmado(usuario)

    if secret and totp_utils.verificar_codigo(secret, codigo):
        agente.resetar_falhas_totp()
        db.session.commit()
        return jsonify(ok=True)

    agente.registrar_falha_totp(max_tentativas=_MAX_TENTATIVAS_TOTP_AGENTE,
                                 minutos_bloqueio=_MINUTOS_BLOQUEIO_TOTP_AGENTE)
    db.session.commit()
    return jsonify(ok=False, erro="Código inválido — confira o app autenticador e tente de novo."), 401


@agente_local_api_bp.route("/tarefas")
@exige_agente
def listar_tarefas():
    pendentes = (
        SolicitacaoBuscaAutos.query
        .filter_by(solicitado_por_id=g.agente_local.usuario_id, status="pendente")
        .order_by(SolicitacaoBuscaAutos.criado_em)
        .all()
    )
    return jsonify(tarefas=[{
        "id": s.id,
        "processo_id": s.processo_id,
        "numero_processo": s.numero_processo_solicitado,
        "tribunal_conector": s.tribunal_conector,
        "criado_em": s.criado_em.isoformat() if s.criado_em else None,
    } for s in pendentes])


@agente_local_api_bp.route("/tarefas/<int:tarefa_id>/iniciar", methods=["POST"])
@exige_agente
def iniciar_tarefa(tarefa_id):
    tarefa = _tarefa_do_agente_ou_404(tarefa_id)
    if tarefa.status != "pendente":
        return jsonify(erro="Esta solicitação não está mais pendente."), 409
    tarefa.iniciar(g.agente_local)
    db.session.commit()
    return jsonify(ok=True)


@agente_local_api_bp.route("/tarefas/<int:tarefa_id>/resultado", methods=["POST"])
@exige_agente
def enviar_resultado(tarefa_id):
    """Recebe o PDF dos autos completos já baixado pelo agente local e
    cria um Documento normal do processo (mesma tabela/pasta de upload
    manual — ver app/routes/processos.py:add_documento), marcando a
    solicitação como concluída."""
    tarefa = _tarefa_do_agente_ou_404(tarefa_id)
    if tarefa.status not in SolicitacaoBuscaAutos.STATUS_ABERTOS:
        return jsonify(erro="Esta solicitação já foi concluída, cancelada ou marcada com erro."), 409

    arquivo = request.files.get("arquivo")
    if not arquivo or arquivo.filename == "":
        return jsonify(erro="Envie o PDF dos autos completos no campo 'arquivo' (multipart/form-data)."), 400
    if not arquivo.filename.lower().endswith(".pdf"):
        return jsonify(erro="Só arquivo PDF é aceito neste endpoint."), 400

    nome_original = secure_filename(arquivo.filename) or "autos_completos.pdf"
    nome_salvo = f"{uuid.uuid4().hex}.pdf"
    pasta_processo = os.path.join(current_app.config["UPLOAD_FOLDER"], str(tarefa.processo_id))
    os.makedirs(pasta_processo, exist_ok=True)
    caminho_completo = os.path.join(pasta_processo, nome_salvo)
    arquivo.save(caminho_completo)

    doc = Documento(
        processo_id=tarefa.processo_id,
        nome_original=nome_original,
        nome_arquivo=nome_salvo,
        categoria="autos_completo_agente_local",
        tamanho_kb=round(os.path.getsize(caminho_completo) / 1024),
        enviado_por_id=tarefa.solicitado_por_id,
    )
    db.session.add(doc)
    db.session.flush()  # doc.id disponível antes do commit final

    tarefa.concluir(doc)
    registrar_log(tarefa.solicitado_por, "agente_local_entregou_autos", "Processo",
                   tarefa.processo_id, f"{nome_original} (conector: {tarefa.tribunal_conector})")
    db.session.commit()
    # Indexação em segundo plano (item 3 — PENDENCIAS.md, seção -103) — os
    # autos completos trazidos pelo Agente Local são exatamente o caso de
    # uso central do item ("dez mil páginas"): nunca roda dentro desta
    # requisição, que é o próprio agente do advogado esperando resposta.
    enfileirar("app.jobs.indexacao_jobs.indexar_documento_job", doc.id)
    return jsonify(ok=True, documento_id=doc.id)


@agente_local_api_bp.route("/tarefas/<int:tarefa_id>/erro", methods=["POST"])
@exige_agente
def reportar_erro(tarefa_id):
    tarefa = _tarefa_do_agente_ou_404(tarefa_id)
    if tarefa.status not in SolicitacaoBuscaAutos.STATUS_ABERTOS:
        return jsonify(erro="Esta solicitação já foi concluída, cancelada ou marcada com erro."), 409

    corpo_json = request.get_json(silent=True) or {}
    mensagem = corpo_json.get("mensagem") or request.form.get("mensagem") or "Erro não especificado pelo agente local."
    tarefa.falhar(mensagem)
    registrar_log(tarefa.solicitado_por, "agente_local_reportou_erro", "Processo", tarefa.processo_id, mensagem[:255])
    db.session.commit()
    return jsonify(ok=True)
