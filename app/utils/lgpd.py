"""
Ferramentas de LGPD (PENDENCIAS.md, seção -43) — exportação de dados
(portabilidade, art. 18 V) e anonimização (direito ao esquecimento, art.
18 VI). Consentimento/base legal são só campos de REGISTRO no cadastro do
cliente (app/models/cliente.py) — não têm lógica própria aqui.

⚠️ Importante sobre escopo (documentado também no PENDENCIAS.md, pra não
prometer mais do que o sistema de fato garante): isto cobre os campos
ESTRUTURADOS de dado pessoal do Cliente (nome, CPF/CNPJ, contatos,
endereço). NÃO varre texto livre em todo o sistema (ex: se o nome do
cliente foi mencionado dentro de `Processo.descricao` ou de uma
`Andamento.descricao`, esse texto livre continua como estava — reescrever
narrativa de processo automaticamente é arriscado demais para fazer sem
revisão humana, poderia corromper o histórico do caso). "Ferramentas de
LGPD" aqui significa "ajuda operacional pra atender uma solicitação",
não "garantia automática de conformidade" — a decisão de que uma
anonimização é apropriada (não há mais base legal pra reter o dado) é
sempre humana.
"""
from datetime import datetime, date
from decimal import Decimal


def _serializavel(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def montar_export_dados_cliente(cliente):
    """
    Portabilidade de dados (art. 18 V da LGPD): tudo que o sistema guarda
    sobre este cliente, num formato estruturado (JSON) — cadastro,
    processos vinculados, lançamentos financeiros, apontamentos de hora
    ligados aos processos dele, e compromissos de agenda.
    """
    dados = {
        "gerado_em": datetime.utcnow().isoformat() + "Z",
        "cliente": {
            "id": cliente.id,
            "tipo_pessoa": cliente.tipo_pessoa,
            "nome": cliente.nome,
            "cpf_cnpj": cliente.cpf_cnpj,
            "rg_ie": cliente.rg_ie,
            "email": cliente.email,
            "telefone": cliente.telefone,
            "whatsapp": cliente.whatsapp,
            "endereco": cliente.endereco,
            "cidade": cliente.cidade,
            "estado": cliente.estado,
            "cep": cliente.cep,
            "observacoes": cliente.observacoes,
            "ativo": cliente.ativo,
            "criado_em": _serializavel(cliente.criado_em),
            "base_legal_tratamento": cliente.base_legal_tratamento,
            "consentimento_obtido_em": _serializavel(cliente.consentimento_obtido_em),
            "consentimento_observacoes": cliente.consentimento_observacoes,
        },
        "processos": [],
        "lancamentos_financeiros": [],
        "apontamentos_horas": [],
        "compromissos_agenda": [],
    }

    for p in cliente.processos:
        dados["processos"].append({
            "id": p.id,
            "numero_processo": p.numero_processo,
            "numero_interno": p.numero_interno,
            "area_direito": p.area_direito,
            "status": p.status,
            "parte_contraria": p.parte_contraria,
            "valor_causa": _serializavel(p.valor_causa),
            "data_distribuicao": _serializavel(p.data_distribuicao),
            "criado_em": _serializavel(p.criado_em),
        })

    from app.models import Lancamento, Apontamento, Compromisso
    for l in Lancamento.query.filter_by(cliente_id=cliente.id).all():
        dados["lancamentos_financeiros"].append({
            "id": l.id,
            "descricao": l.descricao,
            "tipo": l.tipo,
            "natureza": l.natureza,
            "valor": _serializavel(l.valor),
            "status": l.status,
            "data_vencimento": _serializavel(l.data_vencimento),
            "data_pagamento": _serializavel(l.data_pagamento),
            "processo_id": l.processo_id,
        })

    ids_processos = [p.id for p in cliente.processos]
    if ids_processos:
        for a in Apontamento.query.filter(Apontamento.processo_id.in_(ids_processos)).all():
            dados["apontamentos_horas"].append({
                "id": a.id,
                "data": _serializavel(a.data),
                "horas": _serializavel(a.horas),
                "descricao": a.descricao,
                "processo_id": a.processo_id,
            })

    for c in Compromisso.query.filter_by(cliente_id=cliente.id).all():
        dados["compromissos_agenda"].append({
            "id": c.id,
            "titulo": c.titulo,
            "descricao": c.descricao,
            "data_hora": _serializavel(c.data_hora),
            "status": c.status,
        })

    return dados


CAMPOS_ANONIMIZADOS_PLACEHOLDER = {
    "cpf_cnpj": None,
    "rg_ie": None,
    "email": None,
    "telefone": None,
    "whatsapp": None,
    "endereco": None,
    "cidade": None,
    "estado": None,
    "cep": None,
    "observacoes": None,
}


def anonimizar_cliente(cliente, usuario):
    """
    Sobrescreve os campos de dado pessoal identificável do cliente
    (direito ao esquecimento, art. 18 VI). NÃO apaga processos,
    lançamentos financeiros nem apontamentos vinculados — a obrigação
    legal/fiscal de guarda desses registros continua valendo; só a
    identificação pessoal do cliente é removida. NÃO faz commit (quem
    chama decide quando commitar, igual ao resto do projeto) e é
    IRREVERSÍVEL — o nome original não fica guardado em lugar nenhum
    (só no LogAtividade, que registra a ação mas não os dados apagados).
    """
    cliente.nome = f"Cliente anonimizado #{cliente.id}"
    for campo, valor in CAMPOS_ANONIMIZADOS_PLACEHOLDER.items():
        setattr(cliente, campo, valor)
    cliente.anonimizado_em = datetime.utcnow()
    cliente.anonimizado_por_id = usuario.id if usuario else None
