"""
Testa a auditoria de acesso a documentos (PENDENCIAS.md, seção -51):
todo download de documento passa a deixar rastro (quem, quando, IP),
o histórico de UM documento nunca mistura com o de outro (mesmo que
tenham o mesmo nome original), e só admin/gestor consegue ver esse
histórico — usuário comum baixa normalmente, mas não vê quem mais
baixou.
"""
import os

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Documento, LogAtividade


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]

    admin_id = criar_usuario(unidade_id, "admin@auditoria.com", papel="admin", nome="Admin")
    gestor_id = criar_usuario(unidade_id, "gestor@auditoria.com", papel="gestor", nome="Gestor")
    advogado_id = criar_usuario(unidade_id, "adv@auditoria.com", papel="advogado", nome="Advogado")

    cliente = Cliente(nome="Cliente Auditoria", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(numero_processo="0000070-11.2026.8.26.0100", cliente_id=cliente.id,
                         unidade_id=unidade_id, area_direito="Cível",
                         responsavel_id=advogado_id, criado_por_id=advogado_id)
    db.session.add(processo)
    db.session.flush()

    return dict(admin_id=admin_id, gestor_id=gestor_id, advogado_id=advogado_id,
                unidade_id=unidade_id, processo_id=processo.id)


def _criar_documento_no_disco(app, processo_id, nome_original="contrato.pdf", nome_arquivo=None):
    nome_arquivo = nome_arquivo or nome_original
    pasta = os.path.join(app.config["UPLOAD_FOLDER"], str(processo_id))
    os.makedirs(pasta, exist_ok=True)
    caminho = os.path.join(pasta, nome_arquivo)
    with open(caminho, "wb") as f:
        f.write(b"conteudo de teste")
    doc = Documento(processo_id=processo_id, nome_original=nome_original, nome_arquivo=nome_arquivo,
                     categoria="contrato", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def test_baixar_documento_registra_quem_baixou(app, client, login, cenario):
    doc = _criar_documento_no_disco(app, cenario["processo_id"])

    login("adv@auditoria.com")
    r = client.get(f"/processos/documentos/{doc.id}/baixar")
    assert r.status_code == 200
    assert r.data == b"conteudo de teste"

    logs = LogAtividade.query.filter_by(entidade="Documento", entidade_id=doc.id, acao="baixou_documento").all()
    assert len(logs) == 1
    assert logs[0].usuario_id == cenario["advogado_id"]
    assert logs[0].detalhes == "contrato.pdf"


def test_baixar_varias_vezes_registra_cada_download(app, client, login, cenario):
    doc = _criar_documento_no_disco(app, cenario["processo_id"])

    login("adv@auditoria.com")
    client.get(f"/processos/documentos/{doc.id}/baixar")
    client.get(f"/processos/documentos/{doc.id}/baixar")
    client.get(f"/processos/documentos/{doc.id}/baixar")

    assert LogAtividade.query.filter_by(entidade="Documento", entidade_id=doc.id,
                                         acao="baixou_documento").count() == 3


def test_historico_nao_mistura_documentos_com_mesmo_nome(app, client, login, cenario):
    # dois documentos DIFERENTES no mesmo processo com o MESMO nome
    # original — o histórico de cada um tem que continuar isolado,
    # porque a auditoria usa o id do Documento, nunca o nome.
    doc1 = _criar_documento_no_disco(app, cenario["processo_id"], nome_original="peticao.pdf", nome_arquivo="a.pdf")
    doc2 = _criar_documento_no_disco(app, cenario["processo_id"], nome_original="peticao.pdf", nome_arquivo="b.pdf")

    login("adv@auditoria.com")
    client.get(f"/processos/documentos/{doc1.id}/baixar")
    client.get(f"/processos/documentos/{doc1.id}/baixar")
    client.get(f"/processos/documentos/{doc2.id}/baixar")

    assert LogAtividade.query.filter_by(entidade="Documento", entidade_id=doc1.id,
                                         acao="baixou_documento").count() == 2
    assert LogAtividade.query.filter_by(entidade="Documento", entidade_id=doc2.id,
                                         acao="baixou_documento").count() == 1


def test_gestor_ve_historico_com_quem_baixou(app, client, login, cenario):
    doc = _criar_documento_no_disco(app, cenario["processo_id"])

    login("adv@auditoria.com")
    client.get(f"/processos/documentos/{doc.id}/baixar")
    client.get("/logout")

    login("gestor@auditoria.com")
    client.get(f"/processos/documentos/{doc.id}/baixar")
    client.get("/logout")

    login("admin@auditoria.com")
    r = client.get(f"/processos/documentos/{doc.id}/historico")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "Advogado" in corpo
    assert "Gestor" in corpo


def test_advogado_comum_nao_acessa_historico_de_documento(client, login, cenario, app):
    doc = _criar_documento_no_disco(app, cenario["processo_id"])

    login("adv@auditoria.com")
    r = client.get(f"/processos/documentos/{doc.id}/historico")
    assert r.status_code == 403


def test_historico_sem_downloads_fica_vazio_mas_acessivel(client, login, cenario, app):
    doc = _criar_documento_no_disco(app, cenario["processo_id"])

    login("admin@auditoria.com")
    r = client.get(f"/processos/documentos/{doc.id}/historico")
    assert r.status_code == 200
    assert "Nenhum download registrado" in r.data.decode("utf-8")


def test_tela_do_processo_mostra_link_de_historico_com_contagem(client, login, cenario, app):
    doc = _criar_documento_no_disco(app, cenario["processo_id"])

    login("adv@auditoria.com")
    client.get(f"/processos/documentos/{doc.id}/baixar")
    client.get(f"/processos/documentos/{doc.id}/baixar")
    client.get("/logout")

    login("admin@auditoria.com")
    r = client.get(f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "Histórico (2)" in corpo

    client.get("/logout")
    login("adv@auditoria.com")
    r2 = client.get(f"/processos/{cenario['processo_id']}")
    assert r2.status_code == 200
    assert f"/processos/documentos/{doc.id}/historico" not in r2.data.decode("utf-8"), \
        "usuário comum (não admin/gestor) não deveria ver o link de histórico de acesso ao documento"
