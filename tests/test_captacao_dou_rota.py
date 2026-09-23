"""
Rotas de app/routes/captacao_dou.py — cadastro/alternância de palavra-chave,
marcar lida, ignorar (com motivo obrigatório), e isolamento multi-tenant
(mesmo padrão de test_isolamento_multi_tenant_prazos.py / test_captacao_oab.py).
"""
import re
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, PalavraChaveDou, PublicacaoDouCapturada

SENHA = "senha123"


def _criar_empresa_unidade(nome, codigo):
    empresa = Empresa(nome=nome)
    db.session.add(empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade = Unidade(nome=f"Matriz {nome}", codigo=codigo, empresa_id=empresa.id)
    db.session.add(unidade)
    db.session.flush()
    db.session.commit()
    return empresa, unidade


@pytest.fixture()
def cenario(app):
    empresa, unidade = _criar_empresa_unidade("EscritorioX", "EX1")
    admin = Usuario(email="admin@escritoriox.com", unidade_id=unidade.id, papel="admin", nome="Admin")
    admin.set_senha(SENHA)
    db.session.add(admin)
    db.session.commit()
    return dict(empresa_id=empresa.id, unidade_id=unidade.id, admin_id=admin.id)


def _csrf(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# index() — carrega mesmo sem publicações/palavras, e passa `unidades` pro
# template quando o usuário é admin (bug corrigido: a rota precisa computar
# isso, senão o formulário de nova palavra-chave quebra silenciosamente).
# ---------------------------------------------------------------------------

def test_index_carrega_vazio(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    assert r.status_code == 200
    assert "Nenhuma publicação pendente de revisão".encode("utf-8") in r.data


def test_index_admin_ve_select_de_unidade_no_formulario(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    assert r.status_code == 200
    assert b'name="unidade_id"' in r.data


# ---------------------------------------------------------------------------
# nova_palavra_chave
# ---------------------------------------------------------------------------

def test_nova_palavra_chave_cria_com_sucesso(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))
    resp = client.post("/captacao-dou/palavra-chave/nova", data={
        "termo": "Lei 14.133/2021", "unidade_id": cenario["unidade_id"], "csrf_token": token,
    }, follow_redirects=True)
    assert resp.status_code == 200
    palavra = PalavraChaveDou.query.filter_by(termo="Lei 14.133/2021").first()
    assert palavra is not None
    assert palavra.unidade_id == cenario["unidade_id"]
    assert palavra.ativa is True


def test_nova_palavra_chave_vazia_nao_cria(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))
    client.post("/captacao-dou/palavra-chave/nova", data={
        "termo": "  ", "unidade_id": cenario["unidade_id"], "csrf_token": token,
    }, follow_redirects=True)
    assert PalavraChaveDou.query.count() == 0


def test_nova_palavra_chave_duplicada_nao_cria_segunda(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))
    dados = {"termo": "termo repetido", "unidade_id": cenario["unidade_id"], "csrf_token": token}
    client.post("/captacao-dou/palavra-chave/nova", data=dados, follow_redirects=True)

    r2 = client.get("/captacao-dou/")
    token2 = _csrf(r2.data.decode("utf-8"))
    dados2 = {"termo": "termo repetido", "unidade_id": cenario["unidade_id"], "csrf_token": token2}
    client.post("/captacao-dou/palavra-chave/nova", data=dados2, follow_redirects=True)

    assert PalavraChaveDou.query.filter_by(termo="termo repetido").count() == 1


# ---------------------------------------------------------------------------
# alternar_ativo_palavra_chave
# ---------------------------------------------------------------------------

def test_alternar_ativo_palavra_chave(app, client, login, cenario):
    with app.app_context():
        palavra = PalavraChaveDou(unidade_id=cenario["unidade_id"], termo="alternar teste", ativa=True)
        db.session.add(palavra)
        db.session.commit()
        palavra_id = palavra.id

    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))
    resp = client.post(f"/captacao-dou/palavra-chave/{palavra_id}/alternar-ativo",
                        data={"csrf_token": token}, follow_redirects=True)
    assert resp.status_code == 200
    assert PalavraChaveDou.query.get(palavra_id).ativa is False


# ---------------------------------------------------------------------------
# marcar_lida / ignorar
# ---------------------------------------------------------------------------

def _criar_publicacao(unidade_id, id_materia="mat-rota", termo="Cliente Teste"):
    pub = PublicacaoDouCapturada(
        unidade_id=unidade_id, id_materia_fonte=id_materia, secao="DO1", orgao="Órgão Teste",
        titulo="Título Teste", ementa="Ementa teste", texto_trecho="trecho de teste",
        data_publicacao=date(2026, 9, 22), termo_encontrado=termo, status="pendente_revisao",
    )
    db.session.add(pub)
    db.session.commit()
    return pub.id


