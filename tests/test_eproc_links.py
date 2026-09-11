"""
Testes dos links de conveniência pro eproc (PENDENCIAS.md, seção -81) —
NÃO testam captura automática (não existe): só que os links/URLs são
montados certo e que a rota de auto-envio (formulário POST) renderiza o
<form> oculto com os campos esperados.
"""
import pytest

from app.extensions import db
from app.models import Processo
from app.utils.eproc_links import links_eproc_federal, links_eproc_estadual, formulario_post


NUM_ESTADUAL_TJRS = "1234567-89.2023.8.21.0001"  # segmento "8"
NUM_FEDERAL_TRF4 = "1234567-89.2023.4.04.7000"   # segmento "4"


def test_links_federal_para_numero_estadual_fica_vazio():
    assert links_eproc_federal(NUM_ESTADUAL_TJRS) == []


def test_links_estadual_para_numero_federal_fica_vazio():
    assert links_eproc_estadual(NUM_FEDERAL_TRF4) == []


def test_links_federal_traz_trf4_e_as_tres_secoes_judiciarias():
    links = links_eproc_federal(NUM_FEDERAL_TRF4)
    rotulos = [l["rotulo"] for l in links]
    assert any("TRF4" in r for r in rotulos)
    assert any("JFRS" in r for r in rotulos)
    assert any("JFSC" in r for r in rotulos)
    assert any("JFPR" in r for r in rotulos)
    assert len(links) == 4
    assert all(l["tipo"] == "link" for l in links)
    # o número formatado (com pontuação) precisa estar na URL, não só os dígitos
    assert all("1234567-89.2023.4.04.7000" in l["url"] for l in links)
    assert all(l["url"].startswith("https://consulta.trf4.jus.br/trf4/controlador.php") for l in links)


def test_links_estadual_traz_tjrs_tjsc_tjrj():
    links = links_eproc_estadual(NUM_ESTADUAL_TJRS)
    rotulos = [l["rotulo"] for l in links]
    assert any("TJRS" in r for r in rotulos)
    assert any("TJSC" in r for r in rotulos)
    assert any("TJRJ" in r for r in rotulos)
    # TJRS é link direto (SPA, sem prefill); TJSC/TJRJ passam pela rota de auto-envio
    tipos = {l["rotulo"]: l["tipo"] for l in links}
    assert [t for r, t in tipos.items() if "TJRS" in r][0] == "link"
    slugs_post = {l["slug"] for l in links if l["tipo"] == "post"}
    assert slugs_post == {"tjsc", "tjrj"}


def test_formulario_post_slug_desconhecido_devolve_none():
    assert formulario_post("tjpr") is None


def test_formulario_post_tjrj_tem_action_e_campo_numero():
    formulario = formulario_post("tjrj")
    assert formulario is not None
    assert "eproc1g-cp.tjrj.jus.br" in formulario["action"]
    assert formulario["campo_numero"] == "txtNumProcesso"
    assert "cf-turnstile-response" in formulario["campos_fixos"]


# ---------- Rota de auto-envio ----------

@pytest.fixture()
def processo_eproc(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin-eproc@teste.com", papel="admin", nome="Admin Eproc")
    from app.models import Cliente
    cliente = Cliente(nome="Cliente Teste Eproc", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo=NUM_ESTADUAL_TJRS, cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Cível", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "admin_id": admin_id}


def test_rota_abrir_eproc_tjrj_renderiza_form_oculto(client, login, processo_eproc):
    login("admin-eproc@teste.com")
    r = client.get(f"/governanca/processos/{processo_eproc['processo_id']}/abrir-eproc/tjrj")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'action="https://eproc1g-cp.tjrj.jus.br' in html
    assert 'name="txtNumProcesso" value="1234567-89.2023.8.21.0001"' in html
    assert "document.getElementById('formEprocAutoEnvio').submit()" in html


def test_rota_abrir_eproc_slug_desconhecido_404(client, login, processo_eproc):
    login("admin-eproc@teste.com")
    r = client.get(f"/governanca/processos/{processo_eproc['processo_id']}/abrir-eproc/tjpr")
    assert r.status_code == 404


def test_rota_abrir_eproc_processo_sem_numero_404(client, login, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    criar_usuario(unidade_id, "admin-eproc2@teste.com", papel="admin", nome="Admin Eproc 2")
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

    login("admin-eproc2@teste.com")
    r = client.get(f"/governanca/processos/{processo.id}/abrir-eproc/tjrj")
    assert r.status_code == 404
