"""
Agentes de IA jurídica (item 6 do briefing de paridade): três personas —
Operação (advogados), Gestão (controller) e Negócios (sócios) — cada uma
com um system prompt próprio e um "contexto atual do escritório" real,
montado a partir dos dados do banco no escopo do usuário logado (nunca
dados de outra empresa/unidade), injetado a cada mensagem.

Isso é o que separa isso de um chatbot genérico: as respostas são
embasadas em números reais do momento da pergunta (prazos vencendo,
processos parados, receita pendente etc.), não em nada inventado.

Motor: por padrão, modelo de IA local (até 2B parâmetros, ver
app/utils/ia_local.py), rodando dentro do próprio servidor — sem chave de
API, sem custo por mensagem, sem dado saindo do servidor. Desde a rodada
BYOK, cada empresa pode escolher em "Minhas Integrações"
(app/routes/integracoes.py) usar a API do Claude com a PRÓPRIA chave da
Anthropic no lugar do modelo local — ver app/utils/agente_ia_router.py,
que decide qual dos dois usar sem este arquivo precisar saber a
diferença. Em qualquer um dos dois casos, sem o provedor pronto (modelo
não baixado, ou chave Claude não cadastrada/inválida), o agente responde
de forma honesta que está indisponível — nunca finge uma resposta nem
trava a tela.
"""
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import ConversaAgenteIA, MensagemAgenteIA, Processo, Prazo, Tarefa, Cliente, Lancamento
from app.utils.acesso import aplicar_escopo_unidade
from app.utils.notificacoes import registrar_log
from app.utils import agente_ia_router
from app.utils.fila import enfileirar

agente_ia_bp = Blueprint("agente_ia", __name__)

# Limite de mensagens do histórico enviadas a cada chamada. Menor que o
# usado com API grande de propósito: o modelo local roda com uma janela de
# contexto menor (ver IA_LOCAL_CONTEXT_SIZE) e é mais lento por token, então
# um histórico grande deixaria a resposta lenta ou estouraria o contexto.
MAX_MENSAGENS_HISTORICO = 12

PERSONA_CONFIG = {
    "operacao": {
        "titulo": "Agente de Operação",
        "publico": "advogados",
        "descricao": "Pesquisa, resumos, organização de fluxo e apoio à produção jurídica do dia a dia.",
        "system": (
            "Você é o assistente interno de operação jurídica de um escritório de advocacia, "
            "voltado para advogados no dia a dia: ajuda a resumir andamentos, organizar prazos e "
            "tarefas, e sinalizar riscos práticos de agenda. Responda sempre em português do Brasil, "
            "de forma direta e objetiva. Você NÃO é advogado e não substitui a análise jurídica "
            "humana — sempre que a pergunta exigir uma opinião de mérito (interpretação de lei, "
            "estratégia processual, chance de êxito), deixe claro que é uma sugestão a ser validada "
            "pelo advogado responsável, nunca uma conclusão definitiva."
        ),
    },
    "gestao": {
        "titulo": "Agente de Gestão",
        "publico": "controller jurídico",
        "descricao": "Monitoramento de prazos, controle operacional e governança de dados da carteira.",
        "system": (
            "Você é o assistente interno de controladoria jurídica de um escritório de advocacia, "
            "voltado para o controller/gestor: ajuda a identificar risco de prazo, gargalos "
            "operacionais, processos parados e sugerir ações de governança. Responda sempre em "
            "português do Brasil, de forma direta. Use SEMPRE os números do contexto fornecido "
            "abaixo (dados reais do escritório) — nunca invente estatística ou número que não "
            "esteja nesse contexto; se a pergunta exigir um dado que não está lá, diga isso "
            "explicitamente em vez de estimar."
        ),
    },
    "negocios": {
        "titulo": "Agente de Negócios",
        "publico": "sócios",
        "descricao": "Leitura da carteira, oportunidades de crescimento e inteligência comercial.",
        "system": (
            "Você é o assistente interno de inteligência de negócios de um escritório de advocacia, "
            "voltado para os sócios: ajuda a ler a composição da carteira de clientes, identificar "
            "concentração de risco comercial, receita pendente e possíveis oportunidades de "
            "crescimento. Responda sempre em português do Brasil, de forma direta. Use SEMPRE os "
            "números do contexto fornecido abaixo (dados reais do escritório) — nunca invente "
            "estatística, previsão ou probabilidade que não esteja embasada nesse contexto; se a "
            "pergunta exigir um dado que não está lá, diga isso explicitamente em vez de estimar."
        ),
    },
}


