"""
Vigilância do Diário Oficial da União (DOU) via INLABS — ver
app/utils/conector_inlabs_dou.py (download/parsing) e PENDENCIAS.md (seção
mais recente) para a pesquisa completa.

Cobertura deste arquivo — o conector puro (rede sempre mockada, nunca bate
no INLABS real):
  1. login_configurado(): honesto sobre credencial ausente/presente.
  2. _autenticar(): erro sem credencial, erro de rede, erro de credencial
     inválida (sem cookie de sessão na resposta), sucesso.
  3. _listar_arquivos_do_dia(): parsing do HTML (só pega <a title="Baixar
     Arquivo">, só .zip, só das seções pedidas).
  4. _parsear_materia(): estrutura XML real pesquisada no schema do Ro-DOU
     (id, name, pubDate, titulo, ementa, texto com HTML embutido).
  5. _extrair_assinatura / _texto_plano: extração best-effort do campo
     `texto` (HTML embutido).
  6. baixar_materias_do_dia(): fluxo fim-a-fim com tudo mockado, incluindo
     limpeza do diretório temporário (sucesso e erro).
"""
import os
import shutil
import tempfile
import zipfile
from datetime import date
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.utils import conector_inlabs_dou as mod
from app.utils.conector_inlabs_dou import (
    login_configurado, _autenticar, ConexaoInlabsError,
    _listar_arquivos_do_dia, _parsear_materia, _extrair_assinatura,
    _texto_plano, baixar_materias_do_dia, _secao_do_nome_arquivo,
)


def _resp(status=200, text="", cookies=None):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.content = text.encode("utf-8")
    return r


# ---------------------------------------------------------------------------
# 1. login_configurado
# ---------------------------------------------------------------------------

def test_login_configurado_falso_sem_credencial(app):
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""
    with app.app_context():
        assert login_configurado() is False


def test_login_configurado_verdadeiro_com_credencial(app):
    app.config["INLABS_EMAIL"] = "escritorio@example.com"
    app.config["INLABS_SENHA"] = "senha-inlabs"
    with app.app_context():
        assert login_configurado() is True
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""


# ---------------------------------------------------------------------------
# 2. _autenticar
# ---------------------------------------------------------------------------

def test_autenticar_sem_credencial_levanta_erro(app):
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""
    with app.app_context(), pytest.raises(ConexaoInlabsError, match="não configurada"):
        _autenticar()


def test_autenticar_falha_de_rede(app):
    app.config["INLABS_EMAIL"] = "e@x.com"
    app.config["INLABS_SENHA"] = "s"
    with app.app_context():
        with patch.object(mod.requests.Session, "post", side_effect=requests.ConnectionError("sem rede")):
            with pytest.raises(ConexaoInlabsError, match="Falha de conexão"):
                _autenticar()
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""


def test_autenticar_sem_cookie_de_sessao_levanta_erro(app):
    app.config["INLABS_EMAIL"] = "e@x.com"
    app.config["INLABS_SENHA"] = "senha-errada"
    with app.app_context():
        with patch.object(mod.requests.Session, "post", return_value=_resp()):
            with pytest.raises(ConexaoInlabsError, match="Login no INLABS falhou"):
                _autenticar()
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""


def test_autenticar_sucesso_devolve_sessao_com_cookie(app):
    app.config["INLABS_EMAIL"] = "e@x.com"
    app.config["INLABS_SENHA"] = "senha-certa"

    def _post_com_cookie(self, *args, **kwargs):
        self.cookies.set("inlabs_session_cookie", "abc123")
        return _resp()

    with app.app_context():
        with patch.object(mod.requests.Session, "post", _post_com_cookie):
            sessao = _autenticar()
    assert sessao.cookies.get("inlabs_session_cookie") == "abc123"
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""


# ---------------------------------------------------------------------------
# 3. _listar_arquivos_do_dia
# ---------------------------------------------------------------------------

def test_listar_arquivos_do_dia_filtra_por_secao_e_extensao():
    html = """
    <html><body>
      <a title="Baixar Arquivo" href="?p=2026-09-22&dl=2026-09-22-DO1.zip">DO1</a>
      <a title="Baixar Arquivo" href="?p=2026-09-22&dl=2026-09-22-DO2.zip">DO2</a>
      <a title="Baixar Arquivo" href="?p=2026-09-22&dl=2026-09-22-DO3E.zip">DO3E</a>
      <a title="Outro link" href="?p=2026-09-22&dl=irrelevante.zip">Irrelevante</a>
      <a title="Baixar Arquivo" href="?p=2026-09-22&dl=2026-09-22-DO1.pdf">Não é zip</a>
    </body></html>
    """
    sessao = MagicMock()
    sessao.get.return_value = _resp(text=html)
    sessao.cookies.get.return_value = "cookie-x"

    hrefs = _listar_arquivos_do_dia(sessao, date(2026, 9, 22), ("DO1", "DO2"))
    assert len(hrefs) == 2
    assert all("DO1.zip" in h or "DO2.zip" in h for h in hrefs)
    assert not any("DO3E" in h for h in hrefs)


