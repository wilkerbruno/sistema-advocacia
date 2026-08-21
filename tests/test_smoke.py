def test_empresa_basica_fixture_funciona(app, empresa_basica):
    assert empresa_basica["empresa_id"] > 0
    assert empresa_basica["unidade_id"] > 0


def test_login_fixture_funciona(app, client, login, empresa_basica, criar_usuario):
    from app.extensions import db
    criar_usuario(empresa_basica["unidade_id"], "smoke@teste.com", papel="admin")
    db.session.commit()
    r = login("smoke@teste.com")
    assert r.status_code == 200
    r2 = client.get("/")
    assert r2.status_code == 200


def test_isolamento_entre_testes(app, empresa_basica):
    # se o teste anterior tivesse vazado dado, "smoke@teste.com" ainda
    # existiria aqui - confirma que cada teste comeca de um banco vazio
    from app.models import Usuario
    assert Usuario.query.count() == 0