# ---------------------- Contexto real por persona ----------------------

def _escopo_processos():
    return aplicar_escopo_unidade(Processo.query, Processo)


def _escopo_prazos():
    query = Prazo.query.join(Processo).filter(Prazo.deletado_em.is_(None))
    if not current_user.is_admin:
        query = query.filter(Processo.unidade_id == current_user.unidade_id)
    return query


def _contexto_operacao():
    hoje = date.today()
    prazos_vencendo = (
        _escopo_prazos()
        .filter(Prazo.status == "pendente", Prazo.data_vencimento <= hoje + timedelta(days=7))
        .order_by(Prazo.data_vencimento).limit(10).all()
    )
    linhas = [
        f"- \"{p.descricao}\" (processo {p.processo.numero_processo or p.processo.numero_interno or ('#' + str(p.processo_id))}), "
        f"vence em {p.data_vencimento.strftime('%d/%m/%Y')}, status atual: {p.status}."
        for p in prazos_vencendo
    ]
    tarefas_atrasadas = aplicar_escopo_unidade(Tarefa.query, Tarefa).filter(
        Tarefa.status.in_(["pendente", "em_andamento"]),
        Tarefa.data_vencimento.isnot(None), Tarefa.data_vencimento < hoje,
    ).count()

    return (
        f"Prazos vencendo nos próximos 7 dias (até 10 listados):\n"
        + ("\n".join(linhas) if linhas else "nenhum prazo vencendo nesse período.")
        + f"\n\nTarefas atrasadas no escopo deste usuário: {tarefas_atrasadas}."
    )


def _contexto_gestao():
    processos_q = _escopo_processos()
    ativos = processos_q.filter(Processo.status == "ativo").count()
    limite_30 = datetime.utcnow() - timedelta(days=30)
    parados_30 = processos_q.filter(
        Processo.status == "ativo",
        (Processo.ultima_movimentacao_em.is_(None)) | (Processo.ultima_movimentacao_em <= limite_30),
    ).count()

    prazos_q = _escopo_prazos()
    cumpridos = prazos_q.filter(Prazo.status == "cumprido").count()
    perdidos = prazos_q.filter(Prazo.status == "perdido").count()
    finalizados = cumpridos + perdidos
    taxa = (cumpridos / finalizados * 100) if finalizados else None

    processos_nao_monitoraveis = processos_q.filter(Processo.monitoravel.is_(False)).count()

    return (
        f"Processos ativos: {ativos}.\n"
        f"Processos ativos parados há mais de 30 dias sem movimentação: {parados_30}.\n"
        f"Taxa de cumprimento de prazo (histórico): "
        f"{f'{taxa:.1f}%' if taxa is not None else 'sem dado suficiente ainda'} "
        f"({cumpridos} cumpridos / {perdidos} perdidos, de {finalizados} finalizados).\n"
        f"Processos marcados como não monitoráveis automaticamente: {processos_nao_monitoraveis}."
    )


def _contexto_negocios():
    processos_q = _escopo_processos()
    total = processos_q.count()
    por_area = dict(
        processos_q.with_entities(Processo.area_direito, func.count(Processo.id))
        .group_by(Processo.area_direito).all()
    )
    total_clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter(Cliente.ativo.is_(True)).count()

    a_receber = aplicar_escopo_unidade(Lancamento.query, Lancamento).filter(
        Lancamento.natureza == "receita", Lancamento.status == "pendente",
    ).with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()

    atrasado = aplicar_escopo_unidade(Lancamento.query, Lancamento).filter(
        Lancamento.natureza == "receita", Lancamento.status == "pendente",
        Lancamento.data_vencimento < date.today(),
    ).with_entities(func.coalesce(func.sum(Lancamento.valor), 0)).scalar()

    return (
        f"Carteira: {total} processo(s), {total_clientes} cliente(s) ativo(s).\n"
        f"Distribuição por área do direito: {por_area or 'sem processos cadastrados ainda'}.\n"
        f"Receita a receber (pendente): R$ {a_receber}.\n"
        f"Dessa receita pendente, já vencida (atrasada): R$ {atrasado}."
    )