def test_listar_arquivos_do_dia_falha_de_rede_levanta_erro():
    sessao = MagicMock()
    sessao.get.side_effect = requests.ConnectionError("sem rede")
    sessao.cookies.get.return_value = "cookie-x"
    with pytest.raises(ConexaoInlabsError, match="Falha de conexão"):
        _listar_arquivos_do_dia(sessao, date(2026, 9, 22), ("DO1",))


def test_listar_arquivos_do_dia_sem_edicao_devolve_lista_vazia():
    """Domingo/feriado sem edição de alguma seção — nunca é erro."""
    sessao = MagicMock()
    sessao.get.return_value = _resp(text="<html><body>nenhuma edição hoje</body></html>")
    sessao.cookies.get.return_value = "cookie-x"
    assert _listar_arquivos_do_dia(sessao, date(2026, 9, 22), ("DO1", "DO2", "DO3")) == []


# ---------------------------------------------------------------------------
# 4. _parsear_materia — estrutura XML pesquisada no schema real do Ro-DOU
# ---------------------------------------------------------------------------

_XML_MATERIA_COMPLETA = """<?xml version="1.0" encoding="utf-8"?>
<article id="mat-123" name="portaria-123-2026-dou" pubDate="22/09/2026" artType="Portaria">
  <body>
    <Identifica>Ministério da Justiça</Identifica>
    <titulo>PORTARIA Nº 123, DE 22 DE SETEMBRO DE 2026</titulo>
    <subtitulo>Dispõe sobre procedimento administrativo</subtitulo>
    <ementa>Regulamenta o procedimento X</ementa>
    <texto>&lt;p&gt;O Ministro de Estado resolve: Art. 1º Fica aprovado o procedimento.&lt;/p&gt;&lt;p class="assina"&gt;FULANO DE TAL&lt;/p&gt;</texto>
  </body>
</article>
"""

_XML_SEM_ID = """<?xml version="1.0" encoding="utf-8"?>
<article name="sem-id" pubDate="22/09/2026">
  <body><titulo>Sem ID, não pode ser processado</titulo></body>
</article>
"""


def _escrever_xml(tmp_path, conteudo, nome="materia.xml"):
    caminho = os.path.join(tmp_path, nome)
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo)
    return caminho


def test_parsear_materia_extrai_campos_principais(tmp_path):
    caminho = _escrever_xml(str(tmp_path), _XML_MATERIA_COMPLETA)
    materia = _parsear_materia(caminho, "DO1")
    assert materia is not None
    assert materia["id_materia_fonte"] == "mat-123"
    assert materia["secao"] == "DO1"
    assert materia["orgao"] == "Ministério da Justiça"
    assert materia["name"] == "portaria-123-2026-dou"
    assert materia["titulo"] == "PORTARIA Nº 123, DE 22 DE SETEMBRO DE 2026"
    assert materia["ementa"] == "Regulamenta o procedimento X"
    assert materia["subtitulo"] == "Dispõe sobre procedimento administrativo"
    assert "aprovado o procedimento" in materia["texto_plano"]
    assert materia["assinatura"] == "FULANO DE TAL"
    assert materia["data_publicacao_str"] == "22/09/2026"


def test_parsear_materia_sem_id_devolve_none(tmp_path):
    caminho = _escrever_xml(str(tmp_path), _XML_SEM_ID)
    assert _parsear_materia(caminho, "DO1") is None


def test_parsear_materia_xml_corrompido_devolve_none(tmp_path):
    caminho = _escrever_xml(str(tmp_path), "isto não é xml válido <<<")
    assert _parsear_materia(caminho, "DO1") is None


# ---------------------------------------------------------------------------
# 5. _extrair_assinatura / _texto_plano
# ---------------------------------------------------------------------------

def test_extrair_assinatura_encontra_tag_assina():
    html = '<p>Texto do ato.</p><p class="assina">MARIA DA SILVA</p>'
    assert _extrair_assinatura(html) == "MARIA DA SILVA"


