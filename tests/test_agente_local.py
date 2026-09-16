"""
Testa a infraestrutura do Agente Local (PENDENCIAS.md, seção -56):
pareamento (token pessoal, por usuário — mirror de TokenIntegracao, mas
por usuario_id, não por empresa_id), a fila de solicitações de busca de
autos completos, e a API que o agente local usa para autenticar, buscar
tarefas pendentes e devolver o resultado (PDF) ou um erro.

⚠️ O que este arquivo NÃO testa (fora de escopo desta suíte, de
propósito): nenhuma chamada de verdade a nenhum tribunal — isso mora
inteiramente em agente_local_jc/, roda na máquina do advogado, e precisa
de um piloto real com credencial/certificado de teste antes de confiar
no resultado. Aqui só se testa o lado SERVIDOR: pareamento, fila,
autenticação por token e o upload do resultado final.
"""
import io

import pytest

from app.extensions import db
from app.models import (
    AgenteLocalPareado, SolicitacaoBuscaAutos, Processo, Cliente, Documento,
)


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "advagente@teste.com", papel="advogado", nome="Advogado Agente")
    outro_adv_id = criar_usuario(unidade_id, "outroadv@teste.com", papel="advogado", nome="Outro Advogado")
    cliente = Cliente(nome="Cliente Agente Local", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(area_direito="Cível", unidade_id=unidade_id, cliente_id=cliente.id,
                         numero_processo="0001234-56.2024.8.26.0100")
    db.session.add(processo)
    db.session.commit()
    return dict(adv_id=adv_id, outro_adv_id=outro_adv_id, unidade_id=unidade_id,
                cliente_id=cliente.id, processo_id=processo.id)


# ---------------------- Modelo: pareamento ----------------------

def test_emitir_e_validar_token_de_pareamento(app, cenario):
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])

    registro, valor_puro = AgenteLocalPareado.emitir_para(usuario, "Notebook do escritório")
    db.session.commit()

    assert registro.usuario_id == usuario.id
    assert registro.ativo is True
    assert registro.token_hash != valor_puro  # nunca guarda o valor puro

    encontrado = AgenteLocalPareado.validar(valor_puro)
    assert encontrado is not None
    assert encontrado.id == registro.id


def test_validar_token_invalido_ou_revogado_devolve_none(app, cenario):
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])

    assert AgenteLocalPareado.validar("token-que-nunca-existiu") is None
    assert AgenteLocalPareado.validar(None) is None
    assert AgenteLocalPareado.validar("") is None

    registro, valor_puro = AgenteLocalPareado.emitir_para(usuario, "Notebook")
    db.session.commit()
    registro.revogar()
    db.session.commit()

    assert AgenteLocalPareado.validar(valor_puro) is None


# ---------------------- Tela "Meu agente local" ----------------------

def test_tela_meu_agente_parear_e_revogar(client, login, post_csrf, cenario):
    login("advagente@teste.com")

    r = post_csrf("/agente-local/parear", data={"apelido": "PC de casa"}, get_url="/agente-local")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "Copie agora" in corpo
    assert "PC de casa" in corpo

    registro = AgenteLocalPareado.query.filter_by(usuario_id=cenario["adv_id"]).first()
    assert registro is not None
    assert registro.ativo is True

    r2 = post_csrf(f"/agente-local/{registro.id}/revogar", get_url="/agente-local")
    assert r2.status_code == 200
    db.session.refresh(registro)
    assert registro.ativo is False


def test_botao_download_some_sem_url_configurada_e_aparece_com_ela(client, login, cenario, app):
    login("advagente@teste.com")
    r = client.get("/agente-local")
    assert r.status_code == 200
    assert "Baixar agente local" not in r.data.decode("utf-8")  # sem AGENTE_LOCAL_INSTALADOR_URL, sem botão

    app.config["AGENTE_LOCAL_INSTALADOR_URL"] = "https://github.com/exemplo/repo/releases/latest/download/JusControlAgente-Setup.exe"
    try:
        r2 = client.get("/agente-local")
        assert "Baixar agente local" in r2.data.decode("utf-8")
    finally:
        app.config["AGENTE_LOCAL_INSTALADOR_URL"] = ""