_CONTEXTO_POR_PERSONA = {
    "operacao": _contexto_operacao,
    "gestao": _contexto_gestao,
    "negocios": _contexto_negocios,
}


# ---------------------- Montagem do pedido (rápido — só leitura de banco) ----------------------
#
# Separado da chamada ao modelo de propósito: isto aqui roda dentro da
# requisição web normal (é rápido, só monta texto a partir de dados já no
# banco), mas a chamada ao modelo em si (lenta, pode levar minutos por CPU)
# roda em segundo plano, num worker separado (ver app/jobs/ia_jobs.py e
# PENDENCIAS.md, seção -32) — por isso a montagem do "system" e do
# histórico formatado precisa terminar ANTES de enfileirar o job, já que o
# worker não tem acesso a current_user/à sessão de quem perguntou.

def _montar_system_e_mensagens(persona, mensagens_historico, contexto_dados):
    system = (
        PERSONA_CONFIG[persona]["system"]
        + "\n\nContexto atual do escritório (dados reais, consultados no momento desta mensagem — "
          "use-os para embasar a resposta, nunca invente número diferente destes):\n"
        + contexto_dados
    )

    mensagens_api = [
        {"role": m.papel, "content": m.conteudo}
        for m in mensagens_historico[-MAX_MENSAGENS_HISTORICO:]
    ]

    return system, mensagens_api


# ---------------------- Rotas ----------------------

@agente_ia_bp.route("/")
@login_required
def index():
    conversas = ConversaAgenteIA.query.filter_by(usuario_id=current_user.id) \
        .order_by(ConversaAgenteIA.atualizado_em.desc()).all()
    configurado = agente_ia_router.provedor_disponivel(current_user.empresa)
    provedor_texto = agente_ia_router.descricao_provedor(current_user.empresa)
    # Persona "negócios" expõe receita a receber/atrasada (ver
    # _contexto_negocios) — não mostra o cartão de "nova conversa" pra
    # quem não tem acesso financeiro (PENDENCIAS.md, seção -45).
    # `personas` (completo) continua indo pro template porque a tabela de
    # "Suas conversas" precisa achar o título de conversas antigas
    # (inclusive uma eventual conversa "negócios" já existente, mesmo que
    # o cartão de criar uma nova não apareça mais) — só o conjunto de
    # CARTÕES de "nova conversa" é filtrado, em `personas_disponiveis`.
    # Abrir/mandar mensagem numa conversa "negócios" antiga continua
    # bloqueado em conversa()/enviar_mensagem() logo abaixo.
    personas_disponiveis = {
        chave: p for chave, p in PERSONA_CONFIG.items()
        if chave != "negocios" or current_user.pode_ver_financeiro
    }
    return render_template("agente_ia/index.html", conversas=conversas,
                            personas=PERSONA_CONFIG, personas_disponiveis=personas_disponiveis,
                            configurado=configurado, provedor_texto=provedor_texto)


@agente_ia_bp.route("/nova", methods=["POST"])
@login_required
def nova_conversa():
    persona = request.form.get("persona")
    if persona not in ConversaAgenteIA.PERSONAS:
        flash("Selecione um agente válido.", "danger")
        return redirect(url_for("agente_ia.index"))
    if persona == "negocios" and not current_user.pode_ver_financeiro:
        # Mesma regra de acesso financeiro do resto do sistema (ver
        # PENDENCIAS.md, seção -45) — sem isso, qualquer usuário logado
        # conseguia ver receita a receber/atrasada só escolhendo esta
        # persona, mesmo sem acesso à aba Financeiro.
        abort(403)
    if not current_user.unidade_id:
        flash("Seu usuário não está vinculado a uma unidade — não é possível usar o agente de IA.", "danger")
        return redirect(url_for("agente_ia.index"))

    conversa = ConversaAgenteIA(usuario_id=current_user.id, unidade_id=current_user.unidade_id, persona=persona)
    db.session.add(conversa)
    db.session.commit()
    return redirect(url_for("agente_ia.conversa", conversa_id=conversa.id))


