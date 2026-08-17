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


def registrar_movimentacoes_capturadas(processo, movimentacoes_capturadas):
    """
    Persiste uma lista de MovimentacaoCapturada (dataclass de
    captura_conectores.py) como registros de Movimentacao, deduplicando
    por hash, rodando a máquina de estados e o motor de próxima ação.

    Devolve o número de movimentações NOVAS persistidas (as já existentes
    são silenciosamente ignoradas — é o comportamento normal em recaptura
    periódica, onde a maioria já foi vista antes). Não faz commit — quem
    chama decide.
    """
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

        prazo_gerado = aplicar_regra_proxima_acao(mov)
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
