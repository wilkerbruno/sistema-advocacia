"""
Embedding local (PENDENCIAS.md, seção -123) — modelo separado, bem menor
que o de chat, usado pra gerar o Índice vetorial da indexação de documentos
(app/utils/indexacao_documentos.py) quando a empresa não tem chave do
Gemini cadastrada. Ver app/utils/ia_local.py.

Nunca carrega um arquivo GGUF de verdade aqui (não existe nenhum baixado
neste ambiente de teste, de propósito — mesma limitação já registrada para
o modelo de chat) — `embedding_disponivel()`/`nome_modelo_embedding()` só
mexem com o caminho do arquivo (checagem de existência, nunca carregam
nada em memória) e `gerar_embeddings_lote` é testado com
`_obter_modelo_embedding` mockado, mesmo espírito de como
test_indexacao_documentos.py mocka `gemini_api.gerar_embeddings_lote`.
"""
import os

import pytest

from app.utils import ia_local


@pytest.fixture(autouse=True)
def _reseta_singleton_embedding():
    """O modelo carregado fica cacheado num global do módulo (mesmo padrão
    do modelo de chat) — sem resetar entre testes, um teste que mocka
    `_obter_modelo_embedding` vazaria o fake pros testes seguintes."""
    ia_local._modelo_embedding = None
    yield
    ia_local._modelo_embedding = None


def test_embedding_disponivel_falso_sem_arquivo(app):
    app.config["IA_LOCAL_EMBEDDING_MODELO_PATH"] = "/caminho/que/nao/existe.gguf"
    assert ia_local.embedding_disponivel() is False


def test_embedding_disponivel_verdadeiro_com_arquivo(app, tmp_path):
    caminho = tmp_path / "modelo-embedding-fake.gguf"
    caminho.write_bytes(b"nao e um gguf de verdade, so pra checagem de existencia")
    app.config["IA_LOCAL_EMBEDDING_MODELO_PATH"] = str(caminho)
    assert ia_local.embedding_disponivel() is True


def test_nome_modelo_embedding_deriva_do_nome_do_arquivo(app):
    app.config["IA_LOCAL_EMBEDDING_MODELO_PATH"] = "/qualquer/pasta/paraphrase-multilingual-MiniLM-L12-v2-Q8_0.gguf"
    assert ia_local.nome_modelo_embedding() == "local:paraphrase-multilingual-MiniLM-L12-v2-Q8_0.gguf"


def test_nome_modelo_embedding_none_sem_caminho_configurado(app):
    app.config["IA_LOCAL_EMBEDDING_MODELO_PATH"] = None
    assert ia_local.nome_modelo_embedding() is None


def test_gerar_embeddings_lote_levanta_erro_sem_arquivo(app):
    app.config["IA_LOCAL_EMBEDDING_MODELO_PATH"] = "/caminho/que/nao/existe.gguf"
    with pytest.raises(ia_local.ModeloIndisponivelError):
        ia_local.gerar_embeddings_lote(["um texto qualquer"])


def test_gerar_embeddings_lote_preserva_ordem_e_quantidade(app, monkeypatch):
    class _ModeloFake:
        def create_embedding(self, texto):
            # devolve um vetor "identificável" — tamanho = comprimento do
            # texto, só pra provar que cada chamada recebeu o texto certo
            # na ordem certa.
            return {"data": [{"embedding": [float(len(texto))]}]}

    monkeypatch.setattr(ia_local, "_obter_modelo_embedding", lambda: _ModeloFake())

    textos = ["a", "bb", "ccc"]
    vetores = ia_local.gerar_embeddings_lote(textos)

    assert vetores == [[1.0], [2.0], [3.0]]


def test_gerar_embeddings_lote_texto_vazio_nao_quebra(app, monkeypatch):
    chamadas = []

    class _ModeloFake:
        def create_embedding(self, texto):
            chamadas.append(texto)
            return {"data": [{"embedding": [0.0]}]}

    monkeypatch.setattr(ia_local, "_obter_modelo_embedding", lambda: _ModeloFake())

    vetores = ia_local.gerar_embeddings_lote(["", "   ", "texto normal"])

    assert len(vetores) == 3
    # texto vazio/só espaço nunca é mandado em branco pro tokenizer (viraria
    # erro) — vira um espaço único, nunca quebra a chamada.
    assert chamadas[0] == " "
    assert chamadas[1] == " "
    assert chamadas[2] == "texto normal"
