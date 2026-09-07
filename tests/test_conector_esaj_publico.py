"""
Conector e-SAJ público (TJSP, sem certificado/token) — PENDENCIAS.md,
seção -71. Cobre: parsing de uma página real de processo (classe,
assunto, foro, vara, partes, movimentações, valor, data), rejeição de
números CNJ de outro tribunal, detecção de processo protegido por
senha, indisponibilidade de rede, e a rota que reaproveita o pipeline
de captura (mesmo padrão do DataJud) marcando origem_captura="esaj_publico".

O fixture HTML abaixo replica exatamente os IDs/classes confirmados no
pacote open source `juscraper` (MIT) contra o e-SAJ de verdade — ver
aviso completo em app/utils/conector_esaj_publico.py.
"""
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.extensions import db
from app.models import Processo, LogCaptura, Movimentacao
from app.utils import conector_esaj_publico as mod

HTML_PROCESSO = """
<html><body>
<div id="containerDadosPrincipaisProcesso">
  <span id="numeroProcesso">1234567-89.2023.8.26.0100</span>
  <span id="classeProcesso">Procedimento Comum Cível</span>
  <span id="assuntoProcesso">Indenização por Dano Moral</span>
  <span id="foroProcesso">Foro Central Cível</span>
  <span id="varaProcesso">1ª Vara Cível</span>
  <div id="dataHoraDistribuicaoProcesso">19/04/2023 às 12:27 - Livre</div>
  <div id="valorAcaoProcesso">R$ 12.345,67</div>
</div>
<table id="tablePartesPrincipais">
  <tr class="fundoClaro">
    <td><span class="tipoDeParticipacao">Reqte</span></td>
    <td>Joao da Silva<br>Advogado:<br>Maria Advogada</td>
  </tr>
  <tr class="fundoEscuro">
    <td><span class="tipoDeParticipacao">Reqdo</span></td>
    <td>Empresa XYZ Ltda</td>
  </tr>
</table>
<tbody id="tabelaTodasMovimentacoes">
  <tr class="containerMovimentacao">
    <td>20/04/2023</td><td></td>
    <td>Distribuído por sorteio<span style="font-style: italic;">Observação</span></td>
  </tr>
  <tr class="containerMovimentacao">
    <td>25/04/2023</td><td></td>
    <td>Juntada de petição de contestação</td>
  </tr>
</tbody>
</body></html>
"""


def _resposta_fake(html, status=200):
    r = MagicMock()
    r.status_code = status
    r.text = html
    return r


def test_parse_processo_completo():
    resultado = mod._parse_processo(HTML_PROCESSO, "12345678920238260100")
    assert resultado["classe"] == "Procedimento Comum Cível"
    assert resultado["assunto"] == "Indenização por Dano Moral"
    assert resultado["comarca"] == "Foro Central Cível"
    assert resultado["orgao_julgador"] == "1ª Vara Cível"
    assert resultado["valor_causa"] == 12345.67
    assert len(resultado["partes"]) == 2
    assert resultado["partes"][0]["nome"] == "Joao da Silva"
    assert resultado["partes"][0]["advogados"] == ["Maria Advogada"]
    assert len(resultado["movimentacoes"]) == 2
    assert resultado["movimentacoes"][0].complemento == "Observação"
    assert resultado["movimentacoes"][0].codigo_tpu is None


def test_rejeita_numero_cnj_de_outro_tribunal():
    conector = mod.ConectorEsajPublico()
    with pytest.raises(mod.ErroEsajPublico, match="só sabe consultar o TJSP"):
        conector.consultar_processo("1234567-89.2023.4.03.0100")


def test_consulta_publica_sem_nenhuma_credencial():
    """Nenhum header de Authorization, cookie ou credencial é enviado —
    é exatamente o requisito que motivou este conector."""
    conector = mod.ConectorEsajPublico()
    assert "Authorization" not in conector.session.headers
    assert not conector.session.cookies
    with patch.object(conector.session, "get", return_value=_resposta_fake(HTML_PROCESSO)) as m:
        conector.consultar_processo("1234567-89.2023.8.26.0100")
        _, kwargs = m.call_args
        assert "auth" not in kwargs


def test_processo_protegido_por_senha():
    conector = mod.ConectorEsajPublico()
    html = '<html><body><form id="popupSenha"></form></body></html>'
    with patch.object(conector.session, "get", return_value=_resposta_fake(html)):
        with pytest.raises(mod.EsajProtegidoPorSenhaError):
            conector.consultar_processo("1234567-89.2023.8.26.0100")


def test_processo_nao_encontrado():
    conector = mod.ConectorEsajPublico()
    with patch.object(conector.session, "get", return_value=_resposta_fake("<html></html>")):
        with pytest.raises(mod.ErroEsajPublico, match="não encontrado"):
            conector.consultar_processo("1234567-89.2023.8.26.0100")


def test_erro_de_rede_vira_esaj_indisponivel():
    conector = mod.ConectorEsajPublico()
    with patch.object(conector.session, "get", side_effect=requests.RequestException("timeout")):
        with pytest.raises(mod.EsajIndisponivelError):
            conector.consultar_processo("1234567-89.2023.8.26.0100")


def test_metodos_nao_cobertos_levantam_notimplemented():
    conector = mod.ConectorEsajPublico()
    with pytest.raises(NotImplementedError):
        conector.monitorar_publicacoes_por_oab("123456", "SP")
    with pytest.raises(NotImplementedError):
        conector.buscar_processos_por_parte(nome="Fulano")


# ---------- Rota (reaproveita o pipeline de captura) ----------

@pytest.fixture()
def processo_tjsp(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin-esaj@teste.com", papel="admin", nome="Admin Esaj")
    from app.models import Cliente
    cliente = Cliente(nome="Cliente Teste", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo="1234567-89.2023.8.26.0100", cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Cível", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "admin_id": admin_id}


def test_rota_tentar_captura_esaj_sucesso(client, login, post_csrf, processo_tjsp):
    login("admin-esaj@teste.com")
    with patch.object(mod.ConectorEsajPublico, "consultar_processo",
                       return_value=mod._parse_processo(HTML_PROCESSO, "12345678920238260100")):
        r = post_csrf(f"/governanca/processos/{processo_tjsp['processo_id']}/tentar-captura-esaj", {},
                      get_url=f"/processos/{processo_tjsp['processo_id']}")
    assert r.status_code == 200

    processo = db.session.get(Processo, processo_tjsp["processo_id"])
    assert processo.classe_processual == "Procedimento Comum Cível"

    movs = Movimentacao.query.filter_by(processo_id=processo.id).all()
    assert len(movs) == 2
    assert all(m.origem_captura == "esaj_publico" for m in movs)

    log = LogCaptura.query.filter_by(processo_id=processo.id, fonte="esaj_publico").first()
    assert log is not None
    assert log.status == "sucesso"


def test_rota_tentar_captura_esaj_falha_registra_log(client, login, post_csrf, processo_tjsp):
    login("admin-esaj@teste.com")
    with patch.object(mod.ConectorEsajPublico, "consultar_processo",
                       side_effect=mod.EsajIndisponivelError("e-SAJ fora do ar")):
        r = post_csrf(f"/governanca/processos/{processo_tjsp['processo_id']}/tentar-captura-esaj", {},
                      get_url=f"/processos/{processo_tjsp['processo_id']}")
    assert r.status_code == 200

    log = LogCaptura.query.filter_by(processo_id=processo_tjsp["processo_id"], fonte="esaj_publico").first()
    assert log is not None
    assert log.status == "falha"