def test_marcar_lida(app, client, login, cenario):
    with app.app_context():
        pub_id = _criar_publicacao(cenario["unidade_id"])

    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))
    resp = client.post(f"/captacao-dou/{pub_id}/marcar-lida", data={"csrf_token": token}, follow_redirects=True)
    assert resp.status_code == 200
    pub = PublicacaoDouCapturada.query.get(pub_id)
    assert pub.status == "lida"
    assert pub.revisado_por_id == cenario["admin_id"]
    assert pub.revisado_em is not None


def test_ignorar_exige_motivo(app, client, login, cenario):
    with app.app_context():
        pub_id = _criar_publicacao(cenario["unidade_id"])

    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))

    # sem motivo: não muda o status
    client.post(f"/captacao-dou/{pub_id}/ignorar", data={"motivo": "", "csrf_token": token},
                follow_redirects=True)
    assert PublicacaoDouCapturada.query.get(pub_id).status == "pendente_revisao"

    resp = client.post(f"/captacao-dou/{pub_id}/ignorar", data={"motivo": "homônimo", "csrf_token": token},
                        follow_redirects=True)
    assert resp.status_code == 200
    pub = PublicacaoDouCapturada.query.get(pub_id)
    assert pub.status == "ignorada"
    assert pub.motivo_ignorada == "homônimo"


def test_acao_em_publicacao_ja_revisada_nao_muda_status(app, client, login, cenario):
    with app.app_context():
        pub_id = _criar_publicacao(cenario["unidade_id"])
        pub = PublicacaoDouCapturada.query.get(pub_id)
        pub.status = "lida"
        db.session.commit()

    login("admin@escritoriox.com")
    r = client.get("/captacao-dou/")
    token = _csrf(r.data.decode("utf-8"))
    client.post(f"/captacao-dou/{pub_id}/ignorar", data={"motivo": "tentativa tardia", "csrf_token": token},
                follow_redirects=True)
    assert PublicacaoDouCapturada.query.get(pub_id).status == "lida"


# ---------------------------------------------------------------------------
# Isolamento multi-tenant
# ---------------------------------------------------------------------------

def test_isolamento_multi_tenant_palavra_chave_e_publicacao(app, client, login):
    with app.app_context():
        empresa_a, unidade_a = _criar_empresa_unidade("EmpresaA", "UNA")
        empresa_b, unidade_b = _criar_empresa_unidade("EmpresaB", "UNB")

        admin_a = Usuario(email="admina@teste.com", unidade_id=unidade_a.id, papel="admin", nome="Admin A")
        admin_a.set_senha(SENHA)
        admin_b = Usuario(email="adminb@teste.com", unidade_id=unidade_b.id, papel="admin", nome="Admin B")
        admin_b.set_senha(SENHA)
        db.session.add_all([admin_a, admin_b])
        db.session.commit()

        palavra_b = PalavraChaveDou(unidade_id=unidade_b.id, termo="termo exclusivo b", ativa=True)
        db.session.add(palavra_b)
        db.session.commit()
        palavra_b_id = palavra_b.id

        pub_b = PublicacaoDouCapturada(
            unidade_id=unidade_b.id, id_materia_fonte="mat-b", secao="DO1",
            titulo="Publicação exclusiva de B", texto_trecho="trecho b",
            data_publicacao=date(2026, 9, 22), termo_encontrado="termo b", status="pendente_revisao",
        )
        db.session.add(pub_b)
        db.session.commit()
        pub_b_id = pub_b.id

    login("admina@teste.com")

    # admin A não vê palavra-chave nem publicação da empresa B na tela
    r = client.get("/captacao-dou/")
    assert b"termo exclusivo b" not in r.data
    assert b"Publicacao exclusiva de B" not in r.data and "Publicação exclusiva de B".encode("utf-8") not in r.data

    token = _csrf(r.data.decode("utf-8"))

    # admin A não pode alternar palavra-chave da empresa B
    resp = client.post(f"/captacao-dou/palavra-chave/{palavra_b_id}/alternar-ativo",
                        data={"csrf_token": token})
    assert resp.status_code == 403

    # admin A não pode marcar como lida nem ignorar publicação da empresa B
    resp2 = client.post(f"/captacao-dou/{pub_b_id}/marcar-lida", data={"csrf_token": token})
    assert resp2.status_code == 403
    resp3 = client.post(f"/captacao-dou/{pub_b_id}/ignorar", data={"motivo": "x", "csrf_token": token})
    assert resp3.status_code == 403

    # estado da empresa B continua intocado
    assert PalavraChaveDou.query.get(palavra_b_id).ativa is True
    assert PublicacaoDouCapturada.query.get(pub_b_id).status == "pendente_revisao"
