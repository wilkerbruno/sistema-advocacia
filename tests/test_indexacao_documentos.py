"""
Item 3 da lista de pipeline de IA jurídica (PENDENCIAS.md, seção -103):
"Ingestão e indexação" — OCR de PDF escaneado, corte por evento
("particionar_em_chunks") e índice vetorial (embedding via Gemini BYOK,
quando a empresa tiver chave cadastrada) — ver
app/utils/indexacao_documentos.py, app/utils/ocr_documento.py e
app/models/indexacao.py::DocumentoIndexado.

Nenhuma chamada de rede de verdade (Gemini) nem OCR real acontece aqui —
`gerar_embeddings_lote`/`ocr_disponivel`/`ocr_pagina_pdf` são sempre
mockados/monkeypatchados, mesmo espírito de test_gemini_byok.py e
test_referencia_estilo_minuta.py (que já usa reportlab para gerar PDF de
teste sem depender de nenhum arquivo real).
"""
import os

import pytest
from cryptography.fernet import Fernet

from app.extensions import db
from app.models import Cliente, Processo, Documento, DocumentoIndexado, Empresa
from app.utils import gemini_api, cofre
import app.utils.indexacao_documentos as idx_mod
from app.utils.indexacao_documentos import (
    particionar_em_chunks, indexar_documento, buscar_trechos_relevantes, IndexacaoNaoSuportadaError,
)


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------

@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "indexacao@teste.com", papel="advogado", nome="Advogado Indexação")
    cliente = Cliente(nome="Cliente Indexação", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-IDX-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id)
    db.session.add(processo)
    db.session.commit()
    return dict(usuario_id=usuario_id, unidade_id=unidade_id, empresa_id=empresa_basica["empresa_id"],
                processo_id=processo.id, usuario_email="indexacao@teste.com")


@pytest.fixture()
def cofre_configurado(app):
    """Mesma necessidade de test_gemini_byok.py::cofre_configurado — sem
    isso, `cifrar_segredo`/`decifrar_segredo` levantam CofreNaoConfiguradoError."""
    chave_original = app.config.get("COFRE_SENHA_PROCESSO_KEY")
    app.config["COFRE_SENHA_PROCESSO_KEY"] = Fernet.generate_key().decode()
    yield
    app.config["COFRE_SENHA_PROCESSO_KEY"] = chave_original


def _pasta(app, processo_id):
    pasta = os.path.join(app.config["UPLOAD_FOLDER"], str(processo_id))
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _doc_txt(app, processo_id, texto, nome="peticao.txt"):
    caminho = os.path.join(_pasta(app, processo_id), nome)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(texto)
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="peticao", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def _doc_docx(app, processo_id, texto, nome="peticao.docx"):
    import docx
    caminho = os.path.join(_pasta(app, processo_id), nome)
    d = docx.Document()
    for paragrafo in texto.split("\n\n"):
        d.add_paragraph(paragrafo)
    d.save(caminho)
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="peticao", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def _doc_pdf_com_texto(app, processo_id, texto, nome="peticao.pdf"):
    from reportlab.pdfgen import canvas
    caminho = os.path.join(_pasta(app, processo_id), nome)
    c = canvas.Canvas(caminho)
    c.drawString(72, 750, texto)
    c.save()
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="peticao", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def _doc_pdf_em_branco(app, processo_id, nome="escaneado.pdf"):
    """PDF sem NENHUMA camada de texto (só `showPage()`) — simula um PDF
    escaneado (imagem pura), candidato ao fallback de OCR."""
    from reportlab.pdfgen import canvas
    caminho = os.path.join(_pasta(app, processo_id), nome)
    c = canvas.Canvas(caminho)
    c.showPage()
    c.save()
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="peticao", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def _empresa_com_chave_gemini(app, empresa_id, chave="AIzaChaveFake"):
    empresa = db.session.get(Empresa, empresa_id)
    empresa.agente_ia_gemini_chave_cifrada = cofre.cifrar_segredo(chave)
    db.session.commit()
    return empresa


# ---------------------------------------------------------------------
# particionar_em_chunks — "corte por evento"
# ---------------------------------------------------------------------

def test_particionar_texto_curto_devolve_um_unico_chunk():
    assert particionar_em_chunks("um texto qualquer bem curto", 500) == ["um texto qualquer bem curto"]


def test_particionar_texto_vazio_devolve_lista_vazia():
    assert particionar_em_chunks("", 500) == []
    assert particionar_em_chunks("   \n\n  ", 500) == []


