"""
Ordem invertida: e-SAJ/PJe/PJe-JT ANTES do DataJud (PENDENCIAS.md, seção
-95). Motivo do usuário: esses conectores leem o sistema do próprio
tribunal em tempo real, então acham processo recém-distribuído que o
DataJud ainda não indexou (defasagem variável, às vezes de dias) — e
quando o DataJud está lento/indisponível (timeout da API pública do CNJ),
tentar essas fontes primeiro evita que a busca inteira fique bloqueada
esperando o DataJud responder antes de sequer tentar uma fonte que já
teria achado o processo.

Dois aspectos verificados em cada ponto de entrada:
1. Prioridade de dado: quando DataJud e e-SAJ/PJe/PJe-JT concordam em
   achar o processo mas com valores DIFERENTES pro mesmo campo, o valor
   de quem rodou primeiro (e-SAJ/PJe/PJe-JT) é o que fica —
   `aplicar_carga_inicial` só preenche campo vazio.
2. Ordem de chamada: quando e-SAJ/PJe/PJe-JT já encontram o processo, a
   pré-visualização (consultar_cnj_preview) nem chega a chamar o DataJud
   — evita esperar um DataJud lento/travado à toa.
"""
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import Cliente, Processo
from app.utils import conector_esaj_publico as mod_esaj
from app.utils import conector_pje_jt_publico as mod_pje_jt
import app.routes.governanca as governanca_mod
import app.routes.processos as processos_mod


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "adv-ordem@teste.com", papel="advogado", nome="Advogado Ordem")
    cliente = Cliente(nome="Cliente Ordem de Busca", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.commit()
    return {"adv_id": adv_id, "adv_email": "adv-ordem@teste.com", "cliente_id": cliente.id,
            "unidade_id": unidade_id}


# ---------- consultar_cnj_preview: DataJud nem é chamado quando achou antes ----------

def test_preview_nao_chama_datajud_quando_esaj_ja_achou(client, login, cenario):
    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "Procedimento Comum Cível", "assunto": "Indenização",
        "orgao_julgador": "1ª Vara Cível", "comarca": "São Paulo", "instancia": "1º grau",
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    with patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj), \
         patch.object(governanca_mod, "obter_conector") as m_datajud:
        r = client.get("/governanca/processos/consultar-cnj?numero_cnj=1234567-89.2023.8.26.0100")

    m_datajud.assert_not_called()
    dados = r.get_json()
    assert dados["encontrado"] is True
    assert dados["fonte"] == "e-SAJ público"


def test_preview_nao_chama_datajud_quando_pje_jt_ja_achou(client, login, cenario):
    login(cenario["adv_email"])
    dados = mod_pje_jt._parse_processo(
        {
            "classe": "RECLAMAÇÃO TRABALHISTA", "numero": "0001234-56.2023.5.02.0001",
            "orgaoJulgador": "3ª Vara do Trabalho", "orgaoJulgadorColegiado": None,
            "poloAtivo": [], "poloPassivo": [], "movimentos": [],
        },
        "00012345620235020001", "trt2", "1º grau",
    )
    with patch.object(mod_pje_jt.ConectorPjeJtPublico, "consultar_processo", return_value=dados), \
         patch.object(governanca_mod, "obter_conector") as m_datajud:
        r = client.get("/governanca/processos/consultar-cnj?numero_cnj=0001234-56.2023.5.02.0001")

    m_datajud.assert_not_called()
    resposta = r.get_json()
    assert resposta["encontrado"] is True
    assert resposta["fonte"] == "PJe-JT público"


# ---------- Prioridade de dado quando as duas fontes concordam em achar ----------

def test_buscar_processo_prioriza_esaj_sobre_datajud_no_mesmo_campo(client, login, post_csrf, cenario):
    processo = Processo(
        numero_processo="1234567-89.2023.8.26.0100", cliente_id=cenario["cliente_id"],
        unidade_id=cenario["unidade_id"], area_direito="Cível", status="ativo", monitoravel=False,
    )
    db.session.add(processo)
    db.session.commit()
    processo_id = processo.id

    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "CLASSE DO E-SAJ (tempo real)", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    dados_datajud = {
        "tribunal_slug": "tjsp", "classe": "CLASSE DO DATAJUD (defasado)", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    with patch.object(governanca_mod, "obter_conector") as m_obter, \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj):
        m_obter.return_value.consultar_processo.return_value = dados_datajud
        r = post_csrf(f"/governanca/processos/{processo_id}/buscar-processo", {},
                      get_url=f"/processos/{processo_id}")

    assert r.status_code == 200
    processo = db.session.get(Processo, processo_id)
    # e-SAJ rodou primeiro -> preencheu "classe_processual" primeiro -> DataJud
    # (aplicar_carga_inicial só preenche campo vazio) não conseguiu sobrescrever.
    assert processo.classe_processual == "CLASSE DO E-SAJ (tempo real)"
    # Mas o DataJud ainda decide monitoravel/forma_acompanhamento -- achou o
    # processo, então o processo ainda entra em monitoramento automático.
    assert processo.monitoravel is True
    assert processo.forma_acompanhamento == "automatico"


def test_novo_processo_prioriza_esaj_sobre_datajud_no_mesmo_campo(client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "CLASSE DO E-SAJ (tempo real)", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    dados_datajud = {
        "tribunal_slug": "tjsp", "classe": "CLASSE DO DATAJUD (defasado)", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    with patch.object(processos_mod, "obter_conector") as m_obter, \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj):
        m_obter.return_value.consultar_processo.return_value = dados_datajud
        r = post_csrf("/processos/novo", {
            "numero_processo": "1234567-89.2023.8.26.0100", "numero_interno": "", "cliente_id": str(cenario["cliente_id"]),
            "area_direito": "Cível", "tipo_acao": "", "fase": "", "instancia": "", "comarca": "", "vara": "",
            "tribunal_datajud": "", "polo_cliente": "Autor", "parte_contraria": "", "advogado_contrario": "",
            "valor_causa": "", "data_distribuicao": "", "responsavel_id": "", "descricao": "",
        }, get_url="/processos/novo")

    assert r.status_code == 200
    processo = Processo.query.filter_by(numero_processo="1234567-89.2023.8.26.0100").first()
    assert processo is not None
    assert processo.classe_processual == "CLASSE DO E-SAJ (tempo real)"
    assert processo.monitoravel is True
    assert processo.forma_acompanhamento == "automatico"


def test_novo_por_cnj_prioriza_esaj_sobre_datajud_no_mesmo_campo(client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "CLASSE DO E-SAJ (tempo real)", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    dados_datajud = {
        "tribunal_slug": "tjsp", "classe": "CLASSE DO DATAJUD (defasado)", "assunto": None,
        "orgao_julgador": None, "comarca": None, "instancia": None,
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0, "partes": [], "movimentacoes": [],
    }
    with patch.object(governanca_mod, "obter_conector") as m_obter, \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj):
        m_obter.return_value.consultar_processo.return_value = dados_datajud
        r = post_csrf("/governanca/processos/novo-por-cnj", {
            "numero_cnj": "1234567-89.2023.8.26.0100", "cliente_id": str(cenario["cliente_id"]),
            "area_direito": "Cível", "tribunal_datajud": "",
        }, get_url="/governanca/processos/novo-por-cnj")

    assert r.status_code == 200
    processo = Processo.query.filter_by(numero_processo="1234567-89.2023.8.26.0100").first()
    assert processo is not None
    assert processo.classe_processual == "CLASSE DO E-SAJ (tempo real)"
    assert processo.monitoravel is True
    assert processo.forma_acompanhamento == "automatico"
