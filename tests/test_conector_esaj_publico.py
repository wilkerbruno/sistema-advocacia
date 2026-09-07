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
from app.utils.cnj import calcular_digito_verificador

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


def _numero_cnj(segmento, tribunal_codigo, sequencial="1234567", ano="2023", origem="0100"):
    """Monta um número CNJ válido (dígito verificador de verdade) com o
    segmento/tribunal informados — usado nos testes abaixo pra não
    depender de um número de processo real."""
    dv = calcular_digito_verificador(sequencial, ano, segmento, tribunal_codigo, origem)
    return f"{sequencial}-{dv}.{ano}.{segmento}.{tribunal_codigo}.{origem}"


def _numero_cnj_estadual(tribunal_codigo, sequencial="1234567", ano="2023", origem="0100"):
    """Monta um número CNJ válido (segmento 8 = Estadual) com o código de
    tribunal informado — usado para testar o "tenta cada tribunal e-SAJ
    até achar" (PENDENCIAS.md, seção -72), que vale para qualquer código
    diferente de "26" (TJSP), sem precisar de um código real/confirmado."""
    return _numero_cnj("8", tribunal_codigo, sequencial, ano, origem)


HTML_NAO_ENCONTRADO = "<html><body>Nenhum processo localizado com os parâmetros informados.</body></html>"
HTML_SENHA = '<html><body><form id="popupSenha"></form></body></html>'


def _get_por_dominio(respostas):
    """respostas: dict domínio-substring -> html (string) OU Exception a levantar."""
    def _get(url, **kwargs):
        for dominio, valor in respostas.items():
            if dominio in url:
                if isinstance(valor, Exception):
                    raise valor
                return _resposta_fake(valor)
        raise AssertionError(f"URL não esperada neste teste: {url}")
    return _get


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


def test_rejeita_numero_cnj_de_outro_segmento():
    conector = mod.ConectorEsajPublico()
    with pytest.raises(mod.ErroEsajPublico, match="só sabe consultar tribunais estaduais"):
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


# ---------- Múltiplos tribunais e-SAJ (PENDENCIAS.md, seção -72) ----------
# TJSP (código "26") tem caminho próprio, testado acima. Os testes abaixo
# cobrem o "tenta cada um dos outros tribunais e-SAJ até achar" — usam um
# código de tribunal fictício ("99") de propósito: o comportamento testado
# aqui (tentar TJAC/TJAL/TJAM/TJCE/TJMS em ordem) vale pra QUALQUER código
# diferente de "26", já que não existe tabela confiável pra saber qual
# tribunal de verdade cada código representa (ver aviso no topo do módulo).

def test_tenta_proximo_tribunal_ate_achar():
    numero = _numero_cnj_estadual("99")
    conector = mod.ConectorEsajPublico()
    respostas = {
        "esaj.tjac.jus.br": HTML_NAO_ENCONTRADO,
        "www2.tjal.jus.br": HTML_NAO_ENCONTRADO,
        "consultasaj.tjam.jus.br": HTML_PROCESSO,  # achou aqui — não deveria chegar no tjce/tjms
    }
    with patch.object(requests.Session, "get", side_effect=_get_por_dominio(respostas)):
        resultado = conector.consultar_processo(numero)
    assert resultado["tribunal_slug"] == "tjam"
    assert resultado["classe"] == "Procedimento Comum Cível"


def test_nao_encontrado_em_nenhum_tribunal_esaj():
    numero = _numero_cnj_estadual("99")
    conector = mod.ConectorEsajPublico()
    respostas = {
        "esaj.tjac.jus.br": HTML_NAO_ENCONTRADO,
        "www2.tjal.jus.br": HTML_NAO_ENCONTRADO,
        "consultasaj.tjam.jus.br": HTML_NAO_ENCONTRADO,
        "esaj.tjce.jus.br": HTML_NAO_ENCONTRADO,
        "esaj.tjms.jus.br": HTML_NAO_ENCONTRADO,
    }
    with patch.object(requests.Session, "get", side_effect=_get_por_dominio(respostas)):
        with pytest.raises(mod.ErroEsajPublico, match="não encontrado em nenhum dos tribunais e-SAJ testados"):
            conector.consultar_processo(numero)


def test_senha_em_um_tribunal_leva_a_esajprotegidoporsenhaerror():
    """Desde a seção -73 os 5 candidatos são consultados EM PARALELO (não
    mais um de cada vez) — não dá mais pra afirmar "parou depois de 2
    chamadas", já que as 5 requisições saem juntas; o que continua valendo
    é o RESULTADO: achar a senha em qualquer um deles vira
    EsajProtegidoPorSenhaError, citando o tribunal certo."""
    numero = _numero_cnj_estadual("99")
    conector = mod.ConectorEsajPublico()
    respostas = {
        "esaj.tjac.jus.br": HTML_NAO_ENCONTRADO,
        "www2.tjal.jus.br": HTML_SENHA,
        "consultasaj.tjam.jus.br": HTML_NAO_ENCONTRADO,
        "esaj.tjce.jus.br": HTML_NAO_ENCONTRADO,
        "esaj.tjms.jus.br": HTML_NAO_ENCONTRADO,
    }
    with patch.object(requests.Session, "get", side_effect=_get_por_dominio(respostas)):
        with pytest.raises(mod.EsajProtegidoPorSenhaError, match="TJAL"):
            conector.consultar_processo(numero)


