"""
Item 10 da lista de pipeline de IA jurídica trazida pelo usuário
(PENDENCIAS.md, seção -107): "Minuta no modelo do escritório — Biblioteca
de modelos por tipo de peça e área, com estilo e cláusulas do escritório
aprendidos das peças anteriores. Cada afirmação carrega a âncora do evento
que a sustenta."

Cobre três frentes:
- CRUD da biblioteca (app/routes/governanca.py, admin-only, escopado por
  empresa — ver app/models/modelo_peca.py::ModeloPeca).
- Resolução/aplicação automática do modelo certo (tipo_peca + área do
  processo) na geração de rascunho, sem o advogado escolher nada — e o
  texto do modelo nunca "vazando" pro digest usado pela checagem de
  grounding, mesma regra já valendo para texto_referencia avulso (ver
  tests/test_referencia_estilo_minuta.py).
- Âncora de evento: cada linha do digest carrega um `[tipo#id]`, e
  `_checar_ancoras` sinaliza uma âncora usada no rascunho que não bate com
  nenhuma âncora real do digest.
"""
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Movimentacao, Decisao, AnaliseProcessoIA, ModeloPeca
from app.models.modelo_peca import resolver_modelo_peca
from app.utils.analise_processo_ia import gerar_analise, montar_digest_processo, _checar_ancoras


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    empresa_id = empresa_basica["empresa_id"]
    admin_id = criar_usuario(unidade_id, "admin@modelos.com", papel="admin", nome="Admin Modelos")
    adv_id = criar_usuario(unidade_id, "adv@modelos.com", papel="advogado", nome="Advogado Modelos")

    cliente = Cliente(nome="Cliente Modelos", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-MODELO-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=adv_id, criado_por_id=adv_id)
    db.session.add(processo)
    db.session.commit()

    return dict(admin_id=admin_id, adv_id=adv_id, unidade_id=unidade_id, empresa_id=empresa_id,
                processo_id=processo.id, cliente_id=cliente.id)


def _criar_modelo(empresa_id, tipo_peca, area_direito, conteudo, ativo=True, nome="Modelo teste"):
    m = ModeloPeca(empresa_id=empresa_id, nome=nome, tipo_peca=tipo_peca, area_direito=area_direito,
                    conteudo=conteudo, ativo=ativo)
    db.session.add(m)
    db.session.commit()
    return m


DELIMITACAO_VALIDA = {
    "materia_fato": "Se houve entrega do produto.",
    "materia_direito": "Vício do produto.",
    "tese_a_sustentar": "Ausência de vício.",
    "resultado_pretendido": "Improcedência do pedido.",
}


# ---------- resolver_modelo_peca ----------

def test_resolver_prefere_modelo_da_area_exata_sobre_generico(app, cenario):
    with app.app_context():
        _criar_modelo(cenario["empresa_id"], "contestacao", None, "Esqueleto genérico.", nome="Genérico")
        especifico = _criar_modelo(cenario["empresa_id"], "contestacao", "Cível", "Esqueleto cível.",
                                    nome="Cível específico")

        achado = resolver_modelo_peca(cenario["empresa_id"], "contestacao", "Cível")
        assert achado.id == especifico.id


def test_resolver_usa_generico_quando_nao_ha_especifico_da_area(app, cenario):
    with app.app_context():
        generico = _criar_modelo(cenario["empresa_id"], "contestacao", None, "Esqueleto genérico.")

        achado = resolver_modelo_peca(cenario["empresa_id"], "contestacao", "Trabalhista")
        assert achado.id == generico.id


def test_resolver_ignora_modelo_inativo(app, cenario):
    with app.app_context():
        _criar_modelo(cenario["empresa_id"], "contestacao", "Cível", "Esqueleto.", ativo=False)
        assert resolver_modelo_peca(cenario["empresa_id"], "contestacao", "Cível") is None


def test_resolver_nunca_cruza_empresa(app, cenario):
    with app.app_context():
        from app.models import Empresa
        outra_empresa = Empresa(nome="Outro Escritório")
        db.session.add(outra_empresa)
        db.session.commit()
        _criar_modelo(outra_empresa.id, "contestacao", "Cível", "Esqueleto de outro escritório.")

        assert resolver_modelo_peca(cenario["empresa_id"], "contestacao", "Cível") is None


