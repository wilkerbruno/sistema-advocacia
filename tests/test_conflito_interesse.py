"""
Testa a detecção de conflito de interesses: quando a parte contrária de
um processo já é cliente do mesmo escritório (mesma empresa, cruzando
unidade), o sistema avisa no cadastro, no detalhe do cliente, no detalhe
do processo e numa tela dedicada de verificação — sem nunca cruzar
fronteira de empresa e sem falso positivo pro processo do próprio
cliente ou pra cliente sem relação nenhuma.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Empresa, Unidade, Usuario, Cliente, Processo, Licenca


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    empresa_id = empresa_basica["empresa_id"]
    unidade_a_id = empresa_basica["unidade_id"]

    # Segunda unidade da MESMA empresa, pra confirmar que o conflito é
    # visto através de unidades (não só dentro da mesma unidade).
    unidade_b = Unidade(nome="Unidade B", codigo="UB", empresa_id=empresa_id)
    db.session.add(unidade_b)
    db.session.flush()

    # Uma empresa DIFERENTE, pra confirmar que o conflito NUNCA cruza empresa.
    empresa_outra = Empresa(nome="Outro Escritorio")
    db.session.add(empresa_outra)
    db.session.flush()
    db.session.add(Licenca(empresa_id=empresa_outra.id, plano="mensal", valor_negociado=100, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade_outra = Unidade(nome="Unidade Outra Empresa", codigo="UX", empresa_id=empresa_outra.id)
    db.session.add(unidade_outra)
    db.session.flush()

    admin_a_id = criar_usuario(unidade_a_id, "admina@teste.com", papel="admin", nome="Admin A")
    func_b_id = criar_usuario(unidade_b.id, "funcb@teste.com", papel="funcionario", nome="Funcionario B")
    admin_outra_id = criar_usuario(unidade_outra.id, "adminoutra@teste.com", papel="admin", nome="Admin Outra Empresa")

    # Cliente "Industria XPTO Ltda" na unidade A.
    cliente_industria = Cliente(nome="Industria XPTO Ltda", unidade_id=unidade_a_id)
    db.session.add(cliente_industria)
    db.session.flush()

    # Um outro cliente na unidade B tem um processo ONDE a parte contrária
    # é "Industria XPTO Ltda" (com acento e caixa diferentes de propósito,
    # pra testar a normalização). Isso deveria ser detectado como conflito.
    cliente_b = Cliente(nome="Pessoa Fulana", unidade_id=unidade_b.id)
    db.session.add(cliente_b)
    db.session.flush()
    processo_conflitante = Processo(numero_processo="0000050-11.2026.8.26.0100", cliente_id=cliente_b.id,
                                     unidade_id=unidade_b.id, area_direito="Cível",
                                     parte_contraria="INDÚSTRIA xpto ltda",
                                     responsavel_id=func_b_id, criado_por_id=func_b_id)
    db.session.add(processo_conflitante)
    db.session.flush()

    # Um processo do PRÓPRIO cliente_industria com parte_contraria igual ao
    # SEU PRÓPRIO nome não deveria ser flagado.
    processo_proprio = Processo(numero_processo="0000051-11.2026.8.26.0100", cliente_id=cliente_industria.id,
                                 unidade_id=unidade_a_id, area_direito="Cível",
                                 parte_contraria="Industria XPTO Ltda",
                                 responsavel_id=admin_a_id, criado_por_id=admin_a_id)
    db.session.add(processo_proprio)
    db.session.flush()

    # Um cliente/processo SEM NENHUMA relação, pra garantir que não aparece
    # como falso positivo.
    cliente_sem_relacao = Cliente(nome="Sem Relacao Nenhuma", unidade_id=unidade_a_id)
    db.session.add(cliente_sem_relacao)
    db.session.flush()

    # Empresa DIFERENTE tem cliente com o MESMO nome "Industria XPTO Ltda" e
    # um processo com parte_contraria batendo — NÃO deveria aparecer no
    # conflito da primeira empresa (nunca cruza fronteira de empresa).
    cliente_outra_empresa = Cliente(nome="Industria XPTO Ltda", unidade_id=unidade_outra.id)
    db.session.add(cliente_outra_empresa)
    db.session.flush()
    outro_cliente_2 = Cliente(nome="Terceiro Empresa Outra", unidade_id=unidade_outra.id)
    db.session.add(outro_cliente_2)
    db.session.flush()
    processo_outra_empresa = Processo(numero_processo="0000052-11.2026.8.26.0100", cliente_id=outro_cliente_2.id,
                                       unidade_id=unidade_outra.id, area_direito="Cível",
                                       parte_contraria="Industria XPTO Ltda",
                                       responsavel_id=admin_outra_id, criado_por_id=admin_outra_id)
    db.session.add(processo_outra_empresa)
    db.session.commit()

    return dict(
        cliente_industria_id=cliente_industria.id, processo_conflitante_id=processo_conflitante.id,
        unidade_a_id=unidade_a_id, cliente_sem_relacao_id=cliente_sem_relacao.id, cliente_b_id=cliente_b.id,
    )


def test_banner_no_detalhe_do_cliente(client, login, cenario):
    login("admina@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_industria_id']}")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Possível conflito de interesses" in html, "banner de conflito não apareceu no detalhe do cliente"
    inicio = html.find("Possível conflito de interesses")
    fim = html.find("</div>", inicio)
    trecho = html[inicio:fim]
    assert "0000050-11.2026.8.26.0100" in trecho, "processo conflitante não foi listado no banner"
    assert "0000051-11.2026.8.26.0100" not in trecho, \
        "processo do PRÓPRIO cliente não deveria contar como conflito NO BANNER"


def test_banner_no_detalhe_do_processo_conflitante(client, login, cenario):
    login("admina@teste.com")
    r = client.get(f"/processos/{cenario['processo_conflitante_id']}")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Possível conflito de interesses" in html, "banner de conflito não apareceu no detalhe do processo"
    assert "Industria XPTO Ltda" in html


def test_tela_dedicada_de_verificacao(client, login, cenario):
    login("admina@teste.com")
    r = client.get("/governanca/conflitos")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "Industria XPTO Ltda" in html
    assert "0000050-11.2026.8.26.0100" in html
    assert "Terceiro Empresa Outra" not in html, "vazou dado de OUTRA empresa na verificação de conflitos"


def test_usuario_nao_admin_bloqueado_na_tela_dedicada(client, login, cenario):
    login("funcb@teste.com")
    r = client.get("/governanca/conflitos")
    assert r.status_code == 403


def test_sem_falso_positivo_para_cliente_sem_relacao(client, login, cenario):
    login("admina@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_sem_relacao_id']}")
    assert "Possível conflito de interesses" not in r.data.decode("utf-8")


def test_flash_de_aviso_no_cadastro_de_novo_processo(client, login, post_csrf, cenario):
    login("admina@teste.com")
    r = post_csrf("/processos/novo", {
        "area_direito": "Cível", "cliente_id": str(cenario["cliente_b_id"]),
        "parte_contraria": "industria xpto ltda", "unidade_id": str(cenario["unidade_a_id"]),
    })
    assert r.status_code == 200
    assert "Possível conflito de interesses" in r.data.decode("utf-8"), \
        "flash de conflito não apareceu ao cadastrar processo com parte contrária = cliente existente"