def test_extrair_assinatura_sem_tag_devolve_none():
    assert _extrair_assinatura("<p>Sem assinatura marcada.</p>") is None
    assert _extrair_assinatura("") is None
    assert _extrair_assinatura(None) is None


def test_texto_plano_remove_html():
    html = "<p>Primeiro parágrafo.</p><p>Segundo <b>parágrafo</b>.</p>"
    texto = _texto_plano(html)
    assert "<p>" not in texto
    assert "Primeiro parágrafo." in texto
    assert "Segundo" in texto and "parágrafo" in texto


# ---------------------------------------------------------------------------
# 6. baixar_materias_do_dia — fim a fim, tudo mockado
# ---------------------------------------------------------------------------

def _zip_com_xml(caminho_zip, nome_xml, conteudo_xml):
    with zipfile.ZipFile(caminho_zip, "w") as zf:
        zf.writestr(nome_xml, conteudo_xml)


def test_baixar_materias_do_dia_sem_credencial_levanta_erro(app):
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""
    with app.app_context(), pytest.raises(ConexaoInlabsError):
        baixar_materias_do_dia(data_referencia=date(2026, 9, 22))


def test_baixar_materias_do_dia_sem_edicao_devolve_lista_vazia(app):
    app.config["INLABS_EMAIL"] = "e@x.com"
    app.config["INLABS_SENHA"] = "s"

    def _post_com_cookie(self, *args, **kwargs):
        self.cookies.set("inlabs_session_cookie", "abc123")
        return _resp()

    with app.app_context():
        with patch.object(mod.requests.Session, "post", _post_com_cookie), \
             patch.object(mod.requests.Session, "get", return_value=_resp(text="<html></html>")):
            materias = baixar_materias_do_dia(data_referencia=date(2026, 9, 22), secoes=("DO1",))
    assert materias == []
    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""


def test_baixar_materias_do_dia_fluxo_completo_limpa_temporarios(app, tmp_path):
    app.config["INLABS_EMAIL"] = "e@x.com"
    app.config["INLABS_SENHA"] = "s"

    caminho_zip = os.path.join(str(tmp_path), "2026-09-22-DO1.zip")
    _zip_com_xml(caminho_zip, "materia1.xml", _XML_MATERIA_COMPLETA)
    with open(caminho_zip, "rb") as f:
        conteudo_zip = f.read()

    html_listagem = (
        '<html><body><a title="Baixar Arquivo" '
        'href="?p=2026-09-22&amp;dl=2026-09-22-DO1.zip">DO1</a></body></html>'
    )

    pastas_criadas = []
    mkdtemp_original = tempfile.mkdtemp

    def _mkdtemp_rastreado(*args, **kwargs):
        pasta = mkdtemp_original(*args, **kwargs)
        pastas_criadas.append(pasta)
        return pasta

    def _post_com_cookie(self, *args, **kwargs):
        self.cookies.set("inlabs_session_cookie", "abc123")
        return _resp()

    def _get_sequencial(self, url, *args, **kwargs):
        if "dl=" in url:  # chamada de download de um .zip específico
            r = MagicMock()
            r.status_code = 200
            r.content = conteudo_zip
            return r
        return _resp(text=html_listagem)  # chamada de listagem (index.php?p=<data>)

    with app.app_context():
        with patch.object(mod.requests.Session, "post", _post_com_cookie), \
             patch.object(mod.requests.Session, "get", _get_sequencial), \
             patch.object(mod.tempfile, "mkdtemp", side_effect=_mkdtemp_rastreado):
            materias = baixar_materias_do_dia(data_referencia=date(2026, 9, 22), secoes=("DO1",))

    assert len(materias) == 1
    assert materias[0]["id_materia_fonte"] == "mat-123"
    assert materias[0]["secao"] == "DO1"
    assert materias[0]["data_publicacao_fallback"] == date(2026, 9, 22)

    # limpeza: a pasta temporária criada não deve sobrar em disco
    assert pastas_criadas
    assert not os.path.exists(pastas_criadas[0])

    app.config["INLABS_EMAIL"] = ""
    app.config["INLABS_SENHA"] = ""


def test_secao_do_nome_arquivo_usa_nome_do_zip():
    assert _secao_do_nome_arquivo("/tmp/x/2026-09-22-DO2.xml", ("DO1", "DO2", "DO3")) == "DO2"
    assert _secao_do_nome_arquivo("/tmp/x/sem-secao.xml", ("DO1", "DO2")) == "DO1"
