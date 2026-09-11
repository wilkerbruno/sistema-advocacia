"""
Testes dos links de conveniência pro Projudi (PENDENCIAS.md, seção -85) —
mesma disciplina de tests/test_eproc_links.py: NÃO testam captura
automática (não existe), só que os links/URLs são montados certo e que a
rota de auto-envio do TJPR (formulário POST, campos reais confirmados ao
vivo — ver app/utils/projudi_links.py) renderiza o <form> oculto com os
campos esperados.
"""
import pytest

from app.extensions import db
from app.models import Processo
from app.utils.projudi_links import links_projudi_estadual, formulario_projudi


NUM_ESTADUAL_TJPR = "1234567-89.2023.8.16.0001"  # segmento "8"
NUM_FEDERAL_TRF4 = "1234567-89.2023.4.04.7000"   # segmento "4"


def test_links_projudi_para_numero_federal_fica_vazio():
    assert links_projudi_estadual(NUM_FEDERAL_TRF4) == []


def test_links_projudi_traz_tjpr_e_tjam():
    links = links_projudi_estadual(NUM_ESTADUAL_TJPR)
    rotulos = [l["rotulo"] for l in links]
    assert any("TJPR" in r for r in rotulos)
    assert any("TJAM" in r for r in rotulos)
    tipos = {l["rotulo"]: l["tipo"] for l in links}
    # TJPR é ponte de auto-envio (Comarca/Juízo não exigidos pra busca por
    # número — confirmado ao vivo); TJAM é link solto (firewall bloqueou
    # uma tentativa de busca de teste ao vivo, ver seção -84)
    assert [t for r, t in tipos.items() if "TJPR" in r][0] == "bridge"
    assert [t for r, t in tipos.items() if "TJAM" in r][0] == "link"
    # TJGO não aparece (bloqueou já no carregamento simples da página)
    assert not any("TJGO" in r for r in rotulos)


def test_formulario_projudi_slug_desconhecido_devolve_none():
    assert formulario_projudi("tjam") is None
    assert formulario_projudi("tjgo") is None


def test_formulario_projudi_tjpr_tem_action_e_campo_numero():
    formulario = formulario_projudi("tjpr")
    assert formulario is not None
    assert "consulta.tjpr.jus.br" in formulario["action_base"]
    assert formulario["campo_numero"] == "numeroProcesso"
    assert formulario["metodo"] == "post"
    assert formulario["campos_fixos"]["codComarca"] == "-1"
    assert formulario["campos_fixos"]["flagNumeroUnico"] == "true"


# ---------- Rota de auto-envio ----------

@pytest.fixture()
def processo_projudi(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin-projudi@teste.com", papel="admin", nome="Admin Projudi")
    from app.models import Cliente
    cliente = Cliente(nome="Cliente Teste Projudi", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo=NUM_ESTADUAL_TJPR, cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Cível", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "admin_id": admin_id}


def test_rota_abrir_projudi_tjpr_renderiza_form_oculto(client, login, processo_projudi):
    login("admin-projudi@teste.com")
    r = client.get(f"/governanca/processos/{processo_projudi['processo_id']}/abrir-projudi/tjpr")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'method="post"' in html
    assert 'action="https://consulta.tjpr.jus.br/projudi_consulta/processo/consultaPublica.do?actionType=pesquisar"' in html
    assert 'name="numeroProcesso" value="1234567-89.2023.8.16.0001"' in html
    assert 'name="codComarca" value="-1"' in html
    assert "document.getElementById('formConsultaPublicaAutoEnvio').submit()" in html


def test_rota_abrir_projudi_slug_desconhecido_404(client, login, processo_projudi):
    login("admin-projudi@teste.com")
    r = client.get(f"/governanca/processos/{processo_projudi['processo_id']}/abrir-projudi/tjam")
    assert r.status_code == 404


def test_rota_abrir_projudi_processo_sem_numero_404(client, login, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    criar_usuario(unidade_id, "admin-projudi2@teste.com", papel="admin", nome="Admin Projudi 2")
    from app.models import Cliente
    cliente = Cliente(nome="Cliente Sem Numero", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo=None, cliente_id=cliente.id, unidade_id=unidade_id,
        area_direito="Cível", status="ativo", monitoravel=False,
    )
    db.session.add(processo)
    db.session.commit()

    login("admin-projudi2@teste.com")
    r = client.get(f"/governanca/processos/{processo.id}/abrir-projudi/tjpr")
    assert r.status_code == 404