def test_todos_tribunais_indisponiveis_vira_esaj_indisponivel():
    numero = _numero_cnj_estadual("99")
    conector = mod.ConectorEsajPublico()
    with patch.object(requests.Session, "get", side_effect=requests.RequestException("timeout")):
        with pytest.raises(mod.EsajIndisponivelError, match="Não foi possível consultar nenhum"):
            conector.consultar_processo(numero)


def test_mensagem_de_erro_nomeia_tribunais_indisponiveis():
    """Seção -73 — relato real de produção: a mensagem antiga só dizia "2
    tribunal(is) não respondeu/responderam", sem dizer QUAIS, o que
    impedia diagnosticar se era um bloqueio específico e permanente de um
    tribunal. Agora nomeia exatamente quais não responderam."""
    numero = _numero_cnj_estadual("99")
    conector = mod.ConectorEsajPublico()
    respostas = {
        "esaj.tjac.jus.br": HTML_NAO_ENCONTRADO,
        "www2.tjal.jus.br": HTML_NAO_ENCONTRADO,
        "consultasaj.tjam.jus.br": HTML_NAO_ENCONTRADO,
        "esaj.tjce.jus.br": requests.RequestException("timeout"),
        "esaj.tjms.jus.br": requests.RequestException("timeout"),
    }
    with patch.object(requests.Session, "get", side_effect=_get_por_dominio(respostas)):
        with pytest.raises(mod.ErroEsajPublico) as excinfo:
            conector.consultar_processo(numero)
    mensagem = str(excinfo.value)
    assert "TJCE" in mensagem
    assert "TJMS" in mensagem
    assert "não responderam a tempo" in mensagem
    # Os que responderam limpo "não encontrado" não entram na lista de indisponíveis.
    assert "TJAC" not in mensagem.split("não responderam a tempo")[0].split("(")[-1]


def test_timeout_dos_candidatos_e_menor_que_o_do_tjsp():
    """Seção -73: o timeout por tribunal na busca em paralelo caiu pra
    10s (o do TJSP, que é 1 requisição só, continua 20s) — evita que um
    único tribunal fora do ar deixe a busca inteira lenta."""
    assert mod.TIMEOUT_CANDIDATOS_SEGUNDOS < mod.TIMEOUT_SEGUNDOS


def test_tjce_usa_sessao_separada_com_tls_seclevel1():
    conector = mod.ConectorEsajPublico()
    tjce = mod.CANDIDATOS_DEMAIS_TRIBUNAIS[3]
    assert tjce.slug == "tjce"
    sessao_normal = conector._sessao_para(mod.TJSP)
    sessao_tjce = conector._sessao_para(tjce)
    assert sessao_normal is conector.session
    assert sessao_tjce is not conector.session
    assert isinstance(sessao_tjce.adapters["https://"], mod._TJCETLSAdapter)
    # Chamar de novo devolve a MESMA sessão (não recria a cada busca).
    assert conector._sessao_para(tjce) is sessao_tjce


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


# ---------- Pré-visualização "Novo processo": fallback DataJud -> e-SAJ
# público (PENDENCIAS.md, seção -72) ----------
# O ambiente de teste não tem DATAJUD_API_KEY configurada (ver
# config.py) — então, sem precisar mockar nada do DataJud, toda chamada a
# esta rota já cai naturalmente no caminho "DataJud não configurado", que é
# exatamente o cenário que deveria acionar o fallback pro e-SAJ público.

@pytest.fixture()
def usuario_preview(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    criar_usuario(unidade_id, "admin-preview@teste.com", papel="admin", nome="Admin Preview")
    return {"unidade_id": unidade_id}


def test_preview_cnj_cai_no_esaj_publico_quando_datajud_nao_configurado(client, login, usuario_preview):
    login("admin-preview@teste.com")
    numero = "1234567-89.2023.8.26.0100"  # TJSP
    with patch.object(mod.ConectorEsajPublico, "consultar_processo",
                       return_value=mod._parse_processo(HTML_PROCESSO, "12345678920238260100", "tjsp")):
        r = client.get(f"/governanca/processos/consultar-cnj?numero_cnj={numero}")
    dados = r.get_json()
    assert dados["valido"] is True
    assert dados["encontrado"] is True
    assert dados["fonte"] == "e-SAJ público"
    assert dados["classe"] == "Procedimento Comum Cível"
    assert dados["tribunal_slug"] == "tjsp"


def test_preview_cnj_nao_encontrado_nem_no_datajud_nem_no_esaj(client, login, usuario_preview):
    login("admin-preview@teste.com")
    numero = "1234567-89.2023.8.26.0100"
    with patch.object(mod.ConectorEsajPublico, "consultar_processo",
                       side_effect=mod.ErroEsajPublico("não encontrado em nenhum dos tribunais e-SAJ testados (…)")):
        r = client.get(f"/governanca/processos/consultar-cnj?numero_cnj={numero}")
    dados = r.get_json()
    assert dados["valido"] is True
    assert dados["encontrado"] is False
    # Motivo combinado — dá pra ver que as DUAS fontes foram tentadas, não só uma.
    assert "DATAJUD_API_KEY" in dados["motivo"]
    assert "e-SAJ público" in dados["motivo"]


def test_preview_cnj_fora_da_justica_estadual_nao_tenta_esaj(client, login, usuario_preview):
    login("admin-preview@teste.com")
    numero_trabalhista = _numero_cnj("5", "02")  # segmento 5 = Justiça do Trabalho
    with patch.object(mod.ConectorEsajPublico, "consultar_processo") as m:
        r = client.get(f"/governanca/processos/consultar-cnj?numero_cnj={numero_trabalhista}")
    m.assert_not_called()
    dados = r.get_json()
    assert dados["valido"] is True
    assert dados["encontrado"] is False
