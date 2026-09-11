"""
Botão único "Buscar processo" (PENDENCIAS.md, seção -93) — rota
`governanca.buscar_processo`, que decide sozinha entre:

1. Agente Local pareado (token do advogado) -> dispara os 3 conectores
   piloto (pje_mni, projudi, esaj_sp) em paralelo, cada um virando sua
   própria SolicitacaoBuscaAutos (assíncrono: quem responde de verdade é
   o agente na máquina do advogado, não esta requisição).
2. Sem agente pareado (ou `forcar_publico=1`): caminho público, sempre
   com DataJud primeiro, e depois e-SAJ/PJe (segmento 8) ou PJe-JT
   (segmento 5) -- automaticamente, ou só a fonte escolhida em `sistema`.
3. Segmento sem conector público nenhum: só o DataJud é tentado.

Não testa nenhuma chamada de rede de verdade -- tudo mockado, mesmo
espírito de test_conector_esaj_publico.py e test_agente_local.py.
"""
from unittest.mock import patch

import pytest

from app.extensions import db
from app.models import (
    AgenteLocalPareado, SolicitacaoBuscaAutos, Processo, Cliente, LogCaptura, Movimentacao,
)
from app.utils import conector_esaj_publico as mod_esaj
from app.utils import conector_pje_publico as mod_pje
from app.utils import conector_pje_jt_publico as mod_pje_jt
from app.utils import tribunais_conectores
import app.routes.governanca as governanca_mod
from app.utils.captura_conectores import ConectorNaoConfiguradoError


def _url(processo_id):
    return f"/governanca/processos/{processo_id}/buscar-processo"


@pytest.fixture()
def cenario_esaj(app, empresa_basica, criar_usuario):
    """Número de segmento 8 (estadual, TJSP) -- candidato a e-SAJ/PJe."""
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "adv-unificado@teste.com", papel="advogado", nome="Advogado Unificado")
    cliente = Cliente(nome="Cliente Busca Unificada", unidade_id=unidade_id)
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
    return {"processo_id": processo.id, "adv_id": adv_id, "adv_email": "adv-unificado@teste.com"}


