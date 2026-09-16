"""
Item 1 da lista de pipeline de IA jurídica (PENDENCIAS.md, seção -102):
"Captura da intimação DJEN e API Comunica do CNJ por OAB do escritório,
mais push do tribunal onde houver. O input humano do onboarding é a OAB,
não o processo."

Cobertura:
  1. ConectorDJEN.monitorar_publicacoes_por_oab: validação de parâmetro e
     os caminhos de sucesso/erro HTTP, com `requests.get` mockado (nunca
     bate na API real do CNJ nos testes).
  2. app/utils/captura_djen_pipeline.py: decisão auto-vínculo (1 candidato)
     vs. fila de triagem (0 ou 2+ candidatos), dedup por
     `id_comunicacao_fonte`, e idempotência de `vincular_intimacao_a_processo`.
  3. Rotas de app/routes/captacao_oab.py: cadastro de OAB, alternância de
     ativo/inativo, listagem de triagem, vincular, ignorar, e o webhook de
     push (token válido/inválido, payload sem identificador, duplicata).
  4. Isolamento multi-tenant: OAB e intimação de uma empresa nunca visível/
     ligável por outra (mesmo padrão de test_isolamento_multi_tenant_prazos.py).
  5. Regressão: `aplicar_regra_proxima_acao` (Movimentacao) continua
     funcionando exatamente como antes do refatoramento que extraiu
     `_encontrar_regra`/`_montar_prazo` como internos compartilhados com o
     novo `aplicar_regra_a_publicacao`.
"""
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.extensions import db
from app.models import (
    Empresa, Licenca, Unidade, Usuario, Cliente, Processo, Publicacao,
    Movimentacao, RegraProximaAcao, OabMonitorada, IntimacaoCapturada,
)
from app.utils import conector_djen as mod
from app.utils.conector_djen import ConectorDJEN, ConexaoComunicaError
from app.utils.captura_djen_pipeline import processar_publicacao_capturada, vincular_intimacao_a_processo
from app.utils.captura_conectores import PublicacaoCapturada, obter_conector
from app.utils.prazos_engine import aplicar_regra_proxima_acao, aplicar_regra_a_publicacao

SENHA = "senha123"


def _resp(json_body=None, status=200, texto="", raise_json_error=False):
    r = MagicMock()
    r.status_code = status
    r.text = texto
    if raise_json_error:
        r.json.side_effect = ValueError("boom")
    else:
        r.json.return_value = json_body or {}
    return r


# ---------------------------------------------------------------------------
# 1. ConectorDJEN
# ---------------------------------------------------------------------------

def test_monitorar_publicacoes_valida_oab_invalida():
    conector = ConectorDJEN()
    with pytest.raises(ValueError):
        conector.monitorar_publicacoes_por_oab("", "SP")


def test_monitorar_publicacoes_valida_uf_invalida():
    conector = ConectorDJEN()
    with pytest.raises(ValueError):
        conector.monitorar_publicacoes_por_oab("123456", "S")


def test_monitorar_publicacoes_sucesso_mapeia_campos_camel_e_snake(app):
    """Um item em camelCase e outro em snake_case — a API real é
    documentadamente inconsistente entre os dois (ver docstring do módulo)."""
    corpo = {
        "count": 2,
        "items": [
            {
                "id": "111",
                "data_disponibilizacao": "2026-09-10",
                "texto": "Intimação para manifestação em 15 dias.",
                "numero_processo": "1234567-89.2026.8.26.0001",
                "siglaTribunal": "TJSP",
                "tipoComunicacao": "Intimação",
            },
            {
                "id": "222",
                "dataDisponibilizacao": "2026-09-11",
                "texto": "Citação.",
                "numeroProcesso": "9876543-21.2026.8.26.0002",
                "tribunal": "TJSP",
                "tipo_comunicacao": "Citação",
            },
        ],
    }
    with patch.object(mod.requests, "get", return_value=_resp(corpo)) as get_mock:
        conector = ConectorDJEN()
        with app.app_context():
            publicacoes = conector.monitorar_publicacoes_por_oab("123456", "sp", data_inicio=date(2026, 9, 1),
                                                                   data_fim=date(2026, 9, 12))

    assert get_mock.called
    assert len(publicacoes) == 2
    assert publicacoes[0].id_comunicacao_fonte == "111"
    assert publicacoes[0].numero_processo == "12345678920268260001"
    assert publicacoes[0].tribunal == "TJSP"
    assert publicacoes[0].data_disponibilizacao == date(2026, 9, 10)
    # Lei 11.419/2006, art 4º §3º: publicação = 1º dia útil seguinte (2026-09-10 é quinta, então sexta 09-11)
    assert publicacoes[0].data_publicacao == date(2026, 9, 11)
    assert publicacoes[1].id_comunicacao_fonte == "222"
    assert publicacoes[1].numero_processo == "98765432120268260002"


