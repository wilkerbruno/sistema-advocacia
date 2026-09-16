"""
Pipeline de captura por OAB (item 1 da lista de pipeline de IA jurídica —
PENDENCIAS.md, seção -102): recebe uma `PublicacaoCapturada` (de
app/utils/conector_djen.py, via polling, ou de um payload de push — ver
app/routes/captacao_oab.py::webhook_comunicacao) e decide o que fazer com
ela.

Diferente do pipeline por CNJ (app/utils/captura_pipeline.py, onde o
Processo já existe e a captura só ANEXA dado a ele), aqui o processo pode
não existir ainda no sistema — é exatamente o ponto do item 1 ("o input
humano do onboarding é a OAB, não o processo"). Por isso a decisão central
deste módulo é: o número do processo da publicação bate com algum Processo
já cadastrado NESTA EMPRESA?
  - Bate com exatamente um: vincula automaticamente (cria Publicacao real,
    roda o motor de próxima ação, gera Prazo) — nenhuma ação humana
    necessária.
  - Não bate com nenhum (ou bate com mais de um — número truncado/mal
    formatado é raro mas acontece): vai para a caixa de triagem
    (`IntimacaoCapturada.status == "pendente_triagem"`), onde um humano
    decide (app/routes/captacao_oab.py::triagem/vincular).
"""
from app.extensions import db
from app.models import Processo, Publicacao, Unidade, IntimacaoCapturada
from app.utils.cnj import somente_digitos
from app.utils.prazos_engine import aplicar_regra_a_publicacao
from app.utils.notificacoes import notificar


def _processos_candidatos(empresa_id, numero_digitos):
    """
    Compara por dígitos (a fonte pode devolver mascarado ou não, e o
    cadastro do escritório pode ter o número com ou sem máscara) — feito em
    Python, não em SQL, porque `Processo.numero_processo` guarda o texto
    como o usuário/captura anterior digitou (com ou sem pontuação) e
    normalizar isso em SQL portável entre SQLite (testes) e MySQL (produção)
    seria mais frágil do que comparar em memória. Escala bem pro tamanho
    real de um escritório (centenas a poucos milhares de processos por
    empresa) — se um dia isso virar gargalo de verdade, o caminho é
    guardar uma coluna normalizada (`numero_processo_digitos`) indexada.
    """
    candidatos = (
        Processo.query.join(Unidade, Processo.unidade_id == Unidade.id)
        .filter(Unidade.empresa_id == empresa_id, Processo.numero_processo.isnot(None))
        .all()
    )
    return [p for p in candidatos if somente_digitos(p.numero_processo) == numero_digitos]


def processar_publicacao_capturada(oab_monitorada, publicacao_capturada, origem="comunica_api"):
    """
    Ponto de entrada único — usado tanto pelo cron (capturar_intimacoes_oab.py)
    quanto pelo webhook de push (app/routes/captacao_oab.py). Idempotente:
    se `publicacao_capturada.id_comunicacao_fonte` já foi processado antes
    (mesma comunicação vista de novo numa captura seguinte, ou chegando por
    push depois de já ter chegado por polling), não faz nada e devolve
    None — nunca duplica.

    Devolve a `IntimacaoCapturada` criada (com status já definido), ou
    None se já existia.
    """
    id_fonte = publicacao_capturada.id_comunicacao_fonte
    if not id_fonte:
        return None  # sem identificador da fonte não dá pra deduplicar com segurança — descarta

    if IntimacaoCapturada.query.filter_by(id_comunicacao_fonte=id_fonte).first():
        return None

    unidade = oab_monitorada.unidade
    empresa = unidade.empresa if unidade else None

    intimacao = IntimacaoCapturada(
        oab_monitorada_id=oab_monitorada.id, unidade_id=oab_monitorada.unidade_id,
        id_comunicacao_fonte=id_fonte,
        numero_processo=publicacao_capturada.numero_processo,
        numero_processo_mascara=publicacao_capturada.numero_processo_mascara,
        tribunal=publicacao_capturada.tribunal,
        orgao=publicacao_capturada.orgao,
        tipo_comunicacao=publicacao_capturada.tipo_comunicacao,
        tipo_documento=publicacao_capturada.tipo_documento,
        meio=publicacao_capturada.meio,
        texto=publicacao_capturada.teor,
        destinatarios_texto=publicacao_capturada.destinatarios_texto,
        data_disponibilizacao=publicacao_capturada.data_disponibilizacao,
        data_publicacao=publicacao_capturada.data_publicacao,
        link_certidao=publicacao_capturada.link_certidao,
        origem=origem,
        status="pendente_triagem",
    )
    db.session.add(intimacao)
    db.session.flush()

    numero_digitos = somente_digitos(publicacao_capturada.numero_processo or "")
    if numero_digitos and empresa is not None:
        candidatos = _processos_candidatos(empresa.id, numero_digitos)
        if len(candidatos) == 1:
            vincular_intimacao_a_processo(intimacao, candidatos[0], usuario=None)

    return intimacao


def vincular_intimacao_a_processo(intimacao, processo, usuario=None):
    """
    Cria a `Publicacao` real no processo a partir da intimação capturada,
    roda o motor de próxima ação (`aplicar_regra_a_publicacao`) e marca a
    intimação como vinculada. `usuario=None` significa vínculo AUTOMÁTICO
    (feito pelo próprio pipeline, sem decisão humana) — `usuario` informado
    é usado pela triagem manual (app/routes/captacao_oab.py::vincular).

    Idempotente por `hash_dedup`: se este mesmo `id_comunicacao_fonte` já
    virou uma Publicacao antes (não deveria acontecer, já que
    `processar_publicacao_capturada` deduplica antes de chegar aqui — mas
    a triagem manual chama isto diretamente, então vale a mesma garantia
    nos dois caminhos), reaproveita a Publicacao existente em vez de
    duplicar.
    """
    from datetime import datetime

    publicacao = Publicacao.query.filter_by(hash_dedup=intimacao.id_comunicacao_fonte).first()
    if publicacao is None:
        publicacao = Publicacao(
            processo_id=processo.id, diario="DJEN",
            data_disponibilizacao=intimacao.data_disponibilizacao,
            data_publicacao=intimacao.data_publicacao,
            teor=intimacao.texto, oab_destinataria=f"{intimacao.oab_monitorada.numero}/{intimacao.oab_monitorada.uf}",
            origem_captura=intimacao.origem, hash_dedup=intimacao.id_comunicacao_fonte,
        )
        db.session.add(publicacao)
        db.session.flush()

        prazo = aplicar_regra_a_publicacao(publicacao, permitir_generico=True)
        if prazo is not None:
            db.session.add(prazo)
            db.session.flush()
            intimacao.prazo_id = prazo.id
            if processo.responsavel_id:
                notificar(
                    processo.responsavel_id, "Nova intimação capturada por OAB",
                    f"Processo {processo.numero_processo or processo.numero_interno}: "
                    f"\"{prazo.descricao}\" — vencimento {prazo.data_vencimento.strftime('%d/%m/%Y')}.",
                    tipo="prazo",
                )

    processo.ultima_movimentacao_em = datetime.utcnow()

    intimacao.status = "vinculada"
    intimacao.processo_id = processo.id
    intimacao.publicacao_id = publicacao.id
    intimacao.vinculado_por_id = usuario.id if usuario else None
    intimacao.vinculado_em = datetime.utcnow()
    return intimacao
