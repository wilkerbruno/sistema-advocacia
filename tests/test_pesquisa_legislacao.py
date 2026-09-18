"""
Item 8 da lista de pipeline de IA jurídica trazida pelo usuário
(PENDENCIAS.md, seção -108): "Pesquisa vinculada — Legislação específica
... buscada em base real, com ementa e link. Citação não verificada é
bloqueada antes de chegar ao texto."

Cobre três frentes:
- app/utils/lexml.py: parsing da resposta SRU (feliz, registro sem urn
  ignorado, XML malformado levanta erro), montagem da query CQL, e
  `buscar_legislacao` (termo vazio, erro de rede, sucesso mockado) — rede
  sempre mockada (`unittest.mock.patch`), nunca uma chamada real, mesmo
  padrão já usado em tests/test_conector_esaj_publico.py.
- app/utils/analise_processo_ia.py: o resultado de `buscar_legislacao` só
  entra no digest quando passado explicitamente (`legislacao_relacionada`),
  e uma citação legal que bate com esse bloco deixa de exigir o marcador
  [REVISAR] em `_checar_grounding` — sem afetar o comportamento de sempre
  quando nenhuma pesquisa foi feita.
- app/routes/processos.py::gerar_analise_ia: o checkbox "buscar_legislacao"
  dispara a busca ANTES de enfileirar (mesmo padrão de
  tests/test_referencia_estilo_minuta.py pro texto_referencia), e uma falha
  do LexML nunca bloqueia a geração, só avisa.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.extensions import db
from app.models import Cliente, Processo, AnaliseProcessoIA
from app.utils import lexml as mod
from app.utils.lexml import buscar_legislacao, LexmlIndisponivelError, _parse_resposta_sru, _montar_query_cql
from app.utils.analise_processo_ia import (
    gerar_analise, montar_digest_processo, _checar_grounding, _montar_bloco_legislacao_relacionada,
)


# ---------- fixtures ----------

@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    empresa_id = empresa_basica["empresa_id"]
    usuario_id = criar_usuario(unidade_id, "legislacao@teste.com", papel="advogado", nome="Advogado Legislação")

    cliente = Cliente(nome="Cliente Legislação", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-LEX-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id, criado_por_id=usuario_id)
    db.session.add(processo)
    db.session.commit()

    return dict(usuario_id=usuario_id, unidade_id=unidade_id, empresa_id=empresa_id, processo_id=processo.id)


XML_SRU_OK = """<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>2</numberOfRecords>
  <records>
    <record>
      <recordData>
        <srw_dc:dc xmlns:srw_dc="info:srw/schema/1/dc-schema" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Lei nº 8.078, de 11 de setembro de 1990</dc:title>
          <dc:date>1990-09-11</dc:date>
          <dc:description>Dispõe sobre a proteção do consumidor e dá outras providências.</dc:description>
          <urn>urn:lex:br:federal:lei:1990-09-11;8078</urn>
          <tipoDocumento>Lei</tipoDocumento>
        </srw_dc:dc>
      </recordData>
    </record>
    <record>
      <recordData>
        <srw_dc:dc xmlns:srw_dc="info:srw/schema/1/dc-schema" xmlns:dc="http://purl.org/dc/elements/1.1/">
          <dc:title>Registro sem URN (deve ser ignorado)</dc:title>
          <dc:description>Sem urn não dá pra montar link nenhum.</dc:description>
        </srw_dc:dc>
      </recordData>
    </record>
  </records>