def test_monitorar_publicacoes_403_vira_erro_geobloqueio(app):
    with patch.object(mod.requests, "get", return_value=_resp(status=403, texto="forbidden")):
        conector = ConectorDJEN()
        with app.app_context(), pytest.raises(ConexaoComunicaError, match="geobloqueio"):
            conector.monitorar_publicacoes_por_oab("123456", "SP")


def test_monitorar_publicacoes_http_erro_generico(app):
    with patch.object(mod.requests, "get", return_value=_resp(status=500, texto="boom")):
        conector = ConectorDJEN()
        with app.app_context(), pytest.raises(ConexaoComunicaError, match="500"):
            conector.monitorar_publicacoes_por_oab("123456", "SP")


def test_monitorar_publicacoes_json_invalido(app):
    with patch.object(mod.requests, "get", return_value=_resp(raise_json_error=True)):
        conector = ConectorDJEN()
        with app.app_context(), pytest.raises(ConexaoComunicaError, match="JSON válido"):
            conector.monitorar_publicacoes_por_oab("123456", "SP")


def test_monitorar_publicacoes_falha_de_rede(app):
    with patch.object(mod.requests, "get", side_effect=requests.ConnectionError("sem rede")):
        conector = ConectorDJEN()
        with app.app_context(), pytest.raises(ConexaoComunicaError, match="Falha de conexão"):
            conector.monitorar_publicacoes_por_oab("123456", "SP")


def test_obter_conector_djen_retorna_instancia_correta(app):
    with app.app_context():
        conector = obter_conector("djen")
    assert isinstance(conector, ConectorDJEN)


# ---------------------------------------------------------------------------
# Fixtures de cenário (empresa/unidade/OAB/processo)
# ---------------------------------------------------------------------------

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
    db.session.flush()

    cliente = Cliente(nome="Cliente Teste", unidade_id=unidade.id)
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(
        area_direito="Cível", unidade_id=unidade.id, cliente_id=cliente.id,
        numero_processo="1234567-89.2026.8.26.0001", status="ativo", responsavel_id=admin.id,
    )
    db.session.add(processo)
    db.session.flush()

    oab = OabMonitorada(unidade_id=unidade.id, numero="123456", uf="SP", criado_por_id=admin.id,
                         token_webhook="tok-abc-123")
    db.session.add(oab)
    db.session.commit()

    return dict(empresa=empresa, unidade=unidade, admin_id=admin.id, cliente_id=cliente.id,
                processo_id=processo.id, oab_id=oab.id)


def _publicacao_capturada(numero_processo="12345678920268260001", id_fonte="fonte-1", texto="Publicação teste"):
    return PublicacaoCapturada(
        diario="DJEN", data_disponibilizacao=date(2026, 9, 10), data_publicacao=date(2026, 9, 11),
        teor=texto, oab_destinataria="123456/SP", hash_dedup=id_fonte,
        numero_processo=numero_processo, tribunal="TJSP", tipo_comunicacao="Intimação",
        id_comunicacao_fonte=id_fonte,
    )


# ---------------------------------------------------------------------------
# 2. Pipeline: auto-vínculo vs. triagem, dedup, idempotência
# ---------------------------------------------------------------------------

def test_processar_publicacao_com_um_candidato_vincula_automaticamente(app, cenario):
    with app.app_context():
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        pub = _publicacao_capturada()
        intimacao = processar_publicacao_capturada(oab, pub)
        db.session.commit()

        assert intimacao is not None
        assert intimacao.status == "vinculada"
        assert intimacao.processo_id == cenario["processo_id"]
        assert intimacao.publicacao_id is not None
        assert intimacao.vinculado_por_id is None  # vínculo automático, não humano

        publicacao = db.session.get(Publicacao, intimacao.publicacao_id)
        assert publicacao.hash_dedup == "fonte-1"
        assert publicacao.processo_id == cenario["processo_id"]
        assert intimacao.prazo_id is not None  # regra genérica sempre gera prazo (seção 7.1)


