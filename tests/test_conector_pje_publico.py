"""
Conector PJe público (TJRJ, TJMG — sem certificado/token) — PENDENCIAS.md,
seção -78. Mesma cobertura de espírito do test_conector_esaj_publico.py:
parsing de uma página de detalhe (propriedades, partes, movimentações),
extração dos IDs de formulário gerados pelo JSF, rejeição de número CNJ de
outro segmento, "não encontrado" (inclusive quando é sigilo — a consulta
pública do PJe não distingue os dois casos, ver aviso no topo do módulo),
indisponibilidade de rede, e a rota que reaproveita o pipeline de captura
marcando origem_captura="pje_publico".

Os fixtures HTML abaixo replicam a estrutura de campos/IDs documentada
pelo pacote open source `juscraper` (MIT) para a família "ConsultaPública
do PJe" (`src/juscraper/courts/_trf/`) — a única implementação de
referência aberta e testada contra um PJe real que encontramos (ver aviso
completo em app/utils/conector_pje_publico.py sobre isso NÃO estar
confirmado ainda contra TJRJ/TJMG de verdade).
"""
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.extensions import db
from app.models import Processo, LogCaptura, Movimentacao
from app.utils import conector_pje_publico as mod
from app.utils.cnj import calcular_digito_verificador

FORM_HTML = """
<html><body><form id="fPP">
<input type="text" name="fPP:j_id100:processoReferenciaInput">
<input type="text" name="fPP:j_id101:nomeAdv">
<input type="text" name="fPP:j_id102:classeJudicial">
<input type="text" name="fPP:Decoration:j_id103">
<script>
A4J.AJAX.Submit('fPP',event,{'similarityGroupingId':'x','parameters':{'fPP:j_id104' : 'fPP:j_id104'} });
</script>
</form></body></html>
"""

SEARCH_ENCONTRADO = '<html><body><a href="DetalheProcessoConsultaPublica/listView.seam?ca=abc123">ver</a></body></html>'
SEARCH_NAO_ENCONTRADO = "<html><body>Nenhum resultado encontrado para os parâmetros informados.</body></html>"

DETAIL_HTML = """
<html><body>
<div class="propertyView"><span class="name">Número Processo</span><span class="value">1234567-89.2023.8.19.0001</span></div>
<div class="propertyView"><span class="name">Data da Distribuição</span><span class="value">10/01/2023</span></div>
<div class="propertyView"><span class="name">Classe Judicial</span><span class="value">Procedimento Comum Cível</span></div>
<div class="propertyView"><span class="name">Assunto</span><span class="value">Indenização por Dano Moral</span></div>
<div class="propertyView"><span class="name">Jurisdição</span><span class="value">Regional da Barra da Tijuca</span></div>
<div class="propertyView"><span class="name">Órgão Julgador</span><span class="value">1ª Vara Cível</span></div>
<table id="j_id50:processoPartesPoloAtivoResumidoList">
<tr><td></td><td>Joao da Silva</td><td>Ativo</td></tr>
</table>
<table id="j_id50:processoPartesPoloPassivoResumidoList">
<tr><td></td><td>Empresa XYZ Ltda</td><td>Citado</td></tr>
</table>
<table id="j_id50:processoEvento">
<tr><td>20/04/2023 10:00:00 - Distribuído por sorteio</td><td></td></tr>
<tr><td>25/04/2023 11:30:00 - Juntada de petição de contestação</td><td>ID: 1 - 2023-04-25 - Contestação</td></tr>
</table>
</body></html>
"""


def _numero_cnj(segmento, tribunal_codigo, sequencial="1234567", ano="2023", origem="0001"):
    dv = calcular_digito_verificador(sequencial, ano, segmento, tribunal_codigo, origem)
    return f"{sequencial}-{dv}.{ano}.{segmento}.{tribunal_codigo}.{origem}"


def _numero_cnj_estadual(tribunal_codigo="99"):
    """Código de tribunal fictício de propósito — este conector tenta os
    candidatos (TJRJ, TJMG) sem tentar adivinhar o tribunal certo pelo
    número, mesma disciplina de app/utils/conector_esaj_publico.py."""
    return _numero_cnj("8", tribunal_codigo)


def _resp(texto=None, conteudo_bytes=None, status=200):
    r = MagicMock()
    r.status_code = status
    if conteudo_bytes is not None:
        r.content = conteudo_bytes
    else:
        r.text = texto
        r.content = (texto or "").encode("utf-8")
    return r


