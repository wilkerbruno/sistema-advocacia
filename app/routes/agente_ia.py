"""
Agentes de IA jurídica (item 6 do briefing de paridade): três personas —
Operação (advogados), Gestão (controller) e Negócios (sócios) — cada uma
com um system prompt próprio e um "contexto atual do escritório" real,
montado a partir dos dados do banco no escopo do usuário logado (nunca
dados de outra empresa/unidade), injetado a cada mensagem.

Isso é o que separa isso de um chatbot genérico: as respostas são
embasadas em números reais do momento da pergunta (prazos vencendo,
processos parados, receita pendente etc.), não em nada inventado.

Motor: modelo de IA local (até 2B parâmetros, ver app/utils/ia_local.py),
rodando dentro do próprio servidor — sem chave de API, sem custo por
mensagem, sem dado saindo do servidor. Sem o arquivo de pesos baixado
(ver baixar_modelo_ia_local.py), o agente responde de forma honesta que
está indisponível — nunca finge uma resposta nem trava a tela.
"""
from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import ConversaAgenteIA, MensagemAgenteIA, Processo, Prazo, Tarefa, Cliente, Lancamento
from app.utils.acesso import aplicar_escopo_unidade
from app.utils.notificacoes import registrar_log
from app.utils import ia_local

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


class AgenteIndisponivelError(Exception):
    pass


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


# ---------------------- Chamada ao modelo ----------------------

def _chamar_llm(persona, mensagens_historico, contexto_dados):
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

    try:
        return ia_local.gerar_resposta(system, mensagens_api)
    except ia_local.ModeloIndisponivelError as e:
        raise AgenteIndisponivelError(str(e)) from e


# ---------------------- Rotas ----------------------

@agente_ia_bp.route("/")
@login_required
def index():
    conversas = ConversaAgenteIA.query.filter_by(usuario_id=current_user.id) \
        .order_by(ConversaAgenteIA.atualizado_em.desc()).all()
    configurado = ia_local.modelo_disponivel()
    return render_template("agente_ia/index.html", conversas=conversas,
                            personas=PERSONA_CONFIG, configurado=configurado)


@agente_ia_bp.route("/nova", methods=["POST"])
@login_required
def nova_conversa():
    persona = request.form.get("persona")
    if persona not in ConversaAgenteIA.PERSONAS:
        flash("Selecione um agente válido.", "danger")
        return redirect(url_for("agente_ia.index"))
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
    configurado = ia_local.modelo_disponivel()
    return render_template("agente_ia/conversa.html", conversa=conversa,
                            persona=PERSONA_CONFIG[conversa.persona], configurado=configurado)


@agente_ia_bp.route("/<int:conversa_id>/mensagem", methods=["POST"])
@login_required
def enviar_mensagem(conversa_id):
    conversa = db.get_or_404(ConversaAgenteIA, conversa_id)
    if conversa.usuario_id != current_user.id:
        abort(403)

    texto = request.form.get("mensagem", "").strip()
    if not texto:
        return redirect(url_for("agente_ia.conversa", conversa_id=conversa.id))

    msg_usuario = MensagemAgenteIA(conversa_id=conversa.id, papel="user", conteudo=texto)
    db.session.add(msg_usuario)
    if not conversa.titulo:
        conversa.titulo = texto[:80]
    db.session.flush()

    try:
        contexto_dados = _CONTEXTO_POR_PERSONA[conversa.persona]()
        resposta_texto = _chamar_llm(conversa.persona, conversa.mensagens, contexto_dados)
        if not resposta_texto:
            resposta_texto = "[O agente respondeu vazio — tente reformular a pergunta.]"
    except AgenteIndisponivelError as e:
        resposta_texto = f"⚠️ Agente indisponível: {e}"
    except Exception as e:  # nunca deixa a conversa travada por erro da API externa
        resposta_texto = f"⚠️ Não foi possível consultar o agente de IA agora: {e}"

    msg_assistente = MensagemAgenteIA(conversa_id=conversa.id, papel="assistant", conteudo=resposta_texto)
    db.session.add(msg_assistente)
    conversa.atualizado_em = datetime.utcnow()
    registrar_log(current_user, "mensagem_agente_ia", "ConversaAgenteIA", conversa.id, conversa.persona)
    db.session.commit()

    return redirect(url_for("agente_ia.conversa", conversa_id=conversa.id))


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