</searchRetrieveResponse>
"""


def _resposta_fake(conteudo, status=200):
    r = MagicMock()
    r.status_code = status
    r.content = conteudo.encode("utf-8") if isinstance(conteudo, str) else conteudo
    r.raise_for_status = MagicMock()
    return r


# ---------- _montar_query_cql ----------

def test_monta_query_cql_busca_titulo_e_descricao():
    query = _montar_query_cql("código de defesa do consumidor")
    assert 'dc.title any "código de defesa do consumidor"' in query
    assert 'dc.description any "código de defesa do consumidor"' in query


def test_monta_query_cql_escapa_aspas():
    query = _montar_query_cql('termo com "aspas"')
    assert '\\"aspas\\"' in query


# ---------- _parse_resposta_sru ----------

def test_parse_resposta_sru_extrai_campos_e_ignora_registro_sem_urn():
    registros = _parse_resposta_sru(XML_SRU_OK)
    assert len(registros) == 1  # o segundo registro (sem urn) foi ignorado
    r = registros[0]
    assert r["titulo"] == "Lei nº 8.078, de 11 de setembro de 1990"
    assert "proteção do consumidor" in r["ementa"]
    assert r["data"] == "1990-09-11"
    assert r["urn"] == "urn:lex:br:federal:lei:1990-09-11;8078"
    assert r["link"] == "https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1990-09-11;8078"


def test_parse_resposta_sru_xml_malformado_levanta_erro():
    with pytest.raises(LexmlIndisponivelError):
        _parse_resposta_sru("isto não é XML válido <<<")


def test_parse_resposta_sru_sem_recordData_devolve_lista_vazia():
    assert _parse_resposta_sru("<vazio></vazio>") == []


# ---------- buscar_legislacao ----------

def test_buscar_legislacao_termo_vazio_nao_faz_chamada_de_rede():
    with patch.object(requests, "get") as m:
        assert buscar_legislacao("") == []
        assert buscar_legislacao("   ") == []
        m.assert_not_called()


def test_buscar_legislacao_sucesso_mockado():
    with patch.object(mod.requests, "get", return_value=_resposta_fake(XML_SRU_OK)) as m:
        resultado = buscar_legislacao("consumidor vício do produto", limite=5)
    assert len(resultado) == 1
    assert resultado[0]["titulo"].startswith("Lei nº 8.078")
    # confere que a busca foi de fato pro endpoint SRU do LexML, em CQL
    chamada = m.call_args
    assert chamada.args[0] == mod.BASE_URL
    assert "consumidor" in chamada.kwargs["params"]["query"]


def test_buscar_legislacao_respeita_limite():
    with patch.object(mod.requests, "get", return_value=_resposta_fake(XML_SRU_OK)):
        resultado = buscar_legislacao("consumidor", limite=1)
    assert len(resultado) <= 1


def test_buscar_legislacao_erro_de_rede_levanta_erro_proprio():
    with patch.object(mod.requests, "get", side_effect=requests.RequestException("timeout")):
        with pytest.raises(LexmlIndisponivelError):
            buscar_legislacao("consumidor")


def test_buscar_legislacao_http_erro_levanta_erro_proprio():
    resposta = _resposta_fake(XML_SRU_OK)
    resposta.raise_for_status.side_effect = requests.HTTPError("500")
    with patch.object(mod.requests, "get", return_value=resposta):
        with pytest.raises(LexmlIndisponivelError):
            buscar_legislacao("consumidor")


# ---------- _montar_bloco_legislacao_relacionada / montar_digest_processo ----------

def test_montar_bloco_legislacao_vazio_devolve_none():
    assert _montar_bloco_legislacao_relacionada(None) is None
    assert _montar_bloco_legislacao_relacionada([]) is None


def test_montar_bloco_legislacao_monta_linha_com_titulo_ementa_e_link():
    linhas = _montar_bloco_legislacao_relacionada([
        {"titulo": "Lei nº 8.078/1990", "data": "1990-09-11", "ementa": "Código de Defesa do Consumidor.",
         "link": "https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1990-09-11;8078"},
    ])
    assert len(linhas) == 1
    assert "Lei nº 8.078/1990" in linhas[0]
    assert "Código de Defesa do Consumidor" in linhas[0]
    assert "lexml.gov.br" in linhas[0]


def test_digest_sem_legislacao_relacionada_nao_muda(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        digest_sem, _ = montar_digest_processo(processo)
        digest_com_vazio, _ = montar_digest_processo(processo, legislacao_relacionada=[])
        assert "LexML" not in digest_sem
        assert "LexML" not in digest_com_vazio


def test_digest_com_legislacao_relacionada_inclui_bloco(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        digest, _ = montar_digest_processo(processo, legislacao_relacionada=[
            {"titulo": "Lei nº 8.078/1990", "data": "1990-09-11", "ementa": "Código de Defesa do Consumidor.",
             "link": "https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1990-09-11;8078"},
        ])
        assert "Lei nº 8.078/1990" in digest
        assert "LexML" in digest


# ---------- _checar_grounding + legislação pesquisada ----------

def test_citacao_que_bate_com_legislacao_pesquisada_nao_e_sinalizada():
    digest = ("Processo x.\n\nLegislação real encontrada na pesquisa (LexML) — só cite lei/artigo/súmula "
              "daqui sem precisar de [REVISAR]:\n- Lei nº 8.078/1990 (1990-09-11): Código de Defesa do "
              "Consumidor. — https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1990-09-11;8078")
    resultado = "Aplica-se a Lei nº 8.078/1990, que trata do vício do produto."
    avisos = _checar_grounding(resultado, digest)
    assert avisos == []


def test_citacao_que_nao_bate_com_nada_continua_sinalizada_mesmo_com_pesquisa_no_digest():
    digest = ("Processo x.\n\nLegislação real encontrada na pesquisa (LexML) — só cite lei/artigo/súmula "
              "daqui sem precisar de [REVISAR]:\n- Lei nº 8.078/1990 (1990-09-11): Código de Defesa do "
              "Consumidor. — https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1990-09-11;8078")
    resultado = "Aplica-se o art. 999999 do Código Civil, que trata do assunto."
    avisos = _checar_grounding(resultado, digest)
    assert len(avisos) == 1
    assert "art. 999999" in avisos[0]


def test_sem_pesquisa_de_legislacao_comportamento_de_sempre_nao_muda():
    digest = "Processo x. Sem nenhum bloco de legislação pesquisada."
    resultado = "Aplica-se a Lei nº 8.078/1990 ao caso."
    avisos = _checar_grounding(resultado, digest)
    assert len(avisos) == 1
    assert "Lei nº 8.078/1990" in avisos[0]


def test_citacao_com_revisar_continua_nunca_sinalizada():
    digest = "Processo x."
    resultado = "Aplica-se a Lei nº 8.078/1990 [REVISAR: confirmar número da lei] ao caso."
    avisos = _checar_grounding(resultado, digest)
    assert avisos == []


# ---------- gerar_analise (integração ponta a ponta, modelo mockado) ----------

def test_gerar_analise_resumo_aceita_legislacao_relacionada(app, cenario, monkeypatch):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        capturado = {}

        def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
            capturado["system"] = system
            return "SITUAÇÃO ATUAL\nProcesso em andamento, ver Lei nº 8.078/1990."

        import app.utils.analise_processo_ia as ia_mod
        monkeypatch.setattr(ia_mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

        resultado, _ = gerar_analise(processo, "resumo", legislacao_relacionada=[
            {"titulo": "Lei nº 8.078/1990", "data": "1990-09-11", "ementa": "CDC.",
             "link": "https://www.lexml.gov.br/urn/urn:lex:br:federal:lei:1990-09-11;8078"},
        ])
        assert "Lei nº 8.078/1990" in capturado["system"]
        # a citação bate com a legislação pesquisada — não deveria ser sinalizada
        assert "NÃO aparece" not in resultado and "não bater" not in resultado.lower() \
            or "confira se essa citação legal" not in resultado


# ---------- rota HTTP (busca síncrona + enfileiramento, sem Redis/LexML de verdade) ----------

def _fake_fila(monkeypatch):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append(args)
        return None

    import app.routes.processos as rotas_mod
    monkeypatch.setattr(rotas_mod, "enfileirar", _fake_enfileirar)
    monkeypatch.setattr(rotas_mod.agente_ia_router, "provedor_disponivel", lambda empresa: True)
    return chamadas


def test_rota_busca_legislacao_antes_de_enfileirar(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    resultado_fake = [{"titulo": "Lei nº 8.078/1990", "data": "1990-09-11", "ementa": "CDC.",
                        "link": "https://www.lexml.gov.br/urn/x"}]

    import app.routes.processos as rotas_mod
    monkeypatch.setattr(rotas_mod, "buscar_legislacao", lambda termo, **kw: resultado_fake)

    login("legislacao@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
        "buscar_legislacao": "1", "termo_legislacao": "vício do produto",
        "materia_fato": "Fato controvertido.", "materia_direito": "Direito controvertido.",
        "tese_a_sustentar": "Tese.", "resultado_pretendido": "Resultado.",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None
    assert len(chamadas) == 1
    # args = (analise.id, processo.id, tipo, instrucao, texto_referencia, tipo_peca,
    #         delimitacao_id, modelo_peca_id, legislacao_relacionada)
    legislacao_enviada = chamadas[0][-1]
    assert legislacao_enviada == resultado_fake


def test_rota_sem_checkbox_nao_busca_legislacao(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)

    import app.routes.processos as rotas_mod
    chamou = {"sim": False}

    def _nao_deveria_chamar(termo, **kw):
        chamou["sim"] = True
        return []
    monkeypatch.setattr(rotas_mod, "buscar_legislacao", _nao_deveria_chamar)

    login("legislacao@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
        "materia_fato": "Fato controvertido.", "materia_direito": "Direito controvertido.",
        "tese_a_sustentar": "Tese.", "resultado_pretendido": "Resultado.",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert chamou["sim"] is False
    assert len(chamadas) == 1
    assert chamadas[0][-1] == []


def test_rota_falha_do_lexml_nao_bloqueia_geracao(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)

    import app.routes.processos as rotas_mod

    def _falha(termo, **kw):
        raise LexmlIndisponivelError("timeout simulado")
    monkeypatch.setattr(rotas_mod, "buscar_legislacao", _falha)

    login("legislacao@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
        "buscar_legislacao": "1", "termo_legislacao": "vício do produto",
        "materia_fato": "Fato controvertido.", "materia_direito": "Direito controvertido.",
        "tese_a_sustentar": "Tese.", "resultado_pretendido": "Resultado.",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None, "a análise tem que ser criada mesmo com o LexML fora do ar"
    assert len(chamadas) == 1
    assert chamadas[0][-1] == [], "geração segue sem o bloco de legislação, nunca bloqueada"
    texto = r.data.decode("utf-8").lower()
    assert "lexml" in texto or "timeout simulado" in texto