def test_particionar_respeita_fronteira_de_paragrafo():
    paragrafos = [f"Parágrafo número {i} com algum texto de exemplo para preencher espaço." for i in range(6)]
    texto = "\n\n".join(paragrafos)
    chunks = particionar_em_chunks(texto, 150)

    assert len(chunks) > 1
    assert all(len(c) <= 150 for c in chunks)
    # nenhuma palavra se perde nem se funde — reconstrução por palavras bate
    # exatamente com o texto original (prova que nunca corta no meio de
    # uma palavra: se cortasse, uma palavra apareceria partida em duas).
    palavras_reconstruidas = " ".join(chunks).split()
    palavras_originais = texto.replace("\n\n", " ").split()
    assert palavras_reconstruidas == palavras_originais


def test_particionar_paragrafo_grande_cai_para_fronteira_de_frase():
    frase_longa = " ".join(f"Frase número {i} de exemplo, com um pouco mais de texto." for i in range(15))
    chunks = particionar_em_chunks(frase_longa, 120)

    assert len(chunks) > 1
    assert all(len(c) <= 120 for c in chunks)
    # cada chunk termina em pontuação de frase (nunca no meio de uma frase)
    for c in chunks:
        assert c.rstrip()[-1] in ".;!?"


def test_particionar_frase_unica_maior_que_alvo_usa_corte_bruto_como_ultimo_recurso():
    # sem pontuação nenhuma — não há fronteira de frase possível, então o
    # corte bruto por caractere é o único jeito de não devolver um chunk
    # gigante; documentado como o único caso em que isso acontece.
    texto_sem_pontuacao = "a" * 350
    chunks = particionar_em_chunks(texto_sem_pontuacao, 100)

    assert [len(c) for c in chunks] == [100, 100, 100, 50]
    assert "".join(chunks) == texto_sem_pontuacao


# ---------------------------------------------------------------------
# indexar_documento — .txt / .docx / .pdf, sem chave do Gemini
# ---------------------------------------------------------------------

