"""
Reatribuição de casos no desligamento de usuário (PENDENCIAS.md, seção
-46) — item "Reatribuição de casos no desligamento de usuário" da tabela
de prioridades do relatório de 20/08/2026.

Problema que isto resolve: antes desta rodada, desativar um usuário
(desmarcar "Usuário ativo" em Configurações → Usuários) não fazia
NENHUMA verificação — o usuário virava inativo mesmo tendo processo,
prazo, audiência, tarefa ou compromisso futuro sob a responsabilidade
dele. Isso não bloqueia o funcionamento do sistema (o registro continua
existindo), mas deixa "órfão" um item cujo responsável não pode mais
logar nem ser notificado — na prática, ninguem mais recebe lembrete
daquele prazo, daquela audiência.

Modelos com um campo `responsavel_id` que precisa ser levado em conta
(ver grep em app/models/*.py): Processo, Prazo, Audiencia, Tarefa e
Compromisso. Deliberadamente NÃO mexe em `criado_por_id` (Tarefa,
Compromisso) nem em `alterado_por_id`/`regularizado_por_id` (Prazo) —
esses são fatos históricos de auditoria (quem criou/alterou o registro),
não "quem é responsável agora", e reescrever isso apagaria rastro real
do que aconteceu.

Só considera "em aberto" (isto é, só isso conta pra decidir se precisa
de reatribuição, e só isso é reatribuído):
  - Processo: status == "ativo"
  - Prazo: status em STATUS_PRAZO_ABERTOS (mesmo conjunto usado em
    enviar_lembretes_prazos_audiencias.py, exceto que aqui NÃO inclui
    "historico_anterior" — ver docstring de Prazo — porque esse status é
    propositalmente neutro/histórico, não uma pendência de verdade) e
    `deletado_em is None`
  - Audiencia: status == "agendada" (não filtra por data — uma audiência
    agendada pro passado e nunca marcada como realizada ainda é uma
    pendência de verdade, precisa de alguém responsável por ela)
  - Tarefa: status em ("pendente", "em_andamento")
  - Compromisso: status == "agendado" E data_hora no futuro (compromisso
    agendado que já passou da hora não muda mais nada sendo reatribuído)

Nunca reatribui sozinho: a view que usa este módulo sempre exige que um
humano (admin ou gestor) escolha explicitamente o novo responsável antes
de confirmar — mesmo padrão "sugestão nunca vira ação sozinha" usado no
resto do projeto (ex: gerar_cobranca_horas, anonimizar_cliente).
"""
from datetime import datetime
from app.extensions import db
from app.models import Processo, Prazo, Audiencia, Tarefa, Compromisso

STATUS_PRAZO_ABERTOS = ("pendente", "em_elaboracao", "protocolado_aguardando_evidencia")
STATUS_TAREFA_ABERTOS = ("pendente", "em_andamento")


def _query_processos_abertos(usuario_id):
    return Processo.query.filter(Processo.responsavel_id == usuario_id, Processo.status == "ativo")


def _query_prazos_abertos(usuario_id):
    return Prazo.query.filter(
        Prazo.responsavel_id == usuario_id,
        Prazo.status.in_(STATUS_PRAZO_ABERTOS),
        Prazo.deletado_em.is_(None),
    )


def _query_audiencias_abertas(usuario_id):
    return Audiencia.query.filter(Audiencia.responsavel_id == usuario_id, Audiencia.status == "agendada")


def _query_tarefas_abertas(usuario_id):
    return Tarefa.query.filter(Tarefa.responsavel_id == usuario_id, Tarefa.status.in_(STATUS_TAREFA_ABERTOS))


def _query_compromissos_abertos(usuario_id):
    return Compromisso.query.filter(
        Compromisso.responsavel_id == usuario_id,
        Compromisso.status == "agendado",
        Compromisso.data_hora >= datetime.utcnow(),
    )


def itens_em_aberto(usuario_id):
    """
    Conta, por categoria, quantos itens em aberto ainda estão sob a
    responsabilidade deste usuário. Usado tanto pra decidir se o
    desligamento direto (checkbox "Usuário ativo") pode seguir sem
    reatribuição, quanto pra montar a tela de confirmação em
    app/routes/admin.py::desligar_usuario.
    """
    return {
        "processos": _query_processos_abertos(usuario_id).count(),
        "prazos": _query_prazos_abertos(usuario_id).count(),
        "audiencias": _query_audiencias_abertas(usuario_id).count(),
        "tarefas": _query_tarefas_abertas(usuario_id).count(),
        "compromissos": _query_compromissos_abertos(usuario_id).count(),
    }


def tem_itens_em_aberto(usuario_id):
    return any(itens_em_aberto(usuario_id).values())


def reatribuir_itens_em_aberto(usuario_id, novo_responsavel_id):
    """
    Move TODOS os itens em aberto (mesmas 5 categorias/filtros de
    itens_em_aberto) do usuário que está sendo desligado pro novo
    responsável escolhido. Não comita sozinho — quem chama decide quando
    commitar (a view faz isso numa única transação junto com
    `usuario.ativo = False`, pra nunca ficar num estado parcial: ou
    reatribui E desliga, ou nenhum dos dois).

    Retorna o mesmo formato de itens_em_aberto(), com quantos itens
    foram efetivamente movidos em cada categoria — usado pra montar a
    mensagem de confirmação e o registro no log de atividade.
    """
    contagem = {
        "processos": _query_processos_abertos(usuario_id).update(
            {"responsavel_id": novo_responsavel_id}, synchronize_session=False
        ),
        "prazos": _query_prazos_abertos(usuario_id).update(
            {"responsavel_id": novo_responsavel_id}, synchronize_session=False
        ),
        "audiencias": _query_audiencias_abertas(usuario_id).update(
            {"responsavel_id": novo_responsavel_id}, synchronize_session=False
        ),
        "tarefas": _query_tarefas_abertas(usuario_id).update(
            {"responsavel_id": novo_responsavel_id}, synchronize_session=False
        ),
        "compromissos": _query_compromissos_abertos(usuario_id).update(
            {"responsavel_id": novo_responsavel_id}, synchronize_session=False
        ),
    }
    db.session.expire_all()
    return contagem