def test_processar_publicacao_sem_candidato_vai_para_triagem(app, cenario):
    with app.app_context():
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        pub = _publicacao_capturada(numero_processo="00000000000000000000", id_fonte="fonte-2")
        intimacao = processar_publicacao_capturada(oab, pub)
        db.session.commit()

        assert intimacao.status == "pendente_triagem"
        assert intimacao.processo_id is None


def test_processar_publicacao_com_dois_candidatos_vai_para_triagem(app, cenario):
    with app.app_context():
        unidade_id = cenario["unidade"].id if hasattr(cenario["unidade"], "id") else None
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        # segundo processo com o MESMO número (caso raro, mas possível: número truncado/duplicado)
        outro = Processo(area_direito="Cível", unidade_id=oab.unidade_id, cliente_id=cenario["cliente_id"],
                          numero_processo="1234567-89.2026.8.26.0001", status="ativo")
        db.session.add(outro)
        db.session.commit()

        pub = _publicacao_capturada(id_fonte="fonte-3")
        intimacao = processar_publicacao_capturada(oab, pub)
        db.session.commit()

        assert intimacao.status == "pendente_triagem"


def test_processar_publicacao_deduplica_por_id_comunicacao_fonte(app, cenario):
    with app.app_context():
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        pub = _publicacao_capturada(id_fonte="fonte-dup")
        primeira = processar_publicacao_capturada(oab, pub)
        db.session.commit()
        assert primeira is not None

        segunda = processar_publicacao_capturada(oab, _publicacao_capturada(id_fonte="fonte-dup"))
        assert segunda is None
        assert IntimacaoCapturada.query.filter_by(id_comunicacao_fonte="fonte-dup").count() == 1


def test_vincular_intimacao_a_processo_e_idempotente_por_hash_dedup(app, cenario):
    with app.app_context():
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        pub = _publicacao_capturada(numero_processo="00000000000000000001", id_fonte="fonte-4")
        intimacao = processar_publicacao_capturada(oab, pub)
        db.session.commit()
        assert intimacao.status == "pendente_triagem"

        processo = db.session.get(Processo, cenario["processo_id"])
        admin = db.session.get(Usuario, cenario["admin_id"])
        vincular_intimacao_a_processo(intimacao, processo, usuario=admin)
        db.session.commit()
        assert intimacao.status == "vinculada"
        assert intimacao.vinculado_por_id == admin.id
        primeira_publicacao_id = intimacao.publicacao_id

        # chamar de novo (mesmo hash_dedup) não deve criar uma segunda Publicacao
        vincular_intimacao_a_processo(intimacao, processo, usuario=admin)
        db.session.commit()
        assert intimacao.publicacao_id == primeira_publicacao_id
        assert Publicacao.query.filter_by(hash_dedup="fonte-4").count() == 1


# ---------------------------------------------------------------------------
# 3. Rotas
# ---------------------------------------------------------------------------

