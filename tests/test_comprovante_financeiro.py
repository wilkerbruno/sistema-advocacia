"""
Pedido explícito do usuário: "agora no novo lançamento, preciso que tenha
a opção de colocar um arquivo como comprovante sendo receita ou despesa"
— anexar um arquivo (nota fiscal, recibo, boleto pago etc.) a um
Lancamento financeiro, tanto receita quanto despesa.

Mesmo padrão de armazenamento já usado pra Documento de processo (ver
app/routes/processos.py: extensão permitida, nome salvo com uuid, pasta
dedicada em UPLOAD_FOLDER) — aqui um comprovante só por lançamento
(substituir apaga o anterior do disco), sempre opcional: pode ser anexado
na hora de criar o lançamento OU depois, pela listagem (financeiro.py::
anexar_comprovante), pro caso comum de o arquivo chegar depois do
lançamento já registrado.
"""
import io
import os

import pytest

from app.extensions import db
from app.models import Lancamento
from tests.conftest import extrair_csrf


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@comprovante.com", papel="admin", nome="Admin Comprovante")
    return dict(admin_id=admin_id, unidade_id=unidade_id)


def _post_multipart(client, url, data, get_url=None):
    r = client.get(get_url or url)
    token = extrair_csrf(r.data.decode("utf-8"))
    payload = dict(data)
    payload["csrf_token"] = token
    return client.post(url, data=payload, content_type="multipart/form-data", follow_redirects=True)


# ---------------------- Anexar já na criação (novo lançamento) ----------------------

