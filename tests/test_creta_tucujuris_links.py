"""
Testes dos links soltos pro Creta (JFPE) e Tucujuris (TJAP) —
PENDENCIAS.md, seção -87. Mesma disciplina dos outros testes de link:
só confirmam que a URL certa aparece pro segmento certo — não existe
captura automática nem pré-preenchimento aqui (os dois pedem captcha em
toda busca, ver app/utils/creta_tucujuris_links.py).
"""
from app.utils.creta_tucujuris_links import links_outros_estadual, links_outros_federal


NUM_ESTADUAL_TJAP = "1234567-89.2023.8.03.0001"  # segmento "8"
NUM_FEDERAL_JFPE = "1234567-89.2023.4.05.8300"   # segmento "4"


def test_links_outros_estadual_para_numero_federal_fica_vazio():
    assert links_outros_estadual(NUM_FEDERAL_JFPE) == []


def test_links_outros_federal_para_numero_estadual_fica_vazio():
    assert links_outros_federal(NUM_ESTADUAL_TJAP) == []


def test_links_outros_estadual_traz_tucujuris():
    links = links_outros_estadual(NUM_ESTADUAL_TJAP)
    assert len(links) == 1
    assert "Tucujuris" in links[0]["rotulo"]
    assert links[0]["tipo"] == "link"
    assert links[0]["url"] == "https://tucujuris.tjap.jus.br/pages/consultar-processo/consultar-processo.html"


def test_links_outros_federal_traz_creta_jfpe():
    links = links_outros_federal(NUM_FEDERAL_JFPE)
    assert len(links) == 1
    assert "Creta" in links[0]["rotulo"]
    assert links[0]["tipo"] == "link"
    assert links[0]["url"] == "https://creta.jfpe.jus.br/cretainternetpe/consulta/processo/pesquisar.wsp"
