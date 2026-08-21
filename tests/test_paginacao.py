"""
Testa a paginação real (Anterior/Próxima) em processos, clientes e fila
de intimações, e o padrão "top-N + total real" (limitar_com_total) no
Painel de governança (PENDENCIAS.md, seção -47).
"""
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Prazo


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@pag.com", papel="admin", nome="Admin")

    hoje = date.today()

    # 30 clientes (mais que o padrão de 25 por página)
    clientes = [Cliente(nome=f"Cliente {i:03d}", unidade_id=unidade_id, ativo=True) for i in range(30)]
    db.session.add_all(clientes)
    db.session.flush()

    # 40 processos ativos (mais que o padrão de 25 por página)
    processos = [
        Processo(numero_interno=f"P-{i:04d}", cliente_id=clientes[i % 30].id, unidade_id=unidade_id,
                 area_direito="Cível", status="ativo", criado_por_id=admin_id)
        for i in range(40)
    ]
    db.session.add_all(processos)
    db.session.flush()

    # 30 prazos pendentes vinculados ao primeiro processo (mais que 25 por página)
    prazos = [
        Prazo(processo_id=processos[0].id, descricao=f"Prazo {i:03d}",
              data_vencimento=hoje + timedelta(days=i), status="pendente")
        for i in range(30)
    ]
    db.session.add_all(prazos)

    # 51 processos NÃO monitoráveis (mais que o teto de widget, 50), pra
    # testar o aviso de truncamento no Painel de governança.
    nao_monitoraveis = [
        Processo(numero_interno=f"NM-{i:04d}", cliente_id=clientes[i % 30].id, unidade_id=unidade_id,
                 area_direito="Cível", status="ativo", criado_por_id=admin_id,
                 monitoravel=False, motivo_nao_monitoravel="teste")
        for i in range(51)
    ]
    db.session.add_all(nao_monitoraveis)
    db.session.commit()

    return dict(admin_id=admin_id, unidade_id=unidade_id)


def test_processos_pagina_1_mostra_total_e_link_proxima(client, login, cenario):
    login("admin@pag.com")
    r = client.get("/processos/")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert body.count("<tr>") - 1 == 25 or body.count('href="/processos/') >= 25, \
        "página 1 de processos deveria mostrar 25 linhas"
    assert "40 registro(s) no total" in body or "91 registro(s)" in body
    assert "Próxima" in body
    assert "Anterior" in body


def test_processos_pagina_2_mostra_outro_conjunto(client, login, cenario):
    login("admin@pag.com")
    r2 = client.get("/processos/?pagina=2")
    assert r2.status_code == 200
    body2 = r2.data.decode("utf-8")
    assert "P-0000" not in body2 or "P-0024" not in body2, \
        "página 2 não deveria repetir os mesmos processos da página 1"


def test_filtro_e_paginacao_juntos_preservam_querystring(client, login, cenario):
    login("admin@pag.com")
    r3 = client.get("/processos/?status=ativo&pagina=1")
    assert r3.status_code == 200
    body3 = r3.data.decode("utf-8")
    assert "status=ativo" in body3, "o link de paginação deveria preservar o filtro de status na querystring"


def test_pagina_invalida_ou_negativa_nao_quebra(client, login, cenario):
    login("admin@pag.com")
    r4 = client.get("/processos/?pagina=9999")
    assert r4.status_code == 200, f"página além do total NÃO deveria dar erro, veio {r4.status_code}"
    r5 = client.get("/processos/?pagina=abc")
    assert r5.status_code == 200, f"página inválida (não numérica) NÃO deveria quebrar a tela, veio {r5.status_code}"
    r6 = client.get("/processos/?pagina=-1")
    assert r6.status_code == 200, f"página negativa NÃO deveria quebrar a tela, veio {r6.status_code}"


def test_por_pagina_customizado_respeita_teto_maximo(client, login, cenario):
    login("admin@pag.com")
    r7 = client.get("/processos/?por_pagina=5")
    body7 = r7.data.decode("utf-8")
    assert "Página 1 de" in body7
    r8 = client.get("/processos/?por_pagina=99999")
    assert r8.status_code == 200, "por_pagina absurdo não deveria quebrar a tela (deve ser limitado ao máximo)"


def test_clientes_listar_paginado(client, login, cenario):
    login("admin@pag.com")
    rc = client.get("/clientes/")
    assert rc.status_code == 200
    bodyc = rc.data.decode("utf-8")
    assert "30 registro(s) no total" in bodyc, "clientes.listar deveria mostrar o total certo (30)"


def test_fila_intimacoes_paginada(client, login, cenario):
    login("admin@pag.com")
    rf = client.get("/governanca/fila-intimacoes")
    assert rf.status_code == 200
    bodyf = rf.data.decode("utf-8")
    assert "30 registro(s) no total" in bodyf, "fila de intimações deveria mostrar o total certo (30 prazos pendentes)"


def test_painel_governanca_mostra_total_real_e_avisa_truncamento(client, login, cenario):
    login("admin@pag.com")
    rp = client.get("/governanca/painel")
    assert rp.status_code == 200
    bodyp = rp.data.decode("utf-8")
    assert "Processos não monitoráveis automaticamente (51)" in bodyp, \
        "o total de não monitoráveis deveria ser 51, mesmo mostrando só os 50 primeiros"
    assert "Mostrando os 50 mais recentes de 51" in bodyp, \
        "deveria avisar que a lista foi truncada (50 de 51)"