def test_resolver_sem_modelo_nenhum_devolve_none(app, cenario):
    with app.app_context():
        assert resolver_modelo_peca(cenario["empresa_id"], "contestacao", "Cível") is None


# ---------- gerar_analise aplica o modelo (motor, sem HTTP) ----------

def test_modelo_do_escritorio_chega_no_prompt_mas_nunca_no_digest(app, cenario, monkeypatch):
    processo = db.session.get(Processo, cenario["processo_id"])
    modelo = _criar_modelo(cenario["empresa_id"], "contestacao", "Cível",
                            "CLÁUSULA PADRÃO: menciona verba de R$ 55.555,00 só de exemplo de estilo.")

    capturado = {}

    def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        return "III — DOS PEDIDOS\nCondenação de R$ 55.555,00 conforme cláusula padrão."

    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

    resultado, truncado = gerar_analise(processo, "rascunho_peticao", instrucao="contestação padrão",
                                         tipo_peca="contestacao", delimitacao=DELIMITACAO_VALIDA,
                                         modelo_peca=modelo)

    assert "CLÁUSULA PADRÃO" in capturado["system"], "o texto do modelo precisa chegar no prompt"
    assert "R$ 55.555,00" not in capturado["system"].split("Modelo do escritório")[0], \
        "o valor do modelo não pode aparecer antes do bloco do modelo (ou seja, não está no digest real)"
    assert "NÃO aparece nos dados reais" in resultado, \
        "valor vazado do modelo do escritório (nunca do processo real) tem que ser sinalizado pela " \
        "checagem de grounding, igual a texto_referencia avulso"


def test_resumo_ignora_modelo_do_escritorio(app, cenario, monkeypatch):
    processo = db.session.get(Processo, cenario["processo_id"])
    modelo = _criar_modelo(cenario["empresa_id"], "contestacao", "Cível", "Isto nunca deveria aparecer.")

    capturado = {}

    def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        return "SITUAÇÃO ATUAL\n—"

    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

    gerar_analise(processo, "resumo", modelo_peca=modelo)
    assert "Isto nunca deveria aparecer" not in capturado["system"]


# ---------- âncora de evento ----------