def test_baixar_instalador_redireciona_quando_configurado(client, login, cenario, app):
    login("advagente@teste.com")
    app.config["AGENTE_LOCAL_INSTALADOR_URL"] = "https://github.com/exemplo/repo/releases/latest/download/JusControlAgente-Setup.exe"
    try:
        r = client.get("/agente-local/baixar", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "https://github.com/exemplo/repo/releases/latest/download/JusControlAgente-Setup.exe"
    finally:
        app.config["AGENTE_LOCAL_INSTALADOR_URL"] = ""


def test_baixar_instalador_avisa_quando_nao_configurado(client, login, cenario, app):
    login("advagente@teste.com")
    r = client.get("/agente-local/baixar", follow_redirects=True)
    assert r.status_code == 200
    assert "ainda não está publicado" in r.data.decode("utf-8")


def test_nao_consegue_revogar_pareamento_de_outro_usuario(client, login, post_csrf, cenario):
    from app.models import Usuario
    outro = db.session.get(Usuario, cenario["outro_adv_id"])
    registro, _ = AgenteLocalPareado.emitir_para(outro, "Notebook do outro advogado")
    db.session.commit()

    login("advagente@teste.com")
    post_csrf(f"/agente-local/{registro.id}/revogar", get_url="/agente-local")
    db.session.refresh(registro)
    assert registro.ativo is True  # continua ativo — não pertence a quem tentou revogar


# ---------------------- Solicitar/cancelar busca (tela do processo) ----------------------

def test_solicitar_busca_autos_exige_agente_pareado(client, login, post_csrf, cenario):
    login("advagente@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/buscar-autos",
                   data={"tribunal_conector": "pje_mni"},
                   get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert SolicitacaoBuscaAutos.query.count() == 0
    assert "ainda não tem um Agente Local pareado" in r.data.decode("utf-8")


def test_solicitar_e_cancelar_busca_autos(client, login, post_csrf, cenario):
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])
    AgenteLocalPareado.emitir_para(usuario, "Notebook")
    db.session.commit()

    login("advagente@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/buscar-autos",
                   data={"tribunal_conector": "pje_mni"},
                   get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    pedido = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert pedido is not None
    assert pedido.status == "pendente"
    assert pedido.tribunal_conector == "pje_mni"
    assert pedido.numero_processo_solicitado == "0001234-56.2024.8.26.0100"
    assert pedido.solicitado_por_id == cenario["adv_id"]

    r2 = post_csrf(f"/processos/solicitacoes-busca-autos/{pedido.id}/cancelar",
                    get_url=f"/processos/{cenario['processo_id']}")
    assert r2.status_code == 200
    db.session.refresh(pedido)
    assert pedido.status == "cancelada"


def test_solicitar_busca_autos_com_projudi(client, login, post_csrf, cenario):
    """Projudi (PENDENCIAS.md, seção -59) — mesmo caminho do pje_mni,
    confirma que o conector novo já é aceito pela rota."""
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])
    AgenteLocalPareado.emitir_para(usuario, "Notebook")
    db.session.commit()

    login("advagente@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/buscar-autos",
                   data={"tribunal_conector": "projudi"},
                   get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    pedido = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert pedido is not None
    assert pedido.tribunal_conector == "projudi"


def test_solicitar_busca_autos_com_esaj(client, login, post_csrf, cenario):
    """e-SAJ (PENDENCIAS.md, seção -64) — mesmo caminho, confirma que o
    conector novo já é aceito pela rota."""
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])
    AgenteLocalPareado.emitir_para(usuario, "Notebook")
    db.session.commit()

    login("advagente@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/buscar-autos",
                   data={"tribunal_conector": "esaj_sp"},
                   get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    pedido = SolicitacaoBuscaAutos.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert pedido is not None
    assert pedido.tribunal_conector == "esaj_sp"


def test_solicitar_busca_com_conector_nao_implementado_eh_rejeitada(client, login, post_csrf, cenario):
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])
    AgenteLocalPareado.emitir_para(usuario, "Notebook")
    db.session.commit()

    login("advagente@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/buscar-autos",
                   data={"tribunal_conector": "eproc"},  # listado, mas ainda não implementado
                   get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert SolicitacaoBuscaAutos.query.count() == 0


# ---------------------- API do agente local (Bearer token) ----------------------

@pytest.fixture()
def agente_autenticado(app, cenario):
    """Cria um pareamento ativo e devolve (token_puro, agente_id, headers)."""
    from app.models import Usuario
    usuario = db.session.get(Usuario, cenario["adv_id"])
    registro, valor_puro = AgenteLocalPareado.emitir_para(usuario, "Notebook de teste")
    db.session.commit()
    return dict(token=valor_puro, agente_id=registro.id,
                headers={"Authorization": f"Bearer {valor_puro}"})


def test_api_exige_token_valido(client, cenario):
    r = client.get("/api/agente-local/tarefas")
    assert r.status_code == 401

    r2 = client.get("/api/agente-local/tarefas", headers={"Authorization": "Bearer token-invalido"})
    assert r2.status_code == 401


def test_api_ping_confirma_token(client, agente_autenticado):
    r = client.get("/api/agente-local/ping", headers=agente_autenticado["headers"])
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    registro = db.session.get(AgenteLocalPareado, agente_autenticado["agente_id"])
    assert registro.ultimo_contato_em is not None  # polling atualiza "visto por último"


def test_api_lista_so_tarefas_do_proprio_usuario(client, cenario, agente_autenticado):
    from app.models import Usuario
    outro = db.session.get(Usuario, cenario["outro_adv_id"])

    pedido_meu = SolicitacaoBuscaAutos(processo_id=cenario["processo_id"], tribunal_conector="pje_mni",
                                        solicitado_por_id=cenario["adv_id"])
    pedido_de_outro = SolicitacaoBuscaAutos(processo_id=cenario["processo_id"], tribunal_conector="pje_mni",
                                             solicitado_por_id=cenario["outro_adv_id"])
    db.session.add_all([pedido_meu, pedido_de_outro])
    db.session.commit()

    r = client.get("/api/agente-local/tarefas", headers=agente_autenticado["headers"])
    assert r.status_code == 200
    tarefas = r.get_json()["tarefas"]
    assert len(tarefas) == 1
    assert tarefas[0]["id"] == pedido_meu.id


def test_api_iniciar_enviar_resultado_e_criar_documento(client, cenario, agente_autenticado, app, monkeypatch):
    pedido = SolicitacaoBuscaAutos(processo_id=cenario["processo_id"], tribunal_conector="pje_mni",
                                    solicitado_por_id=cenario["adv_id"])
    db.session.add(pedido)
    db.session.commit()
    pedido_id = pedido.id

    # A entrega do resultado enfileira a indexação em segundo plano (item 3
    # — PENDENCIAS.md, seção -103) — sem Redis disponível no ambiente de
    # teste, mocka `enfileirar` (mesmo padrão de tests/test_pipeline_ia_juridica.py).
    import app.routes.agente_local_api as mod_agente_local_api
    chamadas_fila = []
    monkeypatch.setattr(mod_agente_local_api, "enfileirar",
                         lambda func_path, *args, **kwargs: chamadas_fila.append((func_path, args)))

    r_iniciar = client.post(f"/api/agente-local/tarefas/{pedido_id}/iniciar", headers=agente_autenticado["headers"])
    assert r_iniciar.status_code == 200
    db.session.refresh(pedido)
    assert pedido.status == "em_andamento"
    assert pedido.agente_local_id == agente_autenticado["agente_id"]

    pdf_falso = (io.BytesIO(b"%PDF-1.4 conteudo falso de teste"), "autos_completos.pdf")
    r_resultado = client.post(
        f"/api/agente-local/tarefas/{pedido_id}/resultado",
        headers=agente_autenticado["headers"],
        data={"arquivo": pdf_falso},
        content_type="multipart/form-data",
    )
    assert r_resultado.status_code == 200
    corpo = r_resultado.get_json()
    assert corpo["ok"] is True

    db.session.refresh(pedido)
    assert pedido.status == "concluida"
    assert pedido.documento_id == corpo["documento_id"]

    doc = db.session.get(Documento, corpo["documento_id"])
    assert doc is not None
    assert doc.processo_id == cenario["processo_id"]
    assert doc.categoria == "autos_completo_agente_local"


def test_api_reportar_erro(client, cenario, agente_autenticado):
    pedido = SolicitacaoBuscaAutos(processo_id=cenario["processo_id"], tribunal_conector="pje_mni",
                                    solicitado_por_id=cenario["adv_id"])
    db.session.add(pedido)
    db.session.commit()

    r = client.post(f"/api/agente-local/tarefas/{pedido.id}/erro", headers=agente_autenticado["headers"],
                     json={"mensagem": "Certificado expirado."})
    assert r.status_code == 200
    db.session.refresh(pedido)
    assert pedido.status == "erro"
    assert pedido.mensagem_erro == "Certificado expirado."


def test_api_nao_deixa_agente_acessar_tarefa_de_outro_usuario(client, cenario, agente_autenticado):
    pedido_de_outro = SolicitacaoBuscaAutos(processo_id=cenario["processo_id"], tribunal_conector="pje_mni",
                                             solicitado_por_id=cenario["outro_adv_id"])
    db.session.add(pedido_de_outro)
    db.session.commit()

    r = client.post(f"/api/agente-local/tarefas/{pedido_de_outro.id}/iniciar", headers=agente_autenticado["headers"])
    assert r.status_code == 404