def test_cadastrar_oab_via_rota(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-oab/")
    token = None
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode("utf-8"))
    token = m.group(1)
    resp = client.post("/captacao-oab/nova", data={
        "numero": "654321", "uf": "RJ", "nome_advogado": "Fulano", "csrf_token": token,
        "unidade_id": cenario["unidade"].id,
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert OabMonitorada.query.filter_by(numero="654321", uf="RJ").first() is not None


def test_alternar_ativo_oab(client, login, cenario):
    login("admin@escritoriox.com")
    r = client.get("/captacao-oab/")
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode("utf-8")).group(1)
    resp = client.post(f"/captacao-oab/{cenario['oab_id']}/alternar-ativo", data={"csrf_token": token},
                        follow_redirects=True)
    assert resp.status_code == 200
    oab = OabMonitorada.query.get(cenario["oab_id"])
    assert oab.ativo is False


def test_triagem_lista_e_vincula(app, client, login, cenario):
    with app.app_context():
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        pub = _publicacao_capturada(numero_processo="00000000000000000009", id_fonte="fonte-rota")
        intimacao = processar_publicacao_capturada(oab, pub)
        db.session.commit()
        intimacao_id = intimacao.id

    login("admin@escritoriox.com")
    r = client.get("/captacao-oab/triagem")
    assert r.status_code == 200
    assert b"fonte-rota" not in r.data  # id interno nao aparece, mas a pagina carrega

    r2 = client.get(f"/captacao-oab/triagem/{intimacao_id}")
    assert r2.status_code == 200

    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', r2.data.decode("utf-8")).group(1)
    resp = client.post(f"/captacao-oab/triagem/{intimacao_id}/vincular",
                        data={"processo_id": cenario["processo_id"], "csrf_token": token},
                        follow_redirects=True)
    assert resp.status_code == 200
    intimacao = IntimacaoCapturada.query.get(intimacao_id)
    assert intimacao.status == "vinculada"
    assert intimacao.processo_id == cenario["processo_id"]


def test_ignorar_intimacao_exige_motivo(app, client, login, cenario):
    with app.app_context():
        oab = db.session.get(OabMonitorada, cenario["oab_id"])
        pub = _publicacao_capturada(numero_processo="00000000000000000008", id_fonte="fonte-ignorar")
        intimacao = processar_publicacao_capturada(oab, pub)
        db.session.commit()
        intimacao_id = intimacao.id

    login("admin@escritoriox.com")
    r = client.get(f"/captacao-oab/triagem/{intimacao_id}")
    import re
    token = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode("utf-8")).group(1)

    # sem motivo: não muda o status
    client.post(f"/captacao-oab/triagem/{intimacao_id}/ignorar", data={"motivo": "", "csrf_token": token},
                follow_redirects=True)
    assert IntimacaoCapturada.query.get(intimacao_id).status == "pendente_triagem"

    resp = client.post(f"/captacao-oab/triagem/{intimacao_id}/ignorar",
                        data={"motivo": "duplicidade", "csrf_token": token}, follow_redirects=True)
    assert resp.status_code == 200
    intimacao = IntimacaoCapturada.query.get(intimacao_id)
    assert intimacao.status == "ignorada"
    assert intimacao.motivo_ignorada == "duplicidade"


def test_webhook_token_invalido_404(client, cenario):
    resp = client.post("/captacao-oab/webhook/token-errado", json={"id": "x"})
    assert resp.status_code == 404


def test_webhook_sem_identificador_400(client, cenario):
    resp = client.post("/captacao-oab/webhook/tok-abc-123", json={"texto": "sem id"})
    assert resp.status_code == 400


def test_webhook_sucesso_cria_intimacao(app, client, cenario):
    resp = client.post("/captacao-oab/webhook/tok-abc-123", json={
        "id_comunicacao": "push-1",
        "numero_processo": "12345678920268260001",
        "data_disponibilizacao": "2026-09-10",
        "tribunal": "TJSP",
        "texto": "Intimação via push",
    })
    assert resp.status_code == 201
    with app.app_context():
        intimacao = IntimacaoCapturada.query.filter_by(id_comunicacao_fonte="push-1").first()
        assert intimacao is not None
        assert intimacao.origem == "push_tribunal"
        assert intimacao.status == "vinculada"

    # segunda chamada com o mesmo id_comunicacao é idempotente (200, "duplicada")
    resp2 = client.post("/captacao-oab/webhook/tok-abc-123", json={
        "id_comunicacao": "push-1", "texto": "de novo",
    })
    assert resp2.status_code == 200
    assert resp2.get_json()["status"] == "duplicada"


# ---------------------------------------------------------------------------
# 4. Isolamento multi-tenant
# ---------------------------------------------------------------------------

