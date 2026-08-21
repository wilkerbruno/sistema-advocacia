"""
Testa a exportação em massa de dados de um cliente (PENDENCIAS.md, seção
-52) — complemento da exportação LGPD (tests/test_lgpd.py, que só cobre
os metadados estruturados): o pacote .zip inclui o mesmo JSON MAIS os
arquivos de documento de verdade anexados aos processos do cliente, um
arquivo ausente no disco não derruba a exportação inteira, e cada
documento incluído também aparece no histórico de auditoria de acesso
(seção -51) como se tivesse sido baixado.
"""
import io
import os
import zipfile

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Documento, LogAtividade


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "pacote@teste.com", papel="advogado", nome="Advogado Pacote")

    cliente = Cliente(nome="Cliente Pacote", unidade_id=unidade_id, criado_por_id=usuario_id)
    db.session.add(cliente)
    db.session.flush()

    processo1 = Processo(numero_interno="P-PACOTE-1", cliente_id=cliente.id, unidade_id=unidade_id,
                          area_direito="Cível", responsavel_id=usuario_id, criado_por_id=usuario_id)
    processo2 = Processo(numero_interno="P-PACOTE-2", cliente_id=cliente.id, unidade_id=unidade_id,
                          area_direito="Trabalhista", responsavel_id=usuario_id, criado_por_id=usuario_id)
    db.session.add_all([processo1, processo2])
    db.session.flush()
    db.session.commit()

    return dict(usuario_id=usuario_id, unidade_id=unidade_id, cliente_id=cliente.id,
                processo1_id=processo1.id, processo2_id=processo2.id)


def _criar_documento_no_disco(app, processo_id, nome_original, nome_arquivo=None, conteudo=b"conteudo"):
    nome_arquivo = nome_arquivo or nome_original
    pasta = os.path.join(app.config["UPLOAD_FOLDER"], str(processo_id))
    os.makedirs(pasta, exist_ok=True)
    with open(os.path.join(pasta, nome_arquivo), "wb") as f:
        f.write(conteudo)
    doc = Documento(processo_id=processo_id, nome_original=nome_original, nome_arquivo=nome_arquivo,
                     categoria="contrato", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def test_pacote_inclui_json_e_documentos_dos_dois_processos(app, client, login, cenario):
    _criar_documento_no_disco(app, cenario["processo1_id"], "contrato.pdf", conteudo=b"contrato aqui")
    _criar_documento_no_disco(app, cenario["processo2_id"], "procuracao.pdf", conteudo=b"procuracao aqui")

    login("pacote@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/exportar-pacote-completo")
    assert r.status_code == 200
    assert r.mimetype == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(r.data))
    nomes = zf.namelist()
    assert "dados_lgpd.json" in nomes
    assert any(n.endswith("contrato.pdf") for n in nomes)
    assert any(n.endswith("procuracao.pdf") for n in nomes)
    assert zf.read([n for n in nomes if n.endswith("contrato.pdf")][0]) == b"contrato aqui"


def test_documentos_com_mesmo_nome_nao_se_sobrescrevem_no_zip(app, client, login, cenario):
    _criar_documento_no_disco(app, cenario["processo1_id"], "peticao.pdf", nome_arquivo="a.pdf", conteudo=b"primeira")
    _criar_documento_no_disco(app, cenario["processo1_id"], "peticao.pdf", nome_arquivo="b.pdf", conteudo=b"segunda")

    login("pacote@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/exportar-pacote-completo")
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    nomes_peticao = [n for n in zf.namelist() if "peticao" in n]
    assert len(nomes_peticao) == 2, "dois documentos com o mesmo nome original não podem virar um só arquivo no zip"
    conteudos = {zf.read(n) for n in nomes_peticao}
    assert conteudos == {b"primeira", b"segunda"}


def test_arquivo_ausente_no_disco_nao_derruba_exportacao_e_gera_aviso(app, client, login, cenario):
    doc_ok = _criar_documento_no_disco(app, cenario["processo1_id"], "existe.pdf")
    # documento cadastrado no banco, mas SEM arquivo real no disco
    doc_fantasma = Documento(processo_id=cenario["processo1_id"], nome_original="fantasma.pdf",
                              nome_arquivo="nao-existe-no-disco.pdf", categoria="outros", tamanho_kb=1)
    db.session.add(doc_fantasma)
    db.session.commit()

    login("pacote@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/exportar-pacote-completo")
    assert r.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(r.data))
    nomes = zf.namelist()
    assert any(n.endswith("existe.pdf") for n in nomes)
    assert not any("fantasma" in n for n in nomes)
    assert "AVISOS.txt" in nomes
    assert "fantasma.pdf" in zf.read("AVISOS.txt").decode("utf-8")


def test_exportacao_registra_log_e_conta_como_download_de_cada_documento(app, client, login, cenario):
    doc = _criar_documento_no_disco(app, cenario["processo1_id"], "contrato.pdf")

    login("pacote@teste.com")
    client.get(f"/clientes/{cenario['cliente_id']}/exportar-pacote-completo")

    log_cliente = LogAtividade.query.filter_by(entidade="Cliente", entidade_id=cenario["cliente_id"],
                                                acao="exportou_pacote_completo_cliente").first()
    assert log_cliente is not None
    assert log_cliente.usuario_id == cenario["usuario_id"]

    log_documento = LogAtividade.query.filter_by(entidade="Documento", entidade_id=doc.id,
                                                  acao="baixou_documento").first()
    assert log_documento is not None, \
        "documento incluído no pacote em massa também deveria aparecer no histórico de acesso do documento"


def test_cliente_sem_documento_nenhum_gera_zip_so_com_json(client, login, cenario):
    login("pacote@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/exportar-pacote-completo")
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    assert zf.namelist() == ["dados_lgpd.json"]


def test_usuario_de_outra_unidade_nao_acessa_pacote(client, login, cenario, empresa_basica, criar_usuario):
    from app.models import Unidade
    outra_unidade = Unidade(nome="Filial", codigo="F1", empresa_id=empresa_basica["empresa_id"])
    db.session.add(outra_unidade)
    db.session.commit()
    criar_usuario(outra_unidade.id, "fora@teste.com", papel="advogado")

    login("fora@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/exportar-pacote-completo")
    assert r.status_code == 403
