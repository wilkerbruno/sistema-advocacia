"""
API de leitura autenticada para integração com o Data Lake do escritório
(seção 12 do briefing: "o sistema deve ser fonte, não ilha").

Autenticação: header `Authorization: Bearer <token>`, comparado com a
variável de ambiente `DATALAKE_API_TOKEN`. Sem essa variável configurada,
a API responde 503 em vez de deixar passar sem autenticação — nunca abre
os dados por engano.

Suporta sincronização incremental via `?desde=AAAA-MM-DDTHH:MM:SS`
(devolve só registros criados/atualizados a partir daquele instante), para
o Data Lake não precisar reimportar tudo a cada execução.

⚠️ O formato exato de payload que o Data Lake do escritório espera receber
não foi definido aqui (não temos a documentação do lado deles) — este é o
formato "genérico" (JSON, um objeto por registro, todos os campos do
modelo). Se o Data Lake exigir um formato específico (ex: Parquet, ou nomes
de campo diferentes), é só ajustar o `to_dict` de cada rota.
"""
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request, current_app

from app.models import Processo, Movimentacao, Publicacao, Decisao, Prazo

api_integracao_bp = Blueprint("api_integracao", __name__)


def exige_token(f):
    @wraps(f)
    def decorado(*args, **kwargs):
        token_esperado = current_app.config.get("DATALAKE_API_TOKEN")
        if not token_esperado:
            return jsonify(erro="API de integração não configurada (DATALAKE_API_TOKEN ausente no .env)."), 503

        auth = request.headers.get("Authorization", "")
        token_recebido = auth[7:] if auth.startswith("Bearer ") else None
        if not token_recebido or token_recebido != token_esperado:
            return jsonify(erro="Token inválido ou ausente."), 401
        return f(*args, **kwargs)
    return decorado


def _filtrar_desde(query, modelo, desde_str):
    if not desde_str:
        return query
    try:
        desde = datetime.fromisoformat(desde_str)
    except ValueError:
        return query
    campo = getattr(modelo, "atualizado_em", None) or getattr(modelo, "criado_em")
    return query.filter(campo >= desde)


@api_integracao_bp.route("/processos")
@exige_token
def processos():
    query = _filtrar_desde(Processo.query, Processo, request.args.get("desde"))
    pagina = request.args.get("pagina", 1, type=int)
    resultado = query.order_by(Processo.id).paginate(page=pagina, per_page=200, error_out=False)
    return jsonify(
        pagina=resultado.page, total_paginas=resultado.pages, total_registros=resultado.total,
        registros=[{
            "id": p.id, "numero_processo": p.numero_processo, "area_direito": p.area_direito,
            "fase": p.fase, "estado_negocio_atual": p.estado_negocio_atual, "status": p.status,
            "status_comercial": p.status_comercial, "unidade_id": p.unidade_id, "cliente_id": p.cliente_id,
            "valor_causa": float(p.valor_causa) if p.valor_causa is not None else None,
            "data_distribuicao": p.data_distribuicao.isoformat() if p.data_distribuicao else None,
            "monitoravel": p.monitoravel, "forma_acompanhamento": p.forma_acompanhamento,
            "criado_em": p.criado_em.isoformat() if p.criado_em else None,
            "atualizado_em": p.atualizado_em.isoformat() if p.atualizado_em else None,
        } for p in resultado.items],
    )


@api_integracao_bp.route("/movimentacoes")
@exige_token
def movimentacoes():
    query = _filtrar_desde(Movimentacao.query, Movimentacao, request.args.get("desde"))
    pagina = request.args.get("pagina", 1, type=int)
    resultado = query.order_by(Movimentacao.id).paginate(page=pagina, per_page=200, error_out=False)
    return jsonify(
        pagina=resultado.page, total_paginas=resultado.pages, total_registros=resultado.total,
        registros=[{
            "id": m.id, "processo_id": m.processo_id, "data": m.data.isoformat() if m.data else None,
            "codigo_tpu": m.codigo_tpu, "estado_negocio_resultante": m.estado_negocio_resultante,
            "origem_captura": m.origem_captura, "triagem_pendente": m.triagem_pendente,
            "criado_em": m.criado_em.isoformat() if m.criado_em else None,
        } for m in resultado.items],
    )


@api_integracao_bp.route("/decisoes")
@exige_token
def decisoes():
    query = _filtrar_desde(Decisao.query, Decisao, request.args.get("desde"))
    pagina = request.args.get("pagina", 1, type=int)
    resultado = query.order_by(Decisao.id).paginate(page=pagina, per_page=200, error_out=False)
    return jsonify(
        pagina=resultado.page, total_paginas=resultado.pages, total_registros=resultado.total,
        registros=[{
            "id": d.id, "processo_id": d.processo_id, "tipo": d.tipo, "orgao_julgador": d.orgao_julgador,
            "magistrado_relator": d.magistrado_relator, "data": d.data.isoformat() if d.data else None,
            "resultado": d.resultado, "tese": d.tese,
            "criado_em": d.criado_em.isoformat() if d.criado_em else None,
        } for d in resultado.items],
    )


@api_integracao_bp.route("/prazos")
@exige_token
def prazos():
    query = Prazo.query.filter(Prazo.deletado_em.is_(None))
    query = _filtrar_desde(query, Prazo, request.args.get("desde"))
    pagina = request.args.get("pagina", 1, type=int)
    resultado = query.order_by(Prazo.id).paginate(page=pagina, per_page=200, error_out=False)
    return jsonify(
        pagina=resultado.page, total_paginas=resultado.pages, total_registros=resultado.total,
        registros=[{
            "id": pr.id, "processo_id": pr.processo_id, "descricao": pr.descricao,
            "data_vencimento": pr.data_vencimento.isoformat() if pr.data_vencimento else None,
            "status": pr.status, "calculo_automatico": pr.calculo_automatico,
            "responsavel_id": pr.responsavel_id,
            "criado_em": pr.criado_em.isoformat() if pr.criado_em else None,
        } for pr in resultado.items],
    )