def test_novo_lancamento_receita_com_comprovante_salva_arquivo(client, login, cenario):
    login("admin@comprovante.com")
    r = _post_multipart(client, "/financeiro/novo", {
        "descricao": "Honorário com nota fiscal", "valor": "1500.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"conteudo do pdf"), "nota_fiscal.pdf"),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Honorário com nota fiscal").first()
    assert lanc is not None
    assert lanc.natureza == "receita"
    assert lanc.comprovante_nome_original == "nota_fiscal.pdf"
    assert lanc.comprovante_nome_arquivo is not None
    assert lanc.comprovante_tamanho_kb is not None  # arquivo de teste é pequeno, pode arredondar pra 0 KB
    assert lanc.comprovante_enviado_por_id == cenario["admin_id"]

    from flask import current_app
    caminho = os.path.join(current_app.config["UPLOAD_FOLDER"], "comprovantes_financeiro",
                            str(lanc.id), lanc.comprovante_nome_arquivo)
    assert os.path.isfile(caminho)


def test_novo_lancamento_despesa_com_comprovante_salva_arquivo(client, login, cenario):
    login("admin@comprovante.com")
    r = _post_multipart(client, "/financeiro/novo", {
        "descricao": "Compra de material de escritório", "valor": "230.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"conteudo do recibo"), "recibo.jpg"),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Compra de material de escritório").first()
    assert lanc is not None
    assert lanc.natureza == "despesa"
    assert lanc.comprovante_nome_original == "recibo.jpg"


def test_novo_lancamento_sem_comprovante_continua_funcionando_normalmente(client, login, post_csrf, cenario):
    """O campo é opcional — não pode quebrar o fluxo de sempre pra quem não tem arquivo."""
    login("admin@comprovante.com")
    r = post_csrf("/financeiro/novo", {
        "descricao": "Lançamento sem comprovante", "valor": "100.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Lançamento sem comprovante").first()
    assert lanc is not None
    assert lanc.comprovante_nome_arquivo is None


def test_novo_lancamento_extensao_nao_permitida_nao_cria_lancamento_e_avisa(client, login, cenario):
    login("admin@comprovante.com")
    r = _post_multipart(client, "/financeiro/novo", {
        "descricao": "Lançamento com arquivo inválido", "valor": "50.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"binario qualquer"), "virus.exe"),
    }, get_url="/financeiro/novo")
    assert r.status_code == 200
    assert "Tipo de arquivo não permitido" in r.data.decode("utf-8")

    # Nem o lançamento em si deve ter sido criado — a rejeição do arquivo
    # faz rollback de tudo, pra não sobrar um lançamento "quebrado" sem o
    # comprovante que a pessoa pediu explicitamente pra anexar.
    assert Lancamento.query.filter_by(descricao="Lançamento com arquivo inválido").first() is None


# ---------------------- Anexar depois, pela listagem ----------------------

def test_anexar_comprovante_depois_da_criacao(client, login, post_csrf, cenario):
    login("admin@comprovante.com")
    post_csrf("/financeiro/novo", {
        "descricao": "Lançamento pra anexar depois", "valor": "300.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento pra anexar depois").first()
    assert lanc.comprovante_nome_arquivo is None

    r = _post_multipart(client, f"/financeiro/{lanc.id}/comprovante", {
        "comprovante": (io.BytesIO(b"nota chegou depois"), "nota_atrasada.pdf"),
    }, get_url="/financeiro/")
    assert r.status_code == 200

    db.session.refresh(lanc)
    assert lanc.comprovante_nome_original == "nota_atrasada.pdf"


def test_anexar_comprovante_substitui_o_anterior_e_apaga_arquivo_antigo(client, login, cenario):
    login("admin@comprovante.com")
    r1 = _post_multipart(client, "/financeiro/novo", {
        "descricao": "Lançamento com comprovante trocado", "valor": "400.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"primeiro arquivo"), "primeiro.pdf"),
    }, get_url="/financeiro/novo")
    assert r1.status_code == 200
    lanc = Lancamento.query.filter_by(descricao="Lançamento com comprovante trocado").first()

    from flask import current_app
    pasta = os.path.join(current_app.config["UPLOAD_FOLDER"], "comprovantes_financeiro", str(lanc.id))
    caminho_antigo = os.path.join(pasta, lanc.comprovante_nome_arquivo)
    assert os.path.isfile(caminho_antigo)

    r2 = _post_multipart(client, f"/financeiro/{lanc.id}/comprovante", {
        "comprovante": (io.BytesIO(b"segundo arquivo"), "segundo.pdf"),
    }, get_url="/financeiro/")
    assert r2.status_code == 200

    db.session.refresh(lanc)
    assert lanc.comprovante_nome_original == "segundo.pdf"
    assert not os.path.exists(caminho_antigo), "arquivo antigo deveria ter sido apagado do disco na substituição"


def test_anexar_comprovante_sem_selecionar_arquivo_avisa(client, login, post_csrf, cenario):
    login("admin@comprovante.com")
    post_csrf("/financeiro/novo", {
        "descricao": "Lançamento sem arquivo no anexar", "valor": "10.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento sem arquivo no anexar").first()

    r = post_csrf(f"/financeiro/{lanc.id}/comprovante", {}, get_url="/financeiro/")
    assert r.status_code == 200
    assert "Selecione um arquivo" in r.data.decode("utf-8")


# ---------------------- Baixar ----------------------

def test_baixar_comprovante_funciona_e_devolve_o_nome_original(client, login, cenario):
    login("admin@comprovante.com")
    _post_multipart(client, "/financeiro/novo", {
        "descricao": "Lançamento pra baixar", "valor": "700.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"conteudo a baixar"), "para_baixar.pdf"),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento pra baixar").first()

    r = client.get(f"/financeiro/{lanc.id}/comprovante/baixar")
    assert r.status_code == 200
    assert r.data == b"conteudo a baixar"
    assert "para_baixar.pdf" in r.headers.get("Content-Disposition", "")


def test_baixar_comprovante_inexistente_da_404(client, login, post_csrf, cenario):
    login("admin@comprovante.com")
    post_csrf("/financeiro/novo", {
        "descricao": "Lançamento sem comprovante pra baixar", "valor": "20.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento sem comprovante pra baixar").first()

    r = client.get(f"/financeiro/{lanc.id}/comprovante/baixar")
    assert r.status_code == 404


def test_usuario_de_outra_unidade_nao_baixa_comprovante(client, login, criar_usuario, cenario):
    from app.models import Unidade
    outra_unidade = Unidade(nome="Outra unidade", codigo="M2", empresa_id=Unidade.query.get(cenario["unidade_id"]).empresa_id)
    db.session.add(outra_unidade)
    db.session.commit()
    criar_usuario(outra_unidade.id, "outrounidade@comprovante.com", papel="advogado")

    login("admin@comprovante.com")
    _post_multipart(client, "/financeiro/novo", {
        "descricao": "Lançamento sigiloso de unidade", "valor": "900.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"conteudo restrito"), "restrito.pdf"),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento sigiloso de unidade").first()
    client.get("/logout")

    login("outrounidade@comprovante.com")
    r = client.get(f"/financeiro/{lanc.id}/comprovante/baixar")
    assert r.status_code == 403


# ---------------------- Excluir ----------------------

def test_excluir_comprovante_remove_arquivo_e_limpa_campos(client, login, cenario):
    login("admin@comprovante.com")
    _post_multipart(client, "/financeiro/novo", {
        "descricao": "Lançamento pra excluir comprovante", "valor": "150.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"vai ser excluido"), "excluir.pdf"),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento pra excluir comprovante").first()

    from flask import current_app
    caminho = os.path.join(current_app.config["UPLOAD_FOLDER"], "comprovantes_financeiro",
                            str(lanc.id), lanc.comprovante_nome_arquivo)
    assert os.path.isfile(caminho)

    from tests.conftest import extrair_csrf as _extrair
    r_get = client.get("/financeiro/")
    token = _extrair(r_get.data.decode("utf-8"))
    r = client.post(f"/financeiro/{lanc.id}/comprovante/excluir", data={"csrf_token": token}, follow_redirects=True)
    assert r.status_code == 200

    db.session.refresh(lanc)
    assert lanc.comprovante_nome_arquivo is None
    assert lanc.comprovante_nome_original is None
    assert not os.path.exists(caminho)


# ---------------------- Listagem mostra o controle certo ----------------------

def test_listagem_mostra_link_de_download_quando_ja_tem_comprovante(client, login, cenario):
    login("admin@comprovante.com")
    _post_multipart(client, "/financeiro/novo", {
        "descricao": "Lançamento com comprovante na lista", "valor": "80.00", "natureza": "despesa",
        "unidade_id": str(cenario["unidade_id"]),
        "comprovante": (io.BytesIO(b"algum conteudo"), "algum.pdf"),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento com comprovante na lista").first()

    html = client.get("/financeiro/").data.decode("utf-8")
    assert f'/financeiro/{lanc.id}/comprovante/baixar' in html
    assert "Comprovante" in html


def test_listagem_mostra_form_de_anexar_quando_nao_tem_comprovante(client, login, post_csrf, cenario):
    login("admin@comprovante.com")
    post_csrf("/financeiro/novo", {
        "descricao": "Lançamento sem comprovante na lista", "valor": "60.00", "natureza": "receita",
        "unidade_id": str(cenario["unidade_id"]),
    }, get_url="/financeiro/novo")
    lanc = Lancamento.query.filter_by(descricao="Lançamento sem comprovante na lista").first()

    html = client.get("/financeiro/").data.decode("utf-8")
    assert f'/financeiro/{lanc.id}/comprovante"' in html  # action do form de anexar
    assert "Anexar comprovante" in html
