"""
PDF de resumo do processo + lista de partes capturada (PENDENCIAS.md,
seção -77) — app/utils/pdf_processo.py e a formatação de partes em
app/utils/captura_pipeline.py.
"""
from datetime import date, datetime

import pytest

from app.extensions import db
from app.models import Audiencia, Cliente, Movimentacao, Prazo, Processo
from app.utils.captura_pipeline import aplicar_carga_inicial, formatar_partes_texto
from app.utils.pdf_processo import gerar_pdf_processo


@pytest.fixture()
def processo(app, empresa_basica):
    unidade_id = empresa_basica["unidade_id"]
    cliente = Cliente(nome="Cliente Teste", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    proc = Processo(numero_processo="1234567-89.2023.8.26.0100", cliente_id=cliente.id,
                     unidade_id=unidade_id, area_direito="Cível", status="ativo")
    db.session.add(proc)
    db.session.commit()
    return proc


# ---------- formatar_partes_texto ----------

def test_formatar_partes_texto_sem_partes_devolve_none():
    assert formatar_partes_texto(None) is None
    assert formatar_partes_texto([]) is None


def test_formatar_partes_texto_uma_por_linha_com_advogados():
    partes = [
        {"tipo": "Reqte", "nome": "João da Silva", "advogados": ["Maria Advogada"]},
        {"tipo": "Reqdo", "nome": "Empresa XYZ Ltda", "advogados": []},
    ]
    texto = formatar_partes_texto(partes)
    linhas = texto.split("\n")
    assert linhas[0] == "Reqte: João da Silva (Advogado(s): Maria Advogada)"
    assert linhas[1] == "Reqdo: Empresa XYZ Ltda"


def test_aplicar_carga_inicial_preenche_partes_texto(app, processo):
    dados = {"partes": [{"tipo": "Reqte", "nome": "Fulano", "advogados": ["Dr. Ciclano"]}]}
    aplicar_carga_inicial(processo, dados, fonte_rotulo="e-SAJ")
    assert processo.partes_texto == "Reqte: Fulano (Advogado(s): Dr. Ciclano)"


def test_aplicar_carga_inicial_sobrescreve_partes_texto_a_cada_captura(app, processo):
    """Diferente dos outros campos de `aplicar_carga_inicial` (que só
    preenchem quando vazios), partes_texto é sempre atualizado — não é um
    campo editável à parte, é sempre um retrato da última captura."""
    processo.partes_texto = "Reqte: Nome Antigo"
    aplicar_carga_inicial(processo, {"partes": [{"tipo": "Reqte", "nome": "Nome Novo", "advogados": []}]})
    assert processo.partes_texto == "Reqte: Nome Novo"


def test_aplicar_carga_inicial_sem_partes_nao_mexe_no_campo(app, processo):
    processo.partes_texto = "Reqte: Alguém"
    aplicar_carga_inicial(processo, {"classe": "Procedimento Comum Cível"})
    assert processo.partes_texto == "Reqte: Alguém"


# ---------- gerar_pdf_processo ----------

def test_gerar_pdf_processo_vazio_produz_pdf_valido(app, processo):
    buffer = gerar_pdf_processo(processo, empresa=None, unidade=None, upload_folder="/tmp")
    conteudo = buffer.read()
    assert conteudo.startswith(b"%PDF")
    assert len(conteudo) > 500


def test_gerar_pdf_processo_completo_produz_pdf_valido(app, processo):
    processo.partes_texto = "Reqte: Fulano (Advogado(s): Dr. Ciclano)\nReqdo: Empresa XYZ"
    db.session.add(Movimentacao(processo_id=processo.id, data=datetime(2026, 1, 10),
                                 texto_integral="Distribuído por sorteio", origem_captura="esaj_publico",
                                 hash_dedup="h1"))
    db.session.add(Prazo(processo_id=processo.id, descricao="Contestar", status="pendente",
                          data_vencimento=date(2026, 2, 1)))
    db.session.add(Audiencia(processo_id=processo.id, data_hora=datetime(2026, 3, 1, 14, 0),
                              status="agendada", tipo="conciliação", deteccao_automatica=True,
                              observacoes="Detectado automaticamente."))
    db.session.commit()

    buffer = gerar_pdf_processo(processo, empresa=None, unidade=None, upload_folder="/tmp")
    conteudo = buffer.read()
    assert conteudo.startswith(b"%PDF")
    assert len(conteudo) > 500


# ---------- rota ----------

@pytest.fixture()
def usuario_pdf(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    return criar_usuario(unidade_id, "pdf@teste.com", papel="admin", nome="Admin PDF")


def test_rota_pdf_do_processo(client, login, processo, usuario_pdf):
    login("pdf@teste.com")
    r = client.get(f"/processos/{processo.id}/pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data.startswith(b"%PDF")