@pytest.fixture()
def cenario_pje_jt(app, empresa_basica, criar_usuario):
    """Número de segmento 5 (trabalhista) -- candidato a PJe-JT."""
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "adv-unificado-jt@teste.com", papel="advogado", nome="Advogado Unificado JT")
    cliente = Cliente(nome="Cliente Busca Unificada JT", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo="0001234-56.2023.5.01.0001", cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Trabalhista", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "adv_id": adv_id, "adv_email": "adv-unificado-jt@teste.com"}


@pytest.fixture()
def cenario_federal(app, empresa_basica, criar_usuario):
    """Número de segmento 4 (federal) -- sem conector público além do DataJud."""
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "adv-unificado-fed@teste.com", papel="advogado", nome="Advogado Unificado Federal")
    cliente = Cliente(nome="Cliente Busca Unificada Federal", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo="0001234-56.2023.4.04.0000", cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Cível", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "adv_id": adv_id, "adv_email": "adv-unificado-fed@teste.com"}


def _sem_datajud_configurado(nome_fonte, empresa=None):
    raise ConectorNaoConfiguradoError("DATAJUD_API_KEY não configurada (ambiente de teste).")


# ---------- Caminho 1: Agente Local pareado ----------

def test_com_agente_pareado_cria_solicitacao_pra_cada_conector_piloto(client, login, post_csrf, cenario_esaj, app):
    login(cenario_esaj["adv_email"])
    with app.app_context():
        from app.models import Usuario
        usuario = db.session.get(Usuario, cenario_esaj["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    with patch.object(governanca_mod, "obter_conector") as m_datajud:
        r = post_csrf(_url(cenario_esaj["processo_id"]), {}, get_url=f"/processos/{cenario_esaj['processo_id']}")
    assert r.status_code == 200
    m_datajud.assert_not_called()  # caminho do agente nem chega a tentar o público

    pedidos = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario_esaj["processo_id"]).all()
    assert {p.tribunal_conector for p in pedidos} == set(tribunais_conectores.CONECTORES_IMPLEMENTADOS)
    assert all(p.status == "pendente" for p in pedidos)
    assert all(p.solicitado_por_id == cenario_esaj["adv_id"] for p in pedidos)


def test_com_agente_pareado_nao_duplica_solicitacao_ja_aberta(client, login, post_csrf, cenario_esaj, app):
    login(cenario_esaj["adv_email"])
    with app.app_context():
        from app.models import Usuario
        usuario = db.session.get(Usuario, cenario_esaj["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    post_csrf(_url(cenario_esaj["processo_id"]), {}, get_url=f"/processos/{cenario_esaj['processo_id']}")
    post_csrf(_url(cenario_esaj["processo_id"]), {}, get_url=f"/processos/{cenario_esaj['processo_id']}")

    pedidos = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario_esaj["processo_id"]).all()
    assert len(pedidos) == len(tribunais_conectores.CONECTORES_IMPLEMENTADOS)


def test_com_agente_pareado_forcar_publico_pula_direto_pro_publico(client, login, post_csrf, cenario_esaj, app):
    login(cenario_esaj["adv_email"])
    with app.app_context():
        from app.models import Usuario
        usuario = db.session.get(Usuario, cenario_esaj["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    with patch.object(governanca_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo") as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo") as m_pje:
        m_esaj.side_effect = mod_esaj.ErroEsajPublico("não encontrado (mock)")
        m_pje.side_effect = mod_pje.ErroPjePublico("não encontrado (mock)")
        r = post_csrf(_url(cenario_esaj["processo_id"]), {"forcar_publico": "1"},
                      get_url=f"/processos/{cenario_esaj['processo_id']}")
    assert r.status_code == 200
    m_esaj.assert_called_once()
    m_pje.assert_called_once()

    # nenhuma solicitação de agente foi criada -- pulou direto pro público
    assert SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario_esaj["processo_id"]).count() == 0


# ---------- Caminho 2: sem agente, público automático ----------

def test_sem_agente_segmento_8_tenta_datajud_e_esaj_e_pje(client, login, post_csrf, cenario_esaj):
    login(cenario_esaj["adv_email"])

    dados_esaj = {
        "tribunal_slug": "tjsp", "classe": "Procedimento Comum Cível", "assunto": "Indenização",
        "orgao_julgador": "1ª Vara Cível", "comarca": "São Paulo", "instancia": "1º grau",
        "data_ajuizamento": None, "valor_causa": None, "nivel_sigilo": 0,
        "partes": [], "movimentacoes": [],
    }
    dados_pje = dict(dados_esaj, tribunal_slug="tjrj")

    with patch.object(governanca_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo", return_value=dados_esaj) as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo", return_value=dados_pje) as m_pje:
        r = post_csrf(_url(cenario_esaj["processo_id"]), {}, get_url=f"/processos/{cenario_esaj['processo_id']}")

    assert r.status_code == 200
    m_esaj.assert_called_once()
    m_pje.assert_called_once()

    logs = {l.fonte for l in LogCaptura.query.filter_by(processo_id=cenario_esaj["processo_id"]).all()}
    assert "esaj_publico" in logs
    assert "pje_publico" in logs
    # "não configurado" (sem DATAJUD_API_KEY) não gera LogCaptura -- mesma
    # convenção da rota tentar_captura já existente (só falha de rede, via
    # ConexaoDataJudError, é que vira log; falta de configuração só flash).


def test_sem_agente_sistema_escolhido_restringe_a_uma_fonte(client, login, post_csrf, cenario_esaj):
    login(cenario_esaj["adv_email"])

    with patch.object(governanca_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo") as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo") as m_pje:
        m_pje.side_effect = mod_pje.ErroPjePublico("não encontrado (mock)")
        r = post_csrf(_url(cenario_esaj["processo_id"]), {"sistema": "pje"},
                      get_url=f"/processos/{cenario_esaj['processo_id']}")

    assert r.status_code == 200
    m_esaj.assert_not_called()
    m_pje.assert_called_once()


def test_sem_agente_segmento_5_tenta_pje_jt(client, login, post_csrf, cenario_pje_jt):
    login(cenario_pje_jt["adv_email"])
    dados = mod_pje_jt._parse_processo(
        {
            "classe": "RECLAMAÇÃO TRABALHISTA", "numero": "0001234-56.2023.5.01.0001",
            "orgaoJulgador": "1ª Vara do Trabalho", "orgaoJulgadorColegiado": None,
            "poloAtivo": [], "poloPassivo": [], "movimentos": [],
        },
        "12345678920235010001", "trt1", "1º grau",
    )
    with patch.object(governanca_mod, "obter_conector", side_effect=_sem_datajud_configurado), \
         patch.object(mod_pje_jt.ConectorPjeJtPublico, "consultar_processo", return_value=dados) as m_pje_jt:
        r = post_csrf(_url(cenario_pje_jt["processo_id"]), {}, get_url=f"/processos/{cenario_pje_jt['processo_id']}")

    assert r.status_code == 200
    m_pje_jt.assert_called_once()
    processo = db.session.get(Processo, cenario_pje_jt["processo_id"])
    assert processo.classe_processual == "RECLAMAÇÃO TRABALHISTA"


def test_sem_agente_segmento_sem_conector_publico_so_tenta_datajud(client, login, post_csrf, cenario_federal):
    login(cenario_federal["adv_email"])
    with patch.object(governanca_mod, "obter_conector", side_effect=_sem_datajud_configurado) as m_datajud, \
         patch.object(mod_esaj.ConectorEsajPublico, "consultar_processo") as m_esaj, \
         patch.object(mod_pje.ConectorPjePublico, "consultar_processo") as m_pje, \
         patch.object(mod_pje_jt.ConectorPjeJtPublico, "consultar_processo") as m_pje_jt:
        r = post_csrf(_url(cenario_federal["processo_id"]), {}, get_url=f"/processos/{cenario_federal['processo_id']}")

    assert r.status_code == 200
    m_datajud.assert_called_once()
    m_esaj.assert_not_called()
    m_pje.assert_not_called()
    m_pje_jt.assert_not_called()


def test_tela_do_processo_destaca_fallback_quando_todas_tentativas_do_agente_falharam(
    client, login, post_csrf, cenario_esaj, app,
):
    """Cobre a renderização do bloco novo em detalhe.html: quando todas as
    SolicitacaoBuscaAutos do processo já terminaram em "erro" (nenhuma
    pendente/em_andamento/concluída), a tela deve continuar renderizando
    sem erro e mostrar o botão de fallback pro caminho público."""
    login(cenario_esaj["adv_email"])
    with app.app_context():
        from app.models import Usuario
        usuario = db.session.get(Usuario, cenario_esaj["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    post_csrf(_url(cenario_esaj["processo_id"]), {}, get_url=f"/processos/{cenario_esaj['processo_id']}")
    pedidos = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario_esaj["processo_id"]).all()
    assert len(pedidos) == len(tribunais_conectores.CONECTORES_IMPLEMENTADOS)
    for p in pedidos:
        p.falhar("erro simulado no teste")
    db.session.commit()

    r = client.get(f"/processos/{cenario_esaj['processo_id']}")
    assert r.status_code == 200
    assert "Buscar pelos sistemas públicos".encode() in r.data


def test_sem_numero_processo_nao_tenta_nada(client, login, post_csrf, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    criar_usuario(unidade_id, "adv-sem-numero@teste.com", papel="advogado", nome="Advogado Sem Número")
    cliente = Cliente(nome="Cliente Sem Número", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_processo=None, cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", status="ativo", monitoravel=False)
    db.session.add(processo)
    db.session.commit()

    login("adv-sem-numero@teste.com")
    r = post_csrf(_url(processo.id), {}, get_url=f"/processos/{processo.id}")
    assert r.status_code == 200
    assert SolicitacaoBuscaAutos.query.filter_by(processo_id=processo.id).count() == 0