def _mock_fluxo(por_dominio):
    """por_dominio: dict domínio-substring -> {"form":.., "busca":.., "detalhe":..}
    (algumas chaves podem faltar quando o teste não deveria chegar lá —
    nesse caso uma URL inesperada levanta AssertionError, não silencia)."""

    def _get(self, url, **kwargs):
        for dominio, cfg in por_dominio.items():
            if dominio not in url:
                continue
            # DETAIL_PATH precisa ser checado ANTES de LISTVIEW_PATH: a URL
            # de detalhe também termina em ".../listView.seam" (e contém
            # "ConsultaPublica" como sufixo de "DetalheProcessoConsultaPublica"),
            # então um endswith(LISTVIEW_PATH) sozinho casaria com as duas.
            if mod.DETAIL_PATH in url:
                return _resp(conteudo_bytes=cfg["detalhe"].encode("latin-1"))
            if url.endswith(mod.LISTVIEW_PATH):
                return _resp(texto=cfg["form"])
        raise AssertionError(f"GET não esperado neste teste: {url}")

    def _post(self, url, **kwargs):
        for dominio, cfg in por_dominio.items():
            if dominio in url:
                return _resp(texto=cfg["busca"])
        raise AssertionError(f"POST não esperado neste teste: {url}")

    return _get, _post


# ---------- Parsing (sem rede) ----------

def test_parse_processo_completo():
    resultado = mod._parse_processo(DETAIL_HTML, "12345678920238190001", "tjrj")
    assert resultado["classe"] == "Procedimento Comum Cível"
    assert resultado["assunto"] == "Indenização por Dano Moral"
    assert resultado["comarca"] == "Regional da Barra da Tijuca"
    assert resultado["orgao_julgador"] == "1ª Vara Cível"
    assert resultado["tribunal_slug"] == "tjrj"
    assert resultado["sistema"] == "PJe"

    partes = resultado["partes"]
    assert len(partes) == 2
    assert partes[0] == {"tipo": "Polo Ativo", "nome": "Joao da Silva", "advogados": []}
    assert partes[1]["tipo"] == "Polo Passivo"
    assert partes[1]["nome"] == "Empresa XYZ Ltda"
    # PJe não expõe advogado na tabela de partes da consulta pública
    # (diferente do e-SAJ) — ver aviso no topo do módulo.
    assert partes[1]["advogados"] == []

    movs = resultado["movimentacoes"]
    assert len(movs) == 2
    assert movs[0].texto_integral == "Distribuído por sorteio"
    assert movs[0].codigo_tpu is None
    assert movs[1].complemento == "ID: 1 - 2023-04-25 - Contestação"


def test_rejeita_numero_cnj_de_outro_segmento():
    conector = mod.ConectorPjePublico()
    with pytest.raises(mod.ErroPjePublico, match="só sabe consultar tribunais estaduais"):
        conector.consultar_processo("1234567-89.2023.4.03.0100")


def test_metodos_nao_cobertos_levantam_notimplemented():
    conector = mod.ConectorPjePublico()
    with pytest.raises(NotImplementedError):
        conector.monitorar_publicacoes_por_oab("123456", "RJ")
    with pytest.raises(NotImplementedError):
        conector.buscar_processos_por_parte(nome="Fulano")


# ---------- Fluxo completo (com rede mockada) ----------

def test_consulta_publica_sem_nenhuma_credencial():
    """Nenhum header de Authorization, cookie ou credencial é enviado —
    mesmo requisito que motivou o e-SAJ público."""
    numero = _numero_cnj_estadual()
    respostas = {
        "tjrj.pje.jus.br": {"form": FORM_HTML, "busca": SEARCH_ENCONTRADO, "detalhe": DETAIL_HTML},
        "pje-consulta-publica.tjmg.jus.br": {"form": FORM_HTML, "busca": SEARCH_NAO_ENCONTRADO},
    }
    _get, _post = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get), patch.object(requests.Session, "post", _post):
        resultado = mod.ConectorPjePublico().consultar_processo(numero)
    assert resultado["tribunal_slug"] == "tjrj"
    # Nenhuma chamada usou "auth"/Authorization — checado indiretamente:
    # _mock_fluxo não aceita nenhum kwarg de credencial, e o conector
    # nunca configura nenhum (ver _nova_sessao(), só User-Agent).


def test_tenta_proximo_tribunal_ate_achar():
    numero = _numero_cnj_estadual()
    respostas = {
        "tjrj.pje.jus.br": {"form": FORM_HTML, "busca": SEARCH_NAO_ENCONTRADO},
        "pje-consulta-publica.tjmg.jus.br": {"form": FORM_HTML, "busca": SEARCH_ENCONTRADO, "detalhe": DETAIL_HTML},
    }
    _get, _post = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get), patch.object(requests.Session, "post", _post):
        resultado = mod.ConectorPjePublico().consultar_processo(numero)
    assert resultado["tribunal_slug"] == "tjmg"
    assert resultado["classe"] == "Procedimento Comum Cível"