def test_isolamento_multi_tenant_oab_e_triagem(app, client, login):
    with app.app_context():
        empresa_a, unidade_a = _criar_empresa_unidade("EmpresaA", "UNA")
        empresa_b, unidade_b = _criar_empresa_unidade("EmpresaB", "UNB")

        admin_a = Usuario(email="admina@teste.com", unidade_id=unidade_a.id, papel="admin", nome="Admin A")
        admin_a.set_senha(SENHA)
        admin_b = Usuario(email="adminb@teste.com", unidade_id=unidade_b.id, papel="admin", nome="Admin B")
        admin_b.set_senha(SENHA)
        db.session.add_all([admin_a, admin_b])
        db.session.flush()

        oab_a = OabMonitorada(unidade_id=unidade_a.id, numero="111111", uf="SP", criado_por_id=admin_a.id,
                               token_webhook="tok-a")
        oab_b = OabMonitorada(unidade_id=unidade_b.id, numero="222222", uf="RJ", criado_por_id=admin_b.id,
                               token_webhook="tok-b")
        db.session.add_all([oab_a, oab_b])
        db.session.commit()
        oab_a_id, oab_b_id = oab_a.id, oab_b.id

        intimacao_b = IntimacaoCapturada(oab_monitorada_id=oab_b.id, unidade_id=unidade_b.id,
                                          id_comunicacao_fonte="fonte-b", status="pendente_triagem")
        db.session.add(intimacao_b)
        db.session.commit()
        intimacao_b_id = intimacao_b.id

    login("admina@teste.com")
    r = client.get("/captacao-oab/")
    assert f"222222".encode() not in r.data or b"222222/RJ" not in r.data

    # admin A não pode alternar OAB da empresa B
    import re
    r2 = client.get("/captacao-oab/")
    token = re.search(r'name="csrf_token" value="([^"]+)"', r2.data.decode("utf-8")).group(1)
    resp = client.post(f"/captacao-oab/{oab_b_id}/alternar-ativo", data={"csrf_token": token})
    assert resp.status_code == 403

    # admin A não vê nem pode agir na triagem da empresa B
    r3 = client.get("/captacao-oab/triagem")
    assert str(intimacao_b_id).encode() not in r3.data or b"fonte-b" not in r3.data
    resp2 = client.get(f"/captacao-oab/triagem/{intimacao_b_id}")
    assert resp2.status_code == 403
    resp3 = client.post(f"/captacao-oab/triagem/{intimacao_b_id}/ignorar",
                         data={"motivo": "x", "csrf_token": token})
    assert resp3.status_code == 403


# ---------------------------------------------------------------------------
# 5. Regressão: aplicar_regra_proxima_acao (Movimentacao) inalterado
# ---------------------------------------------------------------------------

def test_aplicar_regra_proxima_acao_com_regra_cadastrada_por_codigo(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        regra = RegraProximaAcao(ato_capturado="Citação", codigo_tpu="123", acao_exigida="Apresentar contestação",
                                  prazo_base_dias=15, unidade_prazo="dias_uteis", ativo=True)
        db.session.add(regra)
        db.session.flush()

        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1), codigo_tpu="123",
                            texto_integral="Citação do réu")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        assert prazo is not None
        assert prazo.regra_aplicada_id == regra.id
        assert prazo.calculo_automatico is True
        assert prazo.descricao == "Apresentar contestação"
        assert prazo.data_inicial == date(2026, 9, 1)
        assert prazo.data_vencimento > prazo.data_inicial


def test_aplicar_regra_proxima_acao_sem_regra_gera_prazo_generico_quando_permitido(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1),
                            texto_integral="Ato sem regra cadastrada nenhuma")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=True)
        assert prazo is not None
        assert prazo.regra_aplicada_id is None
        assert "Análise necessária" in prazo.descricao
        assert prazo.calculo_automatico is False


def test_aplicar_regra_proxima_acao_sem_regra_e_sem_generico_retorna_none(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        mov = Movimentacao(processo_id=processo.id, data=datetime(2026, 9, 1),
                            texto_integral="Ato antigo sem regra")
        db.session.add(mov)
        db.session.flush()

        prazo = aplicar_regra_proxima_acao(mov, permitir_generico=False)
        assert prazo is None


def test_aplicar_regra_a_publicacao_casa_por_texto(app, cenario):
    with app.app_context():
        processo = db.session.get(Processo, cenario["processo_id"])
        regra = RegraProximaAcao(ato_capturado="manifestação sobre laudo", codigo_tpu=None,
                                  acao_exigida="Manifestar sobre o laudo pericial", prazo_base_dias=10,
                                  unidade_prazo="dias_uteis", ativo=True)
        db.session.add(regra)
        publicacao = Publicacao(processo_id=processo.id, diario="DJEN", data_publicacao=date(2026, 9, 11),
                                 teor="Intime-se a parte para manifestação sobre laudo em 10 dias.",
                                 hash_dedup="pub-regra-texto")
        db.session.add(publicacao)
        db.session.flush()

        prazo = aplicar_regra_a_publicacao(publicacao, permitir_generico=True)
        assert prazo is not None
        assert prazo.regra_aplicada_id == regra.id
        assert prazo.publicacao_id == publicacao.id
        assert prazo.data_inicial == date(2026, 9, 11)
