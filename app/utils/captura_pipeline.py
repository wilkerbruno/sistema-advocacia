"""
Pipeline compartilhado de persistência de dados capturados de um conector
real (ver app/utils/captura_conectores.py e app/utils/conector_datajud.py).
Usado tanto no cadastro inicial por CNJ (app/routes/governanca.py -
novo_por_cnj) quanto na captura periódica (capturar_movimentacoes.py), pra
garantir que os dois caminhos apliquem exatamente a mesma lógica de
deduplicação, máquina de estados (seção 6) e motor de próxima ação
(seção 7.1) — o mesmo pipeline que já existia para o registro manual
(governanca.nova_movimentacao).
"""
from datetime import datetime

from flask import has_request_context, url_for

from app.extensions import db
from app.models import Movimentacao
from app.utils.estado_processual_engine import traduzir_movimentacao
from app.utils.prazos_engine import aplicar_regra_proxima_acao
from app.utils.notificacoes import notificar


def montar_nota_datajud(dados_capturados):
    """
    Monta uma notinha de texto só com o que o DataJud devolve que NÃO tem
    campo próprio no cadastro (ver app/templates/processos/form.html):
    mais de um assunto CNJ (o campo "Área do direito" só guarda um texto
    corrido), se o processo é eletrônico/físico e por qual sistema (PJe,
    e-Proc...), e um alerta se o DataJud sinalizar algum nível de sigilo —
    pra virar preenchimento automático da "Descrição/objeto" (ver
    `aplicar_carga_inicial` abaixo) em vez de ficar um dado capturado mas
    perdido, sem aparecer em lugar nenhum do cadastro.

    Devolve None quando não há nada que valha a pena registrar (nenhum
    desses três só um assunto e sem sistema/sigilo informado).
    """
    partes = []

    assuntos = dados_capturados.get("assuntos_lista") or []
    if len(assuntos) > 1:
        partes.append(f"Assuntos (CNJ): {'; '.join(assuntos)}.")

    sistema = dados_capturados.get("sistema")
    formato = dados_capturados.get("formato")
    if sistema or formato:
        if formato and sistema:
            partes.append(f"Processo {formato.lower()}, sistema {sistema}.")
        elif formato:
            partes.append(f"Processo {formato.lower()}.")
        else:
            partes.append(f"Sistema: {sistema}.")

    nivel_sigilo = dados_capturados.get("nivel_sigilo")
    if nivel_sigilo not in (None, 0):
        partes.append(
            f"Atenção: o DataJud indica nível de sigilo {nivel_sigilo} neste processo — "
            "confira se deve estar marcado como \"Segredo de justiça\"."
        )

    if not partes:
        return None
    return "Dados do DataJud (captura automática): " + " ".join(partes)


def aplicar_carga_inicial(processo, dados_capturados):
    """
    Preenche campos do Processo com o retorno de
    ConectorCaptura.consultar_processo() — só quando o campo ainda está
    vazio, nunca sobrescreve o que já foi preenchido manualmente.
    """
    if dados_capturados.get("classe") and not processo.classe_processual:
        processo.classe_processual = dados_capturados["classe"]
    # "classe" (ex: "Execução Fiscal", "Procedimento Comum Cível") também
    # preenche "Tipo de ação" — campo visível no cadastro manual (ver
    # app/templates/processos/form.html), que não tem equivalente próprio
    # vindo do DataJud; "classe_processual" acima é o valor "cru" da
    # classificação CNJ, guardado à parte para relatórios/BI.
    if dados_capturados.get("classe") and not processo.tipo_acao:
        processo.tipo_acao = dados_capturados["classe"]
    if dados_capturados.get("assunto") and not processo.assunto_cnj:
        processo.assunto_cnj = dados_capturados["assunto"]
    if dados_capturados.get("orgao_julgador") and not processo.vara:
        processo.vara = dados_capturados["orgao_julgador"]
    if dados_capturados.get("instancia") and not processo.instancia:
        processo.instancia = dados_capturados["instancia"]
    if dados_capturados.get("comarca") and not processo.comarca:
        processo.comarca = dados_capturados["comarca"]
    if dados_capturados.get("data_ajuizamento") and not processo.data_distribuicao:
        processo.data_distribuicao = dados_capturados["data_ajuizamento"].date()
    valor_causa = dados_capturados.get("valor_causa")
    if valor_causa and not processo.valor_causa:
        try:
            processo.valor_causa = valor_causa
        except (TypeError, ValueError):
            pass  # formato inesperado — não trava o cadastro por causa de um campo secundário

    nota = montar_nota_datajud(dados_capturados)
    if nota and not processo.descricao:
        processo.descricao = nota

    # nivelSigilo > 0: o próprio DataJud está sinalizando alguma restrição
    # de acesso — só LIGA a marcação (nunca desliga uma que o usuário já
    # tinha marcado ou desmarcado de propósito), e sempre com o motivo
    # registrado na nota acima, nunca uma mudança silenciosa.
    nivel_sigilo = dados_capturados.get("nivel_sigilo")
    if nivel_sigilo not in (None, 0) and not processo.segredo_justica:
        processo.segredo_justica = True