def test_nao_encontrado_em_nenhum_tribunal_pje():
    """Cobre também o caso de sigilo: a consulta pública do PJe não avisa
    "protegido por senha" como o e-SAJ — sigilo cai exatamente aqui, como
    "não encontrado" (ver aviso no topo do módulo)."""
    numero = _numero_cnj_estadual()
    respostas = {
        "tjrj.pje.jus.br": {"form": FORM_HTML, "busca": SEARCH_NAO_ENCONTRADO},
        "pje-consulta-publica.tjmg.jus.br": {"form": FORM_HTML, "busca": SEARCH_NAO_ENCONTRADO},
    }
    _get, _post = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get), patch.object(requests.Session, "post", _post):
        with pytest.raises(mod.ErroPjePublico, match="não encontrado em nenhum dos tribunais PJe testados") as exc:
            mod.ConectorPjePublico().consultar_processo(numero)
    assert "segredo de justiça" in str(exc.value)
    assert "TJRJ" in str(exc.value) and "TJMG" in str(exc.value)


def test_erro_de_rede_vira_pje_indisponivel():
    numero = _numero_cnj_estadual()

    def _get_falha(self, url, **kwargs):
        raise requests.RequestException("timeout")

    with patch.object(requests.Session, "get", _get_falha):
        with pytest.raises(mod.PjeIndisponivelError):
            mod.ConectorPjePublico().consultar_processo(numero)


def test_formulario_com_estrutura_inesperada_nao_quebra():
    """Se a página do formulário não tiver os campos esperados (estrutura
    diferente do que confirmamos via juscraper — ver aviso no topo do
    módulo sobre isto não estar testado contra TJRJ/TJMG de verdade),
    levanta um erro claro em vez de deixar uma exceção crua (KeyError,
    AttributeError etc.) vazar."""
    numero = _numero_cnj_estadual()
    respostas = {
        "tjrj.pje.jus.br": {"form": "<html><body>página completamente diferente</body></html>", "busca": ""},
        "pje-consulta-publica.tjmg.jus.br": {"form": "<html><body>página completamente diferente</body></html>", "busca": ""},
    }
    _get, _post = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get), patch.object(requests.Session, "post", _post):
        with pytest.raises(mod.ErroPjePublico, match="Não foi possível consultar nenhum dos tribunais PJe testados|não encontrado"):
            mod.ConectorPjePublico().consultar_processo(numero)


# ---------- Rota (reaproveita o pipeline de captura) ----------

@pytest.fixture()
def processo_rj(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin-pje@teste.com", papel="admin", nome="Admin Pje")
    from app.models import Cliente
    cliente = Cliente(nome="Cliente Teste PJe", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo="1234567-89.2023.8.19.0001", cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Cível", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "admin_id": admin_id}


def test_rota_tentar_captura_pje_sucesso(client, login, post_csrf, processo_rj):
    login("admin-pje@teste.com")
    with patch.object(mod.ConectorPjePublico, "consultar_processo",
                       return_value=mod._parse_processo(DETAIL_HTML, "12345678920238190001", "tjrj")):
        r = post_csrf(f"/governanca/processos/{processo_rj['processo_id']}/tentar-captura-pje", {},
                      get_url=f"/processos/{processo_rj['processo_id']}")
    assert r.status_code == 200

    processo = db.session.get(Processo, processo_rj["processo_id"])
    assert processo.classe_processual == "Procedimento Comum Cível"

    movs = Movimentacao.query.filter_by(processo_id=processo.id).all()
    assert len(movs) == 2
    assert all(m.origem_captura == "pje_publico" for m in movs)

    log = LogCaptura.query.filter_by(processo_id=processo.id, fonte="pje_publico").first()
    assert log is not None
    assert log.status == "sucesso"
    assert log.tribunal == "tjrj"


def test_rota_tentar_captura_pje_falha_registra_log(client, login, post_csrf, processo_rj):
    login("admin-pje@teste.com")
    with patch.object(mod.ConectorPjePublico, "consultar_processo",
                       side_effect=mod.PjeIndisponivelError("PJe fora do ar")):
        r = post_csrf(f"/governanca/processos/{processo_rj['processo_id']}/tentar-captura-pje", {},
                      get_url=f"/processos/{processo_rj['processo_id']}")
    assert r.status_code == 200

    log = LogCaptura.query.filter_by(processo_id=processo_rj["processo_id"], fonte="pje_publico").first()
    assert log is not None
    assert log.status == "falha"