def test_digest_movimentacao_carrega_ancora(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    mov = Movimentacao(processo_id=processo.id, data=datetime.utcnow(), texto_integral="Citação do réu.",
                        hash_dedup="hd-ancora-1")
    db.session.add(mov)
    db.session.commit()

    texto, _ = montar_digest_processo(processo)
    assert f"[mov#{mov.id}]" in texto


def test_digest_decisao_carrega_ancora(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    dec = Decisao(processo_id=processo.id, tipo="sentenca", tese="Prescrição.")
    db.session.add(dec)
    db.session.commit()

    texto, _ = montar_digest_processo(processo)
    assert f"[dec#{dec.id}]" in texto


def test_checar_ancoras_aceita_ancora_valida_do_digest(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    mov = Movimentacao(processo_id=processo.id, data=datetime.utcnow(), texto_integral="Citação do réu.",
                        hash_dedup="hd-ancora-2")
    db.session.add(mov)
    db.session.commit()

    digest, _ = montar_digest_processo(processo)
    resultado = f"O réu foi citado [mov#{mov.id}]."
    assert _checar_ancoras(resultado, digest) == []


def test_checar_ancoras_sinaliza_ancora_inventada(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    mov = Movimentacao(processo_id=processo.id, data=datetime.utcnow(), texto_integral="Citação do réu.",
                        hash_dedup="hd-ancora-3")
    db.session.add(mov)
    db.session.commit()

    digest, _ = montar_digest_processo(processo)
    id_inexistente = mov.id + 9999
    resultado = f"O réu foi citado [mov#{id_inexistente}]."
    avisos = _checar_ancoras(resultado, digest)
    assert len(avisos) == 1
    assert f"mov#{id_inexistente}" in avisos[0]


def test_checar_ancoras_aceita_ids_agrupados(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    for i in range(3):
        db.session.add(Movimentacao(processo_id=processo.id, data=datetime.utcnow() - timedelta(hours=i),
                                     texto_integral="Ato ordinatório repetido", hash_dedup=f"hd-grupo-{i}"))
    db.session.commit()

    digest, _ = montar_digest_processo(processo)
    movs = Movimentacao.query.filter_by(processo_id=processo.id).all()
    # A linha agrupada carrega uma âncora "[mov#id1,id2,id3]" — cada id
    # individual precisa ser aceito como âncora válida separadamente.
    for m in movs:
        assert _checar_ancoras(f"texto [mov#{m.id}]", digest) == []


def test_gerar_analise_com_ancora_invalida_gera_aviso_end_to_end(app, cenario, monkeypatch):
    processo = db.session.get(Processo, cenario["processo_id"])
    db.session.add(Movimentacao(processo_id=processo.id, data=datetime.utcnow(),
                                 texto_integral="Citação do réu.", hash_dedup="hd-e2e"))
    db.session.commit()

    def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
        return "I — DOS FATOS\nO réu foi citado [mov#999999]."

    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

    resultado, _ = gerar_analise(processo, "rascunho_peticao", instrucao="contestação",
                                  delimitacao=DELIMITACAO_VALIDA)
    assert "mov#999999" in resultado
    assert "não encontrei esse evento" in resultado


# ---------- rota: resolução automática + tipo_peca extensível ----------

def _fake_fila(monkeypatch):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append(args)
        return None

    import app.routes.processos as mod
    monkeypatch.setattr(mod, "enfileirar", _fake_enfileirar)
    monkeypatch.setattr(mod.agente_ia_router, "provedor_disponivel", lambda empresa: True)
    return chamadas


def test_rota_resolve_modelo_automaticamente_e_grava_na_analise(app, client, login, post_csrf, cenario,
                                                                   monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    modelo = _criar_modelo(cenario["empresa_id"], "contestacao", "Cível", "Esqueleto cível.")

    login("adv@modelos.com")
    payload = {"tipo": "rascunho_peticao", "instrucao": "contestação padrão", "tipo_peca": "contestacao"}
    payload.update(DELIMITACAO_VALIDA)
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", payload,
                  get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None
    assert analise.modelo_peca_id == modelo.id

    # args = (analise.id, processo.id, tipo, instrucao, texto_referencia, tipo_peca, delimitacao_id, modelo_peca_id)
    assert len(chamadas) == 1
    assert chamadas[0][7] == modelo.id


def test_rota_sem_modelo_cadastrado_nao_quebra_nem_bloqueia(app, client, login, post_csrf, cenario,
                                                               monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("adv@modelos.com")
    payload = {"tipo": "rascunho_peticao", "instrucao": "contestação padrão", "tipo_peca": "contestacao"}
    payload.update(DELIMITACAO_VALIDA)
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", payload,
                  get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise.modelo_peca_id is None
    assert chamadas[0][7] is None


def test_rota_aceita_tipo_peca_customizado_cadastrado_pela_empresa(app, client, login, post_csrf, cenario,
                                                                      monkeypatch):
    # "replica" não está em TIPOS_PECA_COM_DOSSIE (só contestação/recurso têm
    # dossiê automático embutido) — mas passa a ser aceito pelo <select>/rota
    # porque o próprio escritório já cadastrou um ModeloPeca pra esse tipo.
    chamadas = _fake_fila(monkeypatch)
    modelo = _criar_modelo(cenario["empresa_id"], "replica", None, "Esqueleto de réplica.")

    login("adv@modelos.com")
    payload = {"tipo": "rascunho_peticao", "instrucao": "réplica padrão", "tipo_peca": "replica"}
    payload.update(DELIMITACAO_VALIDA)
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", payload,
                  get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise.tipo_peca == "replica"
    assert analise.modelo_peca_id == modelo.id


def test_rota_tipo_peca_nao_cadastrado_em_lugar_nenhum_vira_none(app, client, login, post_csrf, cenario,
                                                                    monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("adv@modelos.com")
    payload = {"tipo": "rascunho_peticao", "instrucao": "algo", "tipo_peca": "tipo_qualquer_nao_cadastrado"}
    payload.update(DELIMITACAO_VALIDA)
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", payload,
                  get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise.tipo_peca is None
    assert analise.modelo_peca_id is None


# ---------- CRUD admin (governanca_bp) ----------

def test_admin_cria_edita_e_alterna_modelo(app, client, login, post_csrf, cenario):
    login("admin@modelos.com")

    r = post_csrf("/governanca/modelos-peca/novo", {
        "nome": "Contestação padrão", "tipo_peca": "Contestação Cível", "area_direito": "Cível",
        "conteudo": "Esqueleto de contestação.",
    }, get_url="/governanca/modelos-peca/novo")
    assert r.status_code == 200

    modelo = ModeloPeca.query.filter_by(empresa_id=cenario["empresa_id"]).first()
    assert modelo is not None
    assert modelo.tipo_peca == "contestação_cível"  # normalizado: minúsculo, espaço -> "_"
    assert modelo.ativo is True

    r = post_csrf(f"/governanca/modelos-peca/{modelo.id}/editar", {
        "nome": "Contestação padrão v2", "tipo_peca": modelo.tipo_peca, "area_direito": "Cível",
        "conteudo": "Esqueleto revisado.",
    }, get_url=f"/governanca/modelos-peca/{modelo.id}/editar")
    assert r.status_code == 200
    modelo = db.session.get(ModeloPeca, modelo.id)
    assert modelo.nome == "Contestação padrão v2"
    assert modelo.conteudo == "Esqueleto revisado."

    r = post_csrf(f"/governanca/modelos-peca/{modelo.id}/alternar-ativo", {},
                  get_url="/governanca/modelos-peca")
    assert r.status_code == 200
    assert db.session.get(ModeloPeca, modelo.id).ativo is False


def test_advogado_comum_nao_acessa_crud_de_modelos(app, client, login, cenario):
    login("adv@modelos.com")
    r = client.get("/governanca/modelos-peca")
    assert r.status_code == 403


def test_admin_de_outra_empresa_nao_edita_modelo_alheio(app, client, login, post_csrf, cenario,
                                                           criar_usuario, empresa_basica):
    modelo = _criar_modelo(cenario["empresa_id"], "contestacao", "Cível", "Esqueleto.")

    from app.models import Empresa, Licenca, Unidade
    from datetime import date, timedelta as td
    outra_empresa = Empresa(nome="Outro Escritório 2")
    db.session.add(outra_empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=outra_empresa.id, plano="mensal", valor_negociado=100,
                            status="ativa", data_inicio=date.today(), data_fim=date.today() + td(days=30)))
    outra_unidade = Unidade(nome="Outra Matriz", codigo="OM1", empresa_id=outra_empresa.id)
    db.session.add(outra_unidade)
    db.session.commit()
    admin_outro_id = criar_usuario(outra_unidade.id, "admin_outro@modelos.com", papel="admin")

    login("admin_outro@modelos.com")
    r = client.get(f"/governanca/modelos-peca/{modelo.id}/editar")
    assert r.status_code == 404


def test_lista_modelos_so_mostra_da_propria_empresa(app, client, login, cenario, criar_usuario):
    _criar_modelo(cenario["empresa_id"], "contestacao", "Cível", "Esqueleto da própria empresa.",
                  nome="Meu modelo")

    from app.models import Empresa, Licenca, Unidade
    from datetime import date, timedelta as td
    outra_empresa = Empresa(nome="Outro Escritório 3")
    db.session.add(outra_empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=outra_empresa.id, plano="mensal", valor_negociado=100,
                            status="ativa", data_inicio=date.today(), data_fim=date.today() + td(days=30)))
    outra_unidade = Unidade(nome="Outra Matriz 2", codigo="OM2", empresa_id=outra_empresa.id)
    db.session.add(outra_unidade)
    db.session.commit()
    _criar_modelo(outra_empresa.id, "contestacao", "Cível", "Esqueleto de outro escritório.",
                  nome="Modelo alheio")

    login("admin@modelos.com")
    html = client.get("/governanca/modelos-peca").data.decode("utf-8")
    assert "Meu modelo" in html
    assert "Modelo alheio" not in html
