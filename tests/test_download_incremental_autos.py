"""
Item 2 da lista de pipeline de IA jurídica (PENDENCIAS.md, seção -103):
"Download dos autos" — "baixa só o que ainda não está indexado". Antes
desta funcionalidade, pedir busca de autos completos pelo Agente Local
(app/routes/governanca.py::buscar_processo) não tinha NENHUMA noção de
"já baixei isso antes" — cada clique disparava um novo pedido às cegas.

`info_ultima_busca_autos` (app/utils/indexacao_documentos.py) é a função
que dá essa consciência; é usada de forma NÃO BLOQUEANTE (só muda a
mensagem de flash — nunca impede a criação da SolicitacaoBuscaAutos), ver
docstring da função e o comentário em `buscar_processo` sobre esta
decisão de design.
"""
from datetime import datetime, timedelta

import pytest
from unittest.mock import patch

from app.extensions import db
from app.models import (
    Cliente, Processo, Documento, DocumentoIndexado, Movimentacao,
    AgenteLocalPareado, SolicitacaoBuscaAutos, Usuario,
)
from app.utils import tribunais_conectores
from app.utils.indexacao_documentos import info_ultima_busca_autos, indexar_documento
import app.routes.governanca as governanca_mod


def _url(processo_id):
    return f"/governanca/processos/{processo_id}/buscar-processo"


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "adv-incremental@teste.com", papel="advogado", nome="Advogado Incremental")
    cliente = Cliente(nome="Cliente Incremental", unidade_id=unidade_id)
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
    return dict(unidade_id=unidade_id, adv_id=adv_id, adv_email="adv-incremental@teste.com",
                processo_id=processo.id)


def _doc_autos_completo(processo_id, enviado_em, nome="autos_completos.txt"):
    import os
    from flask import current_app
    pasta = os.path.join(current_app.config["UPLOAD_FOLDER"], str(processo_id))
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, nome)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write("Conteúdo simulado dos autos completos baixados pelo Agente Local.")
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="autos_completo_agente_local", enviado_em=enviado_em)
    db.session.add(doc)
    db.session.commit()
    return doc


# ---------------------------------------------------------------------
# info_ultima_busca_autos — função pura
# ---------------------------------------------------------------------