JANELA_DIAS_MOVIMENTACAO_RECENTE = 60
# Ver PENDENCIAS.md, seção -34. Uma movimentação SEM regra cadastrada só
# pode gerar o prazo genérico "Análise necessária" quando aconteceu há no
# máximo essa janela de dias (contados de hoje, não da data de cadastro do
# processo) — qualquer coisa mais antiga que isso é, por definição,
# histórico, nunca um prazo de verdade em aberto. Folga generosa acima do
# maior prazo processual comum (30 dias) de propósito, pra nunca suprimir
# um alerta genuinamente recente.


def registrar_movimentacoes_capturadas(processo, movimentacoes_capturadas, captura_inicial=False):
    """
    Persiste uma lista de MovimentacaoCapturada (dataclass de
    captura_conectores.py) como registros de Movimentacao, deduplicando
    por hash, rodando a máquina de estados e o motor de próxima ação.

    Uma movimentação SEM regra cadastrada só gera o prazo genérico de
    "Análise necessária" quando é RECENTE (ver JANELA_DIAS_MOVIMENTACAO_RECENTE
    acima) — decidido pela data real do ato, não por esta função ter sido
    chamada com `captura_inicial=True` ou não (ver PENDENCIAS.md, seção -34
    para o histórico da mudança). Isso importa em dois casos, não só um:

    1) Cadastro por CNJ (captura_inicial=True): o DataJud devolve o
       histórico inteiro do processo de uma vez, que pode ter anos (ou
       décadas) de movimentações antigas — sem esse filtro, cada uma delas
       que não bater com nenhuma regra cadastrada geraria um prazo
       "genérico" com vencimento já vencido há anos, inundando a tela de
       Prazos com dezenas de alarmes falsos (foi exatamente o que
       aconteceu num processo real de 2002 usado nos testes).
    2) Captura periódica (captura_inicial=False, padrão — ver
       capturar_movimentacoes.py): o DataJud pode indexar/expor uma
       movimentação ANTIGA só depois (defasagem de indexação do próprio
       tribunal, às vezes de meses, especialmente em processos migrados de
       físico pra eletrônico) — nesse caso ela chega "nova" pro nosso hash
       de deduplicação mesmo sendo de anos atrás, e caía no mesmo problema
       do item 1 mesmo fora da carga inicial (é o que gerou a avalanche de
       "Análise necessária" de 2002/2003/2012 num processo real reportado
       pelo usuário — a captura funcionou certo, o problema era só este
       filtro não cobrir esse caminho).

    Em ambos os casos a movimentação continua registrada e visível do
    mesmo jeito (aba Governança, badge "triagem pendente" quando sem
    mapa) — o filtro só evita virar uma tarefa de prazo fantasma; uma
    movimentação recente de verdade (dentro da janela) sempre gera seu
    prazo genérico normalmente, capturada na carga inicial ou depois.

    `captura_inicial` é mantido como parâmetro só para os chamadores
    documentarem a intenção da chamada (cadastro/CNJ vs. cron periódico) e
    para diferenciar a mensagem de LogCaptura em quem o usa — não influencia
    mais esta decisão.

    Devolve o número de movimentações NOVAS persistidas (as já existentes
    são silenciosamente ignoradas — é o comportamento normal em recaptura
    periódica, onde a maioria já foi vista antes). Não faz commit — quem
    chama decide.
    """
    hoje = datetime.utcnow()

    novas = 0
    for capturada in movimentacoes_capturadas:
        if not capturada.data:
            continue
        if Movimentacao.query.filter_by(hash_dedup=capturada.hash_dedup).first():
            continue

        mov = Movimentacao(
            processo_id=processo.id, data=capturada.data,
            codigo_tpu=capturada.codigo_tpu, texto_integral=capturada.texto_integral,
            origem_captura="datajud", hash_dedup=capturada.hash_dedup,
        )
        db.session.add(mov)
        db.session.flush()

        historico = traduzir_movimentacao(mov)
        if historico:
            db.session.add(historico)

        permitir_generico = (hoje - capturada.data).days <= JANELA_DIAS_MOVIMENTACAO_RECENTE
        prazo_gerado = aplicar_regra_proxima_acao(mov, permitir_generico=permitir_generico)
        if prazo_gerado:
            db.session.add(prazo_gerado)
            db.session.flush()
            if prazo_gerado.responsavel_id:
                link = None
                if has_request_context():
                    try:
                        link = url_for("processos.detalhe", processo_id=processo.id)
                    except Exception:
                        link = None
                notificar(prazo_gerado.responsavel_id, "Novo prazo gerado automaticamente",
                          f"{prazo_gerado.descricao} — vence em {prazo_gerado.data_vencimento.strftime('%d/%m/%Y')}",
                          tipo="prazo", link=link)
        novas += 1

    processo.ultima_captura_em = datetime.utcnow()
    datas = [m.data for m in movimentacoes_capturadas if m.data]
    if datas:
        mais_recente = max(datas)
        if not processo.ultima_movimentacao_em or mais_recente > processo.ultima_movimentacao_em:
            processo.ultima_movimentacao_em = mais_recente

    return novas
