"""
Busca completa já no cadastro de processo novo (PENDENCIAS.md, seção -94):
"Novo processo" (processos.novo), "Editar processo" (processos.editar,
quando o número muda) e "Cadastrar por CNJ" (governanca.novo_por_cnj) agora
tentam DataJud e, na sequência, e-SAJ/PJe (segmento estadual) ou PJe-JT
(segmento trabalhista) — o mesmo trio que o botão "Buscar processo" já usa
pra processo existente (ver tests/test_busca_processo_unificada.py) — em
vez de só DataJud como antes.

Nenhuma chamada de rede de verdade -- tudo mockado.
"""
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Cliente, Processo, LogCaptura, Movimentacao
from app.utils import conector_esaj_publico as mod_esaj
from app.utils import conector_pje_publico as mod_pje
from app.utils import conector_pje_jt_publico as mod_pje_jt
from app.utils.captura_conectores import ConectorNaoConfiguradoError
import app.routes.processos as processos_mod
import app.routes.governanca as governanca_mod


def _sem_datajud_configurado(nome_fonte, empresa=None):
    raise ConectorNaoConfiguradoError("DATAJUD_API_KEY não configurada (ambiente de teste).")


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "adv-cadastro@teste.com", papel="advogado", nome="Advogado Cadastro")
    cliente = Cliente(nome="Cliente Cadastro Completo", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.commit()
    return {"adv_id": adv_id, "adv_email": "adv-cadastro@teste.com", "cliente_id": cliente.id,
            "unidade_id": unidade_id}


def _payload_novo(cenario, numero_processo):
    return {
        "numero_processo": numero_processo, "numero_interno": "", "cliente_id": str(cenario["cliente_id"]),
        "area_direito": "Cível", "tipo_acao": "", "fase": "", "instancia": "", "comarca": "", "vara": "",
        "tribunal_datajud": "", "polo_cliente": "Autor", "parte_contraria": "", "advogado_contrario": "",
        "valor_causa": "", "data_distribuicao": "", "responsavel_id": "", "descricao": "",
    }


def test_novo_processo_tenta_datajud_e_esaj_e_pje_juntos(client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "Procedimento Comum Cível", "assunto": "Indenização",
        "orgao_julgador": "1ª Vara Cível", "comarca": "São Paulo", "instancia": "1º grau",
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    dados_pje = dict(dados_esaj, tribunal_slug="tjrj")

    with patch.object(processos_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj) as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo", return_value=dados_pje) as m_pje:
        r = post_csrf("/processos/novo", _payload_novo(cenario, "1234567-89.2023.8.26.0100"),
                      get_url="/processos/novo")

    assert r.status_code == 200
    m_esaj.assert_called_once()
    m_pje.assert_called_once()

    processo = Processo.query.filter_by(numero_processo="1234567-89.2023.8.26.0100").first()
    assert processo is not None
    # DataJud não configurado -> não entra em monitoramento automático...
    assert processo.forma_acompanhamento == "nao_monitoravel"
    assert processo.monitoravel is False
    # ...mas os dados do e-SAJ/PJe foram capturados mesmo assim (enriquecimento).
    assert processo.classe_processual == "Procedimento Comum Cível"
    logs = {l.fonte for l in LogCaptura.query.filter_by(processo_id=processo.id).all()}
    assert "esaj_publico" in logs
    assert "pje_publico" in logs


def test_novo_processo_segmento_trabalhista_tenta_pje_jt(client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    dados = mod_pje_jt._parse_processo(
        {
            "classe": "RECLAMAÇÃO TRABALHISTA", "numero": "0009999-11.2023.5.02.0001",
            "orgaoJulgador": "2ª Vara do Trabalho", "orgaoJulgadorColegiado": None,
            "poloAtivo": [], "poloPassivo": [], "movimentos": [],
        },
        "00099991120235020001", "trt2", "1º grau",
    )
    with patch.object(processos_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_pje_jt.ConectorPjeJtPublico, "consultar_processo", return_value=dados) as m_pje_jt:
        r = post_csrf("/processos/novo", _payload_novo(cenario, "0009999-11.2023.5.02.0001"),
                      get_url="/processos/novo")

    assert r.status_code == 200
    m_pje_jt.assert_called_once()
    processo = Processo.query.filter_by(numero_processo="0009999-11.2023.5.02.0001").first()
    assert processo.classe_processual == "RECLAMAÇÃO TRABALHISTA"


def test_novo_processo_sem_numero_nao_tenta_fontes_publicas(client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    with patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo") as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo") as m_pje, \
         patch.object(mod_pje_jt.ConectorPjeJtPublico, "consultar_processo") as m_pje_jt:
        r = post_csrf("/processos/novo", _payload_novo(cenario, ""), get_url="/processos/novo")

    assert r.status_code == 200
    m_esaj.assert_not_called()
    m_pje.assert_not_called()
    m_pje_jt.assert_not_called()


def test_editar_processo_com_numero_novo_tenta_fontes_publicas(client, login, post_csrf, cenario, app):
    processo = Processo(area_direito="Cível", unidade_id=cenario["unidade_id"], cliente_id=cenario["cliente_id"])
    db.session.add(processo)
    db.session.commit()
    processo_id = processo.id

    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "Execução Fiscal", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    with patch.object(processos_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj) as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo") as m_pje:
        m_pje.side_effect = mod_pje.ErroPjePublico("não encontrado (mock)")
        r = post_csrf(f"/processos/{processo_id}/editar",
                      {
                          "numero_processo": "1234567-89.2023.8.26.0100", "numero_interno": "", "area_direito": "Cível",
                          "cliente_id": str(cenario["cliente_id"]),
                          "tipo_acao": "", "fase": "", "instancia": "", "comarca": "", "vara": "",
                          "tribunal_datajud": "", "polo_cliente": "", "parte_contraria": "", "advogado_contrario": "",
                          "valor_causa": "", "data_distribuicao": "", "responsavel_id": "", "descricao": "",
                          "status": "ativo",
                      },
                      get_url=f"/processos/{processo_id}/editar")

    assert r.status_code == 200
    m_esaj.assert_called_once()
    m_pje.assert_called_once()
    processo = db.session.get(Processo, processo_id)
    assert processo.classe_processual == "Execução Fiscal"


def test_novo_por_cnj_tenta_esaj_alem_do_datajud(client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "Monitória", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    with patch.object(governanca_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj) as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo") as m_pje:
        m_pje.side_effect = mod_pje.ErroPjePublico("não encontrado (mock)")
        r = post_csrf("/governanca/processos/novo-por-cnj", {
            "numero_cnj": "1234567-89.2023.8.26.0100", "cliente_id": str(cenario["cliente_id"]),
            "area_direito": "Cível", "tribunal_datajud": "",
        }, get_url="/governanca/processos/novo-por-cnj")

    assert r.status_code == 200
    m_esaj.assert_called_once()
    processo = Processo.query.filter_by(numero_processo="1234567-89.2023.8.26.0100").first()
    assert processo is not None
    assert processo.classe_processual == "Monitória"
    assert processo.forma_acompanhamento == "nao_monitoravel"  # DataJud não achou; e-SAJ só enriquece