def test_info_ultima_busca_devolve_none_quando_nunca_baixou(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    assert info_ultima_busca_autos(processo) is None


def test_info_ultima_busca_ignora_documento_de_outra_categoria(app, cenario):
    _doc_autos_completo(cenario["processo_id"], datetime.utcnow(), nome="x.pdf")
    # sobrescreve a categoria pra simular um documento comum (não é "autos completo")
    doc = Documento.query.filter_by(processo_id=cenario["processo_id"]).first()
    doc.categoria = "peticao"
    db.session.commit()

    processo = db.session.get(Processo, cenario["processo_id"])
    assert info_ultima_busca_autos(processo) is None


def test_info_ultima_busca_indexado_false_quando_ainda_nao_indexado(app, cenario):
    _doc_autos_completo(cenario["processo_id"], datetime.utcnow())
    processo = db.session.get(Processo, cenario["processo_id"])

    info = info_ultima_busca_autos(processo)
    assert info is not None
    assert info["indexado"] is False
    assert info["qtd_movimentacoes_novas"] == 0


def test_info_ultima_busca_indexado_true_apos_indexar(app, cenario):
    doc = _doc_autos_completo(cenario["processo_id"], datetime.utcnow())
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    processo = db.session.get(Processo, cenario["processo_id"])
    info = info_ultima_busca_autos(processo)
    assert info["indexado"] is True
    assert info["documento"].id == doc.id


def test_info_ultima_busca_conta_so_movimentacoes_capturadas_depois_do_download(app, cenario):
    referencia = datetime(2026, 6, 1, 12, 0, 0)
    doc = _doc_autos_completo(cenario["processo_id"], referencia)

    # movimentação capturada ANTES do download — não deve contar.
    mov_antiga = Movimentacao(processo_id=cenario["processo_id"], data=datetime(2026, 5, 1),
                               texto_integral="Movimentação antiga")
    db.session.add(mov_antiga)
    db.session.flush()
    mov_antiga.criado_em = referencia - timedelta(days=5)

    # movimentação capturada DEPOIS do download — deve contar.
    mov_nova = Movimentacao(processo_id=cenario["processo_id"], data=datetime(2026, 6, 10),
                             texto_integral="Movimentação nova")
    db.session.add(mov_nova)
    db.session.flush()
    mov_nova.criado_em = referencia + timedelta(days=2)

    # movimentação nova, mas DELETADA — não deve contar.
    mov_deletada = Movimentacao(processo_id=cenario["processo_id"], data=datetime(2026, 6, 11),
                                 texto_integral="Movimentação nova mas deletada")
    db.session.add(mov_deletada)
    db.session.flush()
    mov_deletada.criado_em = referencia + timedelta(days=3)
    mov_deletada.deletado_em = referencia + timedelta(days=4)

    db.session.commit()

    processo = db.session.get(Processo, cenario["processo_id"])
    info = info_ultima_busca_autos(processo)
    assert info["qtd_movimentacoes_novas"] == 1


# ---------------------------------------------------------------------
# Rota governanca.buscar_processo — mensagem informativa, nunca bloqueia
# ---------------------------------------------------------------------

def test_busca_com_agente_pareado_sem_download_anterior_usa_mensagem_padrao(app, client, login, post_csrf, cenario):
    login(cenario["adv_email"])
    with app.app_context():
        usuario = db.session.get(Usuario, cenario["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    with patch.object(governanca_mod, "obter_conector") as m_datajud:
        r = post_csrf(_url(cenario["processo_id"]), {}, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    m_datajud.assert_not_called()
    corpo = r.data.decode("utf-8")
    assert "talvez" not in corpo.lower() or "não precise esperar" not in corpo.lower()

    pedidos = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario["processo_id"]).all()
    assert {p.tribunal_conector for p in pedidos} == set(tribunais_conectores.CONECTORES_IMPLEMENTADOS)


def test_busca_com_download_anterior_indexado_e_sem_novidade_mostra_aviso_mas_ainda_cria_solicitacoes(
        app, client, login, post_csrf, cenario):
    doc = _doc_autos_completo(cenario["processo_id"], datetime.utcnow() - timedelta(days=1))
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    login(cenario["adv_email"])
    with app.app_context():
        usuario = db.session.get(Usuario, cenario["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    with patch.object(governanca_mod, "obter_conector"):
        r = post_csrf(_url(cenario["processo_id"]), {}, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "já tinham sido baixados e" in corpo
    assert "talvez" in corpo.lower()

    # nunca bloqueia — as solicitações são criadas do mesmo jeito, mesmo
    # com o aviso de "provavelmente redundante" (decisão deliberada, ver
    # docstring de info_ultima_busca_autos e o comentário em buscar_processo).
    pedidos = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario["processo_id"]).all()
    assert {p.tribunal_conector for p in pedidos} == set(tribunais_conectores.CONECTORES_IMPLEMENTADOS)
    assert all(p.status == "pendente" for p in pedidos)


def test_busca_com_download_anterior_mas_movimentacao_nova_usa_mensagem_padrao(
        app, client, login, post_csrf, cenario):
    referencia = datetime.utcnow() - timedelta(days=3)
    doc = _doc_autos_completo(cenario["processo_id"], referencia)
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    mov = Movimentacao(processo_id=cenario["processo_id"], data=datetime.utcnow(),
                        texto_integral="Movimentação nova capturada depois do último download")
    db.session.add(mov)
    db.session.commit()  # criado_em = agora, depois da referência

    login(cenario["adv_email"])
    with app.app_context():
        usuario = db.session.get(Usuario, cenario["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    with patch.object(governanca_mod, "obter_conector"):
        r = post_csrf(_url(cenario["processo_id"]), {}, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "já tinham sido baixados e" not in corpo


def test_busca_com_download_anterior_nao_indexado_usa_mensagem_padrao(app, client, login, post_csrf, cenario):
    # documento baixado, mas AINDA não indexado (job não rodou ainda) — não
    # basta ter baixado antes, precisa também já estar indexado pra valer o
    # aviso de "talvez não precise esperar".
    _doc_autos_completo(cenario["processo_id"], datetime.utcnow() - timedelta(days=1))

    login(cenario["adv_email"])
    with app.app_context():
        usuario = db.session.get(Usuario, cenario["adv_id"])
        AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
        db.session.commit()

    with patch.object(governanca_mod, "obter_conector"):
        r = post_csrf(_url(cenario["processo_id"]), {}, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "já tinham sido baixados e" not in corpo