def test_indexar_documento_txt_sem_chave_gemini_cria_chunks_sem_embedding(app, cenario):
    doc = _doc_txt(app, cenario["processo_id"], "Conteúdo da petição em texto puro, para fins de indexação.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 1
    assert resumo["com_embedding"] == 0
    assert resumo["erro"] is None
    assert doc.indexado_em is not None
    assert doc.erro_indexacao is None

    linhas = DocumentoIndexado.query.filter_by(documento_id=doc.id).all()
    assert len(linhas) == 1
    assert linhas[0].origem_texto == "txt"
    assert linhas[0].embedding is None
    assert linhas[0].processo_id == cenario["processo_id"]


def test_indexar_documento_docx_sem_chave_gemini_cria_chunks(app, cenario):
    doc = _doc_docx(app, cenario["processo_id"], "Primeiro parágrafo do contrato.\n\nSegundo parágrafo do contrato.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] >= 1
    linhas = DocumentoIndexado.query.filter_by(documento_id=doc.id).all()
    assert all(l.origem_texto == "docx" for l in linhas)


def test_indexar_documento_pdf_com_camada_de_texto_nao_usa_ocr(app, cenario, monkeypatch):
    chamou_ocr = []
    monkeypatch.setattr(idx_mod, "ocr_disponivel", lambda: True)
    monkeypatch.setattr(idx_mod, "ocr_pagina_pdf", lambda *a, **k: chamou_ocr.append(1) or "não deveria ter chamado")

    doc = _doc_pdf_com_texto(app, cenario["processo_id"], "Texto nativo do PDF de teste")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 1
    assert resumo["paginas_ocr"] == 0
    assert not chamou_ocr
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.origem_texto == "camada_pdf"
    assert "Texto nativo do PDF" in linha.texto


def test_indexar_documento_extensao_nao_suportada_registra_erro_sem_criar_chunk(app, cenario):
    caminho = os.path.join(_pasta(app, cenario["processo_id"]), "foto.jpg")
    with open(caminho, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0conteudo binario qualquer")
    doc = Documento(processo_id=cenario["processo_id"], nome_original="foto.jpg", nome_arquivo="foto.jpg",
                     categoria="outros", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()

    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 0
    assert "jpg" in resumo["erro"]
    assert doc.indexado_em is not None
    assert doc.erro_indexacao == resumo["erro"]
    assert DocumentoIndexado.query.filter_by(documento_id=doc.id).count() == 0


def test_indexar_documento_arquivo_ausente_no_disco_registra_erro_honesto(app, cenario):
    doc = Documento(processo_id=cenario["processo_id"], nome_original="sumiu.txt", nome_arquivo="sumiu.txt",
                     categoria="outros", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()

    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 0
    assert "não encontrado" in resumo["erro"]
    assert doc.indexado_em is not None


def test_indexar_documento_e_idempotente_ao_reindexar(app, cenario):
    doc = _doc_txt(app, cenario["processo_id"], "Conteúdo que será indexado duas vezes.")
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()
    primeira_contagem = DocumentoIndexado.query.filter_by(documento_id=doc.id).count()

    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()
    segunda_contagem = DocumentoIndexado.query.filter_by(documento_id=doc.id).count()

    assert primeira_contagem == segunda_contagem == 1


# ---------------------------------------------------------------------
# indexar_documento — fallback de OCR (mockado, nunca tesseract de verdade)
# ---------------------------------------------------------------------

def test_indexar_pdf_escaneado_sem_ocr_disponivel_fica_sem_texto(app, cenario, monkeypatch):
    monkeypatch.setattr(idx_mod, "ocr_disponivel", lambda: False)

    doc = _doc_pdf_em_branco(app, cenario["processo_id"])
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 0
    assert resumo["paginas_ocr"] == 0
    assert "Nenhum texto encontrado" in resumo["erro"]


def test_indexar_pdf_escaneado_com_ocr_disponivel_usa_fallback_e_marca_origem(app, cenario, monkeypatch):
    monkeypatch.setattr(idx_mod, "ocr_disponivel", lambda: True)
    monkeypatch.setattr(idx_mod, "ocr_pagina_pdf", lambda caminho, pagina, **k: "Texto reconhecido via OCR simulado.")

    doc = _doc_pdf_em_branco(app, cenario["processo_id"])
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 1
    assert resumo["paginas_ocr"] == 1
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.origem_texto == "ocr"
    assert "OCR simulado" in linha.texto


# ---------------------------------------------------------------------
# indexar_documento — com chave do Gemini (embedding sempre mockado)
# ---------------------------------------------------------------------

def test_indexar_documento_com_chave_gemini_gera_embedding(app, cenario, cofre_configurado, monkeypatch):
    _empresa_com_chave_gemini(app, cenario["empresa_id"])

    def _fake_embeddings(textos, api_key, modelo=None):
        assert api_key == "AIzaChaveFake"
        return [[0.1, 0.2, 0.3] for _ in textos]

    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote", _fake_embeddings)

    doc = _doc_txt(app, cenario["processo_id"], "Texto que vai virar embedding fake.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 1
    assert resumo["com_embedding"] == 1
    assert resumo["erro"] is None
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.embedding is not None
    assert linha.embedding_modelo == gemini_api.MODELO_EMBEDDING_PADRAO


def test_indexar_documento_com_chave_gemini_mas_api_falha_mantem_chunks_sem_embedding(
        app, cenario, cofre_configurado, monkeypatch):
    _empresa_com_chave_gemini(app, cenario["empresa_id"])

    def _fake_falha(textos, api_key, modelo=None):
        raise gemini_api.GeminiIndisponivelError("cota estourada (simulado)")

    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote", _fake_falha)

    doc = _doc_txt(app, cenario["processo_id"], "Texto que não vai conseguir gerar embedding.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    # nunca perde a indexação inteira por causa do embedding falhar — os
    # chunks continuam lá, só sem vetor.
    assert resumo["chunks"] == 1
    assert resumo["com_embedding"] == 0
    assert "cota estourada" in resumo["erro"]
    assert doc.erro_indexacao == resumo["erro"]
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.embedding is None


def test_indexar_documento_sem_chave_gemini_nao_tenta_gerar_embedding(app, cenario, monkeypatch):
    chamou = []
    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote",
                         lambda *a, **k: chamou.append(1) or [])
    # embedding local também indisponível neste teste (sem arquivo baixado)
    # — nenhum dos dois caminhos deveria ser tentado.
    monkeypatch.setattr(idx_mod.ia_local, "embedding_disponivel", lambda: False)

    doc = _doc_txt(app, cenario["processo_id"], "Sem chave nenhuma cadastrada nesta empresa.")
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert not chamou


# ---------------------------------------------------------------------
# indexar_documento — sem chave Gemini, mas com embedding LOCAL disponível
# (PENDENCIAS.md, seção -123) — mesmo mecanismo de fila em segundo plano,
# só troca o provedor. `_obter_modelo_embedding` sempre mockado, nunca
# carrega um GGUF de verdade aqui (ver tests/test_ia_local_embedding.py).
# ---------------------------------------------------------------------

def _mocka_embedding_local(monkeypatch, vetor_por_texto=None, nome_modelo="local:fake-embedding.gguf"):
    """`vetor_por_texto`: dict texto->vetor, ou None pra devolver um vetor
    fixo [0.1, 0.2, 0.3] pra qualquer texto (mesmo padrão de
    `_fake_embeddings` já usado pros testes do Gemini acima)."""
    monkeypatch.setattr(idx_mod.ia_local, "embedding_disponivel", lambda: True)
    monkeypatch.setattr(idx_mod.ia_local, "nome_modelo_embedding", lambda: nome_modelo)

    def _fake(textos):
        if vetor_por_texto is not None:
            return [vetor_por_texto.get(t, [0.0, 0.0, 1.0]) for t in textos]
        return [[0.1, 0.2, 0.3] for _ in textos]

    monkeypatch.setattr(idx_mod.ia_local, "gerar_embeddings_lote", _fake)


def test_indexar_documento_sem_chave_gemini_mas_com_embedding_local_usa_local(app, cenario, monkeypatch):
    _mocka_embedding_local(monkeypatch)

    doc = _doc_txt(app, cenario["processo_id"], "Texto que vai virar embedding local fake.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 1
    assert resumo["com_embedding"] == 1
    assert resumo["erro"] is None
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.embedding is not None
    assert linha.embedding_modelo == "local:fake-embedding.gguf"


def test_indexar_documento_com_chave_gemini_e_local_disponivel_prefere_gemini(
        app, cenario, cofre_configurado, monkeypatch):
    """Gemini (pago, mais preciso) sempre tem prioridade sobre o modelo
    local quando os dois estão disponíveis — nunca o contrário."""
    _empresa_com_chave_gemini(app, cenario["empresa_id"])
    _mocka_embedding_local(monkeypatch)

    chamou_local = []
    monkeypatch.setattr(idx_mod.ia_local, "gerar_embeddings_lote", lambda textos: chamou_local.append(1) or [])
    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote",
                         lambda textos, api_key, modelo=None: [[0.9, 0.9, 0.9] for _ in textos])

    doc = _doc_txt(app, cenario["processo_id"], "Empresa com os dois provedores disponíveis.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["com_embedding"] == 1
    assert not chamou_local
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.embedding_modelo == gemini_api.MODELO_EMBEDDING_PADRAO


def test_indexar_documento_com_falha_no_embedding_local_mantem_chunks_sem_embedding(app, cenario, monkeypatch):
    monkeypatch.setattr(idx_mod.ia_local, "embedding_disponivel", lambda: True)
    monkeypatch.setattr(idx_mod.ia_local, "nome_modelo_embedding", lambda: "local:fake-embedding.gguf")

    def _falha(textos):
        raise idx_mod.ia_local.ModeloIndisponivelError("modelo local indisponível (simulado)")

    monkeypatch.setattr(idx_mod.ia_local, "gerar_embeddings_lote", _falha)

    doc = _doc_txt(app, cenario["processo_id"], "Texto que não vai conseguir gerar embedding local.")
    resumo = indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    assert resumo["chunks"] == 1
    assert resumo["com_embedding"] == 0
    assert "modelo local indisponível" in resumo["erro"]
    linha = DocumentoIndexado.query.filter_by(documento_id=doc.id).first()
    assert linha.embedding is None


# ---------------------------------------------------------------------
# buscar_trechos_relevantes — semântico (mockado) e fallback
# ---------------------------------------------------------------------

def test_buscar_trechos_relevantes_sem_nenhum_chunk_devolve_lista_vazia(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    assert buscar_trechos_relevantes(processo, "qualquer consulta") == []


def test_buscar_trechos_relevantes_sem_chave_cai_para_mais_recentes(app, cenario):
    doc1 = _doc_txt(app, cenario["processo_id"], "Primeiro documento indexado.", nome="doc1.txt")
    indexar_documento(doc1, app.config["UPLOAD_FOLDER"])
    db.session.commit()
    doc2 = _doc_txt(app, cenario["processo_id"], "Segundo documento indexado, mais recente.", nome="doc2.txt")
    indexar_documento(doc2, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    processo = db.session.get(Processo, cenario["processo_id"])
    trechos = buscar_trechos_relevantes(processo, "consulta qualquer", top_k=6)

    assert len(trechos) == 2
    assert trechos[0].documento_id == doc2.id  # mais recente primeiro


def test_buscar_trechos_relevantes_com_embedding_ordena_por_similaridade(app, cenario, cofre_configurado, monkeypatch):
    _empresa_com_chave_gemini(app, cenario["empresa_id"])

    # três chunks com embeddings conhecidos — vetor da consulta é [1, 0, 0],
    # então o chunk "parecido" (mesma direção) deve vencer a busca.
    vetores_documento = {
        "parecido com a consulta": [1.0, 0.0, 0.0],
        "quase ortogonal": [0.0, 1.0, 0.0],
        "oposto da consulta": [-1.0, 0.0, 0.0],
    }

    def _fake_embeddings(textos, api_key, modelo=None):
        return [vetores_documento.get(t, [0.0, 0.0, 1.0]) for t in textos]

    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote", _fake_embeddings)

    for texto in vetores_documento:
        doc = _doc_txt(app, cenario["processo_id"], texto, nome=f"{texto[:6]}.txt")
        indexar_documento(doc, app.config["UPLOAD_FOLDER"])
        db.session.commit()

    def _fake_embeddings_consulta(textos, api_key, modelo=None):
        assert textos == ["minha consulta de busca"]
        return [[1.0, 0.0, 0.0]]

    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote", _fake_embeddings_consulta)

    processo = db.session.get(Processo, cenario["processo_id"])
    trechos = buscar_trechos_relevantes(processo, "minha consulta de busca", top_k=3)

    assert trechos[0].texto == "parecido com a consulta"
    assert trechos[-1].texto == "oposto da consulta"


def test_buscar_trechos_relevantes_falha_de_embedding_cai_para_fallback_sem_quebrar(
        app, cenario, cofre_configurado, monkeypatch):
    _empresa_com_chave_gemini(app, cenario["empresa_id"])
    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote",
                         lambda textos, api_key, modelo=None: [[0.5, 0.5] for _ in textos])

    doc = _doc_txt(app, cenario["processo_id"], "Documento indexado com embedding de verdade.")
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    def _falha_na_consulta(textos, api_key, modelo=None):
        raise gemini_api.GeminiIndisponivelError("falha simulada na hora da busca")

    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote", _falha_na_consulta)

    processo = db.session.get(Processo, cenario["processo_id"])
    trechos = buscar_trechos_relevantes(processo, "consulta qualquer")
    assert len(trechos) == 1  # cai pro fallback (mais recentes) em vez de propagar o erro


# ---------------------------------------------------------------------
# buscar_trechos_relevantes — embedding LOCAL (sem chave Gemini) e a regra
# de nunca misturar vetores de proveniências diferentes (PENDENCIAS.md,
# seção -123)
# ---------------------------------------------------------------------

def test_buscar_trechos_relevantes_sem_chave_gemini_usa_embedding_local(app, cenario, monkeypatch):
    vetores_documento = {
        "parecido com a consulta": [1.0, 0.0, 0.0],
        "oposto da consulta": [-1.0, 0.0, 0.0],
    }
    _mocka_embedding_local(monkeypatch, vetor_por_texto=vetores_documento)

    for texto in vetores_documento:
        doc = _doc_txt(app, cenario["processo_id"], texto, nome=f"{texto[:6]}.txt")
        indexar_documento(doc, app.config["UPLOAD_FOLDER"])
        db.session.commit()

    _mocka_embedding_local(monkeypatch, vetor_por_texto={"minha consulta": [1.0, 0.0, 0.0]})

    processo = db.session.get(Processo, cenario["processo_id"])
    trechos = buscar_trechos_relevantes(processo, "minha consulta", top_k=2)

    assert trechos[0].texto == "parecido com a consulta"
    assert trechos[-1].texto == "oposto da consulta"


def test_buscar_trechos_relevantes_nunca_mistura_embedding_de_modelos_diferentes(
        app, cenario, cofre_configurado, monkeypatch):
    """Um chunk indexado com o Gemini (de quando a empresa tinha chave) e
    outro indexado depois com o modelo local (empresa removeu a chave) —
    a busca atual (local, sem chave) só pode considerar o chunk do MESMO
    modelo que vai gerar o vetor da consulta agora; o chunk do Gemini fica
    de fora da comparação semântica (nunca é tratado como comparável), mas
    continua existindo (ainda entraria no fallback de "mais recentes")."""
    _empresa_com_chave_gemini(app, cenario["empresa_id"])
    monkeypatch.setattr(idx_mod.gemini_api, "gerar_embeddings_lote",
                         lambda textos, api_key, modelo=None: [[9.0, 9.0, 9.0] for _ in textos])
    doc_gemini = _doc_txt(app, cenario["processo_id"], "Chunk antigo, indexado com Gemini.", nome="gemini.txt")
    indexar_documento(doc_gemini, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    # empresa perdeu a chave do Gemini — próxima indexação usa o local
    empresa = db.session.get(Empresa, cenario["empresa_id"])
    empresa.agente_ia_gemini_chave_cifrada = None
    db.session.commit()
    _mocka_embedding_local(monkeypatch, vetor_por_texto={"Chunk novo, indexado com o modelo local.": [1.0, 0.0, 0.0]})
    doc_local = _doc_txt(app, cenario["processo_id"], "Chunk novo, indexado com o modelo local.", nome="local.txt")
    indexar_documento(doc_local, app.config["UPLOAD_FOLDER"])
    db.session.commit()

    _mocka_embedding_local(monkeypatch, vetor_por_texto={"consulta atual": [1.0, 0.0, 0.0]})
    processo = db.session.get(Processo, cenario["processo_id"])
    trechos = buscar_trechos_relevantes(processo, "consulta atual", top_k=6)

    # só o chunk local participa da comparação semântica — o do Gemini,
    # com um `embedding_modelo` diferente, nunca é comparado a um vetor do
    # modelo local (evita uma "similaridade" sem significado nenhum).
    assert trechos[0].documento_id == doc_local.id


# ---------------------------------------------------------------------
# Rotas: add_documento enfileira o job, reindexar_documento também
# ---------------------------------------------------------------------

def _fake_fila(monkeypatch, modulo):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append((func_path, args))
        return None

    monkeypatch.setattr(modulo, "enfileirar", _fake_enfileirar)
    return chamadas


def test_add_documento_enfileira_job_de_indexacao(app, client, login, post_csrf, cenario, monkeypatch):
    import app.routes.processos as processos_mod
    chamadas = _fake_fila(monkeypatch, processos_mod)

    login(cenario["usuario_email"])
    import io
    r = client.get(f"/processos/{cenario['processo_id']}")
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode("utf-8")).group(1)

    resp = client.post(f"/processos/{cenario['processo_id']}/documentos", data={
        "arquivo": (io.BytesIO(b"conteudo de teste"), "arquivo.txt"),
        "categoria": "outros", "csrf_token": token,
    }, content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200

    doc = Documento.query.filter_by(processo_id=cenario["processo_id"], nome_original="arquivo.txt").first()
    assert doc is not None
    assert len(chamadas) == 1
    assert chamadas[0][0] == "app.jobs.indexacao_jobs.indexar_documento_job"
    assert chamadas[0][1] == (doc.id,)


def test_reindexar_documento_enfileira_job_de_novo(app, client, login, post_csrf, cenario, monkeypatch):
    import app.routes.processos as processos_mod
    chamadas = _fake_fila(monkeypatch, processos_mod)

    doc = _doc_txt(app, cenario["processo_id"], "Documento já anexado antes de existir indexação.")

    login(cenario["usuario_email"])
    resp = post_csrf(f"/processos/documentos/{doc.id}/reindexar", {},
                      get_url=f"/processos/{cenario['processo_id']}")
    assert resp.status_code == 200
    assert len(chamadas) == 1
    assert chamadas[0] == ("app.jobs.indexacao_jobs.indexar_documento_job", (doc.id,))


def test_excluir_documento_remove_chunks_indexados_sem_erro_de_integridade(app, client, login, post_csrf, cenario):
    doc = _doc_txt(app, cenario["processo_id"], "Documento que será indexado e depois excluído.")
    indexar_documento(doc, app.config["UPLOAD_FOLDER"])
    db.session.commit()
    assert DocumentoIndexado.query.filter_by(documento_id=doc.id).count() == 1

    login(cenario["usuario_email"])
    resp = post_csrf(f"/processos/documentos/{doc.id}/excluir", {},
                      get_url=f"/processos/{cenario['processo_id']}")
    assert resp.status_code == 200
    assert db.session.get(Documento, doc.id) is None
    assert DocumentoIndexado.query.filter_by(documento_id=doc.id).count() == 0
