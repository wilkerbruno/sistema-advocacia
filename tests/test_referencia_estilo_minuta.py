"""
Testa a referência de estilo por documento já anexado, no rascunho de
petição por IA (PENDENCIAS.md, seção -53): extração de texto de
.pdf/.docx/.txt funciona e degrada graciosamente pra tipo não suportado
ou arquivo ilegível/ausente, o texto de referência nunca entra no
"digest" usado pela checagem de grounding (então um dado vazado da
referência pro rascunho continua sendo sinalizado, exatamente como uma
invenção do modelo), e a rota extrai a referência ANTES de enfileirar,
sem nunca bloquear a geração por causa de uma referência problemática.
"""
import os

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Documento, AnaliseProcessoIA
from app.utils.extracao_documento import extrair_texto_documento, ExtracaoNaoSuportadaError
from app.utils.analise_processo_ia import gerar_analise


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "minuta@teste.com", papel="advogado", nome="Advogado Minuta")

    cliente = Cliente(nome="Cliente Minuta", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(numero_interno="P-MINUTA-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id, criado_por_id=usuario_id)
    processo_outro = Processo(numero_interno="P-MINUTA-2", cliente_id=cliente.id, unidade_id=unidade_id,
                               area_direito="Cível", responsavel_id=usuario_id, criado_por_id=usuario_id)
    db.session.add_all([processo, processo_outro])
    db.session.flush()
    db.session.commit()

    return dict(usuario_id=usuario_id, unidade_id=unidade_id, processo_id=processo.id,
                processo_outro_id=processo_outro.id)


def _pasta(app, processo_id):
    pasta = os.path.join(app.config["UPLOAD_FOLDER"], str(processo_id))
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _doc_txt(app, processo_id, texto, nome="modelo.txt"):
    caminho = os.path.join(_pasta(app, processo_id), nome)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(texto)
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="peticao", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def _doc_pdf(app, processo_id, texto, nome="modelo.pdf"):
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


def _doc_docx(app, processo_id, texto, nome="modelo.docx"):
    import docx
    caminho = os.path.join(_pasta(app, processo_id), nome)
    d = docx.Document()
    d.add_paragraph(texto)
    d.save(caminho)
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="peticao", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


def _doc_imagem(app, processo_id, nome="foto.jpg"):
    caminho = os.path.join(_pasta(app, processo_id), nome)
    with open(caminho, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0conteudo binario qualquer")
    doc = Documento(processo_id=processo_id, nome_original=nome, nome_arquivo=nome,
                     categoria="outros", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    return doc


# ---------- extração de texto ----------

def test_extrai_texto_de_txt(app, cenario):
    doc = _doc_txt(app, cenario["processo_id"], "EXCELENTÍSSIMO SENHOR DOUTOR JUIZ — modelo de contestação.")
    texto, truncado = extrair_texto_documento(doc, app.config["UPLOAD_FOLDER"])
    assert "EXCELENTÍSSIMO" in texto
    assert truncado is False


def test_extrai_texto_de_pdf(app, cenario):
    doc = _doc_pdf(app, cenario["processo_id"], "Peticao modelo em PDF de teste")
    texto, truncado = extrair_texto_documento(doc, app.config["UPLOAD_FOLDER"])
    assert "Peticao modelo em PDF" in texto


def test_extrai_texto_de_docx(app, cenario):
    doc = _doc_docx(app, cenario["processo_id"], "Peticao modelo em DOCX de teste")
    texto, truncado = extrair_texto_documento(doc, app.config["UPLOAD_FOLDER"])
    assert "Peticao modelo em DOCX" in texto


def test_tipo_nao_suportado_levanta_erro_especifico(app, cenario):
    doc = _doc_imagem(app, cenario["processo_id"])
    with pytest.raises(ExtracaoNaoSuportadaError):
        extrair_texto_documento(doc, app.config["UPLOAD_FOLDER"])


def test_arquivo_ausente_no_disco_levanta_value_error(app, cenario):
    doc = Documento(processo_id=cenario["processo_id"], nome_original="fantasma.txt",
                     nome_arquivo="nao-existe.txt", categoria="outros", tamanho_kb=1)
    db.session.add(doc)
    db.session.commit()
    with pytest.raises(ValueError):
        extrair_texto_documento(doc, app.config["UPLOAD_FOLDER"])


def test_texto_grande_e_truncado(app, cenario):
    doc = _doc_txt(app, cenario["processo_id"], "A" * 5000)
    texto, truncado = extrair_texto_documento(doc, app.config["UPLOAD_FOLDER"], limite_chars=2000)
    assert truncado is True
    assert len(texto) == 2000


# ---------- gerar_analise com referência (motor, sem HTTP) ----------

def test_referencia_nunca_entra_no_digest_usado_pelo_grounding(app, cenario, monkeypatch):
    """
    Um valor em R$ que só existe no texto de REFERÊNCIA (nunca no processo
    de verdade) tem que continuar sendo sinalizado pela checagem de
    grounding se vazar pro resultado — prova de que a referência não
    "contamina" o que conta como dado real.
    """
    processo = db.session.get(Processo, cenario["processo_id"])

    capturado = {}

    def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        # simula o modelo "vazando" um valor que só está na referência
        return "III — DOS PEDIDOS\nCondenação ao pagamento de R$ 77.777,00 conforme referência."

    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

    resultado, truncado = gerar_analise(
        processo, "rascunho_peticao", instrucao="contestação padrão",
        texto_referencia="Modelo antigo que menciona R$ 77.777,00 só como exemplo de estilo.",
    )

    assert "Modelo antigo que menciona R$ 77.777,00" in capturado["system"], \
        "o texto de referência precisa chegar no prompt mandado pro modelo"
    assert "NÃO aparece nos dados reais" in resultado, \
        "valor vazado da referência (nunca do processo real) tem que ser sinalizado pela checagem de grounding"


def test_resumo_ignora_referencia_de_estilo(app, cenario, monkeypatch):
    processo = db.session.get(Processo, cenario["processo_id"])
    capturado = {}

    def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        return "SITUAÇÃO ATUAL\nProcesso em andamento."

    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

    gerar_analise(processo, "resumo", texto_referencia="isto nunca deveria aparecer no prompt de resumo")
    assert "isto nunca deveria aparecer" not in capturado["system"]


# ---------- rota HTTP (extração + enfileiramento, sem Redis de verdade) ----------

def _fake_fila(monkeypatch):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append(args)
        return None

    import app.routes.processos as mod
    monkeypatch.setattr(mod, "enfileirar", _fake_enfileirar)
    monkeypatch.setattr(mod.agente_ia_router, "provedor_disponivel", lambda empresa: True)
    return chamadas


def test_rota_extrai_referencia_antes_de_enfileirar(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    doc = _doc_txt(app, cenario["processo_id"], "Trecho de estilo de uma petição anterior real.")

    login("minuta@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
        "documento_referencia_id": str(doc.id),
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None
    assert analise.documento_referencia_id == doc.id

    assert len(chamadas) == 1
    texto_referencia_enviado = chamadas[0][-1]
    assert texto_referencia_enviado is not None
    assert "Trecho de estilo" in texto_referencia_enviado


def test_rota_documento_de_outro_processo_e_rejeitado_sem_bloquear_geracao(app, client, login, post_csrf,
                                                                            cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    doc_outro_processo = _doc_txt(app, cenario["processo_outro_id"], "Não pertence a este processo.")

    login("minuta@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
        "documento_referencia_id": str(doc_outro_processo.id),
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert "referência" in r.data.decode("utf-8").lower() or "inválido" in r.data.decode("utf-8").lower()

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None
    assert analise.documento_referencia_id is None
    assert len(chamadas) == 1
    assert chamadas[0][-1] is None, "geração segue sem referência, nunca bloqueada"


def test_rota_tipo_nao_suportado_avisa_mas_nao_bloqueia(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    doc_imagem = _doc_imagem(app, cenario["processo_id"])

    login("minuta@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
        "documento_referencia_id": str(doc_imagem.id),
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None
    assert analise.documento_referencia_id is None
    assert len(chamadas) == 1
    assert chamadas[0][-1] is None


def test_rota_sem_referencia_funciona_como_antes(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)

    login("minuta@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "resumo",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert len(chamadas) == 1
    assert chamadas[0][-1] is None