@agente_ia_bp.route("/<int:conversa_id>")
@login_required
def conversa(conversa_id):
    conversa = db.get_or_404(ConversaAgenteIA, conversa_id)
    if conversa.usuario_id != current_user.id and not current_user.is_admin:
        abort(403)
    if conversa.persona == "negocios" and not current_user.pode_ver_financeiro:
        # Cobre conversa antiga: se o acesso financeiro do usuário foi
        # revogado depois de criada, a conversa "Negócios" não pode mais
        # ser reaberta (ver PENDENCIAS.md, seção -45).
        abort(403)
    configurado = agente_ia_router.provedor_disponivel(current_user.empresa)
    provedor_texto = agente_ia_router.descricao_provedor(current_user.empresa)
    return render_template("agente_ia/conversa.html", conversa=conversa,
                            persona=PERSONA_CONFIG[conversa.persona], configurado=configurado,
                            provedor_texto=provedor_texto)


@agente_ia_bp.route("/<int:conversa_id>/mensagem", methods=["POST"])
@login_required
def enviar_mensagem(conversa_id):
    conversa = db.get_or_404(ConversaAgenteIA, conversa_id)
    if conversa.usuario_id != current_user.id:
        abort(403)
    if conversa.persona == "negocios" and not current_user.pode_ver_financeiro:
        # Mesma checagem de conversa() — é aqui, de fato, que
        # _contexto_negocios() roda e consulta Lancamento (ver
        # PENDENCIAS.md, seção -45).
        abort(403)

    texto = request.form.get("mensagem", "").strip()
    if not texto:
        return redirect(url_for("agente_ia.conversa", conversa_id=conversa.id))

    msg_usuario = MensagemAgenteIA(conversa_id=conversa.id, papel="user", conteudo=texto, status="pronta")
    db.session.add(msg_usuario)
    if not conversa.titulo:
        conversa.titulo = texto[:80]
    db.session.flush()

    # Monta o pedido agora (rápido, só leitura de banco) e cria a mensagem
    # do assistente já como placeholder "processando" — a chamada ao
    # modelo em si roda em segundo plano (ver app/jobs/ia_jobs.py e
    # PENDENCIAS.md, seção -32), pra não travar este worker do gunicorn
    # pelos minutos que o modelo local pode levar.
    contexto_dados = _CONTEXTO_POR_PERSONA[conversa.persona]()
    system, mensagens_api = _montar_system_e_mensagens(conversa.persona, conversa.mensagens, contexto_dados)

    msg_assistente = MensagemAgenteIA(conversa_id=conversa.id, papel="assistant", conteudo="", status="processando")
    db.session.add(msg_assistente)
    conversa.atualizado_em = datetime.utcnow()
    registrar_log(current_user, "mensagem_agente_ia", "ConversaAgenteIA", conversa.id, conversa.persona)
    db.session.commit()

    empresa_id = current_user.empresa_id_atual
    enfileirar(
        "app.jobs.ia_jobs.processar_mensagem_agente_ia",
        msg_assistente.id, empresa_id, system, mensagens_api,
    )

    return redirect(url_for("agente_ia.conversa", conversa_id=conversa.id))


@agente_ia_bp.route("/mensagens/<int:mensagem_id>/status")
@login_required
def status_mensagem(mensagem_id):
    """
    Endpoint de polling (ver conversa.html) — a página consulta isto de
    poucos em poucos segundos enquanto uma mensagem está "processando",
    pra saber quando recarregar e mostrar a resposta pronta, sem precisar
    de WebSocket.
    """
    mensagem = db.get_or_404(MensagemAgenteIA, mensagem_id)
    if mensagem.conversa.usuario_id != current_user.id and not current_user.is_admin:
        abort(403)
    return {"status": mensagem.status or "pronta"}


@agente_ia_bp.route("/<int:conversa_id>/excluir", methods=["POST"])
@login_required
def excluir_conversa(conversa_id):
    conversa = db.get_or_404(ConversaAgenteIA, conversa_id)
    if conversa.usuario_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(conversa)
    registrar_log(current_user, "excluiu", "ConversaAgenteIA", conversa_id)
    db.session.commit()
    flash("Conversa removida.", "info")
    return redirect(url_for("agente_ia.index"))
