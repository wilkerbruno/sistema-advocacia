"""
Timbrado do escritório (logo nos PDFs gerados) — PENDENCIAS.md, seção
-69. Cobre: upload válido aparece no cabeçalho do recibo (indiretamente:
o recibo continua um PDF válido com a logo cadastrada), upload de
extensão errada é rejeitado, upload de conteúdo que não é uma imagem de
verdade (apesar da extensão certa) é rejeitado sem deixar arquivo
temporário esfarrapado pra trás, remover a logo apaga o arquivo em disco,
e só admin da própria empresa consegue configurar/ver isso.
"""
import io
import os
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image

from app.extensions import db
from app.models import Empresa, Lancamento
from app.utils import timbrado


def _png_valido_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 20), color=(10, 20, 30)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.read()


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin-timbrado@teste.com", papel="admin", nome="Admin Timbrado")
    adv_id = criar_usuario(unidade_id, "adv-timbrado@teste.com", papel="advogado", nome="Advogado Timbrado")

    lancamento = Lancamento(descricao="Honorario pago pra recibo com timbrado", tipo="honorario",
                             natureza="receita", valor=Decimal("900.00"), status="pago",
                             data_pagamento=date.today(), forma_pagamento="Pix",
                             unidade_id=unidade_id, criado_por_id=admin_id)
    db.session.add(lancamento)
    db.session.commit()

    return {"empresa_id": empresa_basica["empresa_id"], "unidade_id": unidade_id,
            "admin_id": admin_id, "adv_id": adv_id, "lancamento_id": lancamento.id}


def test_upload_logo_valida_persiste_e_recibo_continua_pdf_valido(client, login, post_csrf, cenario, app):
    login("admin-timbrado@teste.com")

    r = post_csrf("/minhas-integracoes/timbrado",
                   {"logo": (io.BytesIO(_png_valido_bytes()), "logo_escritorio.png")},
                   get_url="/minhas-integracoes")
    assert r.status_code == 200

    empresa = db.session.get(Empresa, cenario["empresa_id"])
    assert empresa.logo_arquivo == "logo.png"

    with app.app_context():
        caminho = timbrado.caminho_logo(app.config["UPLOAD_FOLDER"], empresa)
    assert caminho is not None and os.path.isfile(caminho)

    # A pré-visualização na própria tela serve os bytes certos.
    r_imagem = client.get("/minhas-integracoes/timbrado/imagem")
    assert r_imagem.status_code == 200
    assert r_imagem.mimetype == "image/png"

    # O recibo continua sendo gerado normalmente (agora com a logo).
    r_recibo = client.get(f"/financeiro/{cenario['lancamento_id']}/recibo")
    assert r_recibo.status_code == 200
    assert r_recibo.mimetype == "application/pdf"
    assert r_recibo.data[:4] == b"%PDF"
    assert len(r_recibo.data) > 500


def test_upload_extensao_nao_permitida_e_rejeitado(client, login, post_csrf, cenario):
    login("admin-timbrado@teste.com")

    r = post_csrf("/minhas-integracoes/timbrado",
                   {"logo": (io.BytesIO(b"conteudo qualquer"), "arquivo.txt")},
                   get_url="/minhas-integracoes")
    assert r.status_code == 200

    empresa = db.session.get(Empresa, cenario["empresa_id"])
    assert empresa.logo_arquivo is None


def test_upload_conteudo_nao_e_imagem_de_verdade_e_rejeitado_sem_deixar_lixo(client, login, post_csrf, cenario, app):
    login("admin-timbrado@teste.com")

    r = post_csrf("/minhas-integracoes/timbrado",
                   {"logo": (io.BytesIO(b"isto nao e um PNG, so tem a extensao"), "falso.png")},
                   get_url="/minhas-integracoes")
    assert r.status_code == 200

    empresa = db.session.get(Empresa, cenario["empresa_id"])
    assert empresa.logo_arquivo is None

    # Não deve sobrar nenhum arquivo TEMPORÁRIO na pasta desta empresa (o
    # que salvar_logo() precisa limpar sozinho ao rejeitar um upload) —
    # não checamos a pasta inteira "vazia" porque, na suíte de testes, o
    # id de Empresa reinicia em 1 a cada teste (transação isolada), então
    # a MESMA pasta em disco pode legitimamente já ter um "logo.png" de
    # outro teste que rodou antes nesta mesma sessão do pytest.
    with app.app_context():
        pasta = timbrado._pasta_logo_empresa(app.config["UPLOAD_FOLDER"], cenario["empresa_id"])
    if os.path.isdir(pasta):
        arquivos_temporarios = [n for n in os.listdir(pasta) if n.startswith("_upload_tmp")]
        assert arquivos_temporarios == [], f"vazou arquivo temporário: {arquivos_temporarios}"


def test_remover_logo_apaga_arquivo_e_recibo_volta_a_ser_so_texto(client, login, post_csrf, cenario, app):
    login("admin-timbrado@teste.com")
    post_csrf("/minhas-integracoes/timbrado",
              {"logo": (io.BytesIO(_png_valido_bytes()), "logo.png")},
              get_url="/minhas-integracoes")

    with app.app_context():
        empresa = db.session.get(Empresa, cenario["empresa_id"])
        caminho_antes = timbrado.caminho_logo(app.config["UPLOAD_FOLDER"], empresa)
    assert caminho_antes is not None

    r = post_csrf("/minhas-integracoes/timbrado/remover", {}, get_url="/minhas-integracoes")
    assert r.status_code == 200

    empresa = db.session.get(Empresa, cenario["empresa_id"])
    assert empresa.logo_arquivo is None
    assert not os.path.isfile(caminho_antes)

    # Recibo continua funcionando normalmente, agora sem logo (comportamento de sempre).
    r_recibo = client.get(f"/financeiro/{cenario['lancamento_id']}/recibo")
    assert r_recibo.status_code == 200
    assert r_recibo.data[:4] == b"%PDF"


def test_apenas_admin_da_empresa_configura_timbrado(client, login, post_csrf, cenario):
    login("adv-timbrado@teste.com")  # papel="advogado", não "admin" — sem acesso a "Minhas Integrações"

    # "/minhas-integracoes" (que post_csrf usaria por padrão pra buscar o
    # token) também é admin-only e devolveria 403 sem token nenhum no
    # corpo — por isso pega o token numa página que este papel PODE
    # acessar (mesmo padrão de tests/test_rbac_financeiro.py).
    r_upload = post_csrf("/minhas-integracoes/timbrado",
                          {"logo": (io.BytesIO(_png_valido_bytes()), "logo.png")},
                          get_url="/agente-ia/")
    assert r_upload.status_code == 403

    r_remover = post_csrf("/minhas-integracoes/timbrado/remover", {}, get_url="/agente-ia/")
    assert r_remover.status_code == 403

    r_imagem = client.get("/minhas-integracoes/timbrado/imagem")
    assert r_imagem.status_code == 403
