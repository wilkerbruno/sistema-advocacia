"""
Item 8 da lista de pipeline de IA jurídica trazida pelo usuário
(PENDENCIAS.md, seção -108): "Pesquisa vinculada — Legislação específica...
buscada em base real, com ementa e link."

Usa a API pública SRU (Search/Retrieval via URL, protocolo padrão da
Library of Congress) do LexML — Rede de Informação Legislativa e Jurídica,
projeto mantido por Senado, STF, CNJ e outros órgãos públicos
(https://www.lexml.gov.br/ / https://projeto.lexml.gov.br/), gratuita e
sem necessidade de cadastro ou chave de API. Jurisprudência real (ementa +
link de decisões de tribunal) NÃO está coberta aqui — normalmente exige um
provedor pago (Jusbrasil, Escavador, ou API oficial de algum tribunal
específico); decisão consciente de escopo desta rodada (ver PENDENCIAS.md,
seção -108) foi implementar só legislação por enquanto, e nunca fingir ter
uma fonte de jurisprudência que não foi contratada.

Endpoint e formato confirmados via documentação pública do projeto e
wrappers open source de terceiros (py-lexml-acervo, LeXML-mcp):
  - Base: https://www.lexml.gov.br/busca/SRU?operation=searchRetrieve&version=1.1
  - Query em CQL (Contextual Query Language) — ex.: dc.title any "termo"
  - Resposta em XML, Dublin Core (srw_dc:dc), com <urn>, <dc:title>,
    <dc:date>, <dc:description> (ementa), <tipoDocumento>.
  - Link público de um documento: https://www.lexml.gov.br/urn/{urn}

⚠️ Assim como app/utils/conector_datajud.py (mesmo aviso, mesmo motivo):
este código foi escrito a partir de documentação pública e de wrappers de
terceiros, mas não pôde ser testado contra uma chamada real à API a partir
deste ambiente de geração (rede de saída restrita). O parsing XML abaixo é
propositalmente tolerante (ignora namespace, ignora campo ausente) pra não
quebrar se algum detalhe do schema real divergir um pouco do documentado —
mas teste com uma busca real depois do deploy; se os resultados vierem
sistematicamente vazios ou malformados, me avise com um exemplo do XML de
resposta pra eu ajustar `_parse_resposta_sru`.
"""
import xml.etree.ElementTree as ET

import requests

BASE_URL = "https://www.lexml.gov.br/busca/SRU"
URN_URL_TEMPLATE = "https://www.lexml.gov.br/urn/{urn}"

_CAMPOS_TEXTO = ("title", "date", "description", "identifier")


class LexmlIndisponivelError(Exception):
    """Erro de rede/timeout/resposta malformada consultando o LexML — quem chama
    decide o que fazer (na prática: nunca bloqueia a geração do rascunho, só
    segue sem o bloco de legislação, com um aviso — ver app/routes/processos.py)."""


def _nome_local(tag):
    """Nome da tag XML sem o prefixo de namespace (ex.:
    '{http://purl.org/dc/elements/1.1/}title' -> 'title') — parsing
    tolerante a namespace, pra não depender de acertar a URI exata usada
    pelo LexML (documentação pública não é 100% explícita sobre isso)."""
    return tag.rsplit("}", 1)[-1]


def _montar_query_cql(termo):
    # CQL usa aspas duplas para delimitar frase — escapa aspas literais do
    # termo de busca pra não quebrar a sintaxe da query.
    termo_escapado = termo.replace('"', '\\"').strip()
    return f'dc.title any "{termo_escapado}" or dc.description any "{termo_escapado}"'


def _parse_resposta_sru(conteudo_xml):
    """Extrai a lista de registros de uma resposta XML SRU do LexML.
    Devolve [] se a resposta não tiver nenhum <recordData> reconhecível
    (nunca inventa resultado) — levanta LexmlIndisponivelError só se o XML
    em si estiver malformado (não parseável)."""
    try:
        raiz = ET.fromstring(conteudo_xml)
    except ET.ParseError as e:
        raise LexmlIndisponivelError(f"resposta do LexML não é um XML válido: {e}") from e

    registros = []
    for elemento in raiz.iter():
        if _nome_local(elemento.tag) != "recordData":
            continue
        campos = {}
        for filho in elemento.iter():
            nome = _nome_local(filho.tag)
            if nome in _CAMPOS_TEXTO and filho.text and nome not in campos:
                campos[nome] = filho.text.strip()
            elif nome == "urn" and filho.text and "urn" not in campos:
                campos["urn"] = filho.text.strip()
            elif nome == "tipoDocumento" and filho.text and "tipoDocumento" not in campos:
                campos["tipoDocumento"] = filho.text.strip()

        if not campos.get("urn"):
            continue  # sem urn não dá pra montar link nenhum — registro inútil, pula
        registros.append({
            "titulo": campos.get("title") or campos["urn"],
            "ementa": campos.get("description") or "",
            "data": campos.get("date") or "",
            "tipo": campos.get("tipoDocumento") or "",
            "urn": campos["urn"],
            "link": URN_URL_TEMPLATE.format(urn=campos["urn"]),
        })
    return registros


def buscar_legislacao(termo, limite=5, timeout=6):
    """
    Busca legislação real no LexML por texto livre (ex.: "código de defesa
    do consumidor vício do produto"). Devolve uma lista de dicts
    {titulo, ementa, data, tipo, urn, link} — lista vazia quando `termo`
    está vazio ou a busca não encontra nada (nunca inventa resultado).

    Levanta LexmlIndisponivelError em erro de rede/timeout/resposta
    malformada — chamado SEMPRE de forma síncrona a partir de uma rota web
    (nunca de dentro de `montar_digest_processo`, que precisa continuar
    determinístico/sem rede pros testes — ver
    app/routes/processos.py::gerar_analise_ia), então quem chama SEMPRE
    envolve isso num try/except e segue sem o bloco de legislação em caso
    de falha — a pesquisa é um reforço, nunca pode travar a geração do
    rascunho.
    """
    if not termo or not termo.strip():
        return []

    params = {
        "operation": "searchRetrieve",
        "version": "1.1",
        "query": _montar_query_cql(termo),
        "maximumRecords": str(max(1, min(limite, 20))),
    }
    try:
        resposta = requests.get(BASE_URL, params=params, timeout=timeout,
                                 headers={"User-Agent": "JusControl/1.0 (pesquisa de legislacao)"})
        resposta.raise_for_status()
    except requests.RequestException as e:
        raise LexmlIndisponivelError(f"não foi possível consultar o LexML: {e}") from e

    return _parse_resposta_sru(resposta.content)[:limite]
