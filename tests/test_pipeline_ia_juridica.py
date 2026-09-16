"""
Testa três frentes implementadas a partir da análise da lista de pipeline
de IA jurídica trazida pelo usuário (PENDENCIAS.md, seção -101):

- item 7 (Delimitação do objeto): "a minuta só começa depois disso
  resolvido" — rascunho_peticao passa a EXIGIR matéria de fato/direito
  controvertidas, tese e resultado pretendido, tanto na rota quanto no
  motor (`gerar_analise`), e o texto delimitado precisa chegar no prompt.
- item 4 (Dossiê por tipo de peça): `tipo_peca="contestacao"`/"recurso"
  muda o que `montar_digest_processo` prioriza (petição inicial, decisão
  mais recente, laudos, últimos 3 atos) em vez do corte genérico.
- item 5 (Relatório estruturado): `Processo.pedidos`/`causa_de_pedir`
  (texto livre, editável no form) e a rota `processos.relatorio`, que lista
  partes, pedidos, causa de pedir, decisões (linkadas), provas, valores e
  incidentes pendentes (linkados).
"""
import pytest

from app.extensions import db
from app.models import Cliente, Processo, Documento, Movimentacao, Decisao, Prazo, AnaliseProcessoIA, DelimitacaoObjeto
from app.utils.analise_processo_ia import gerar_analise, montar_digest_processo


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    usuario_id = criar_usuario(unidade_id, "objeto@teste.com", papel="advogado", nome="Advogado Objeto")

    cliente = Cliente(nome="Cliente Objeto", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(numero_interno="P-OBJETO-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=usuario_id, criado_por_id=usuario_id,
                         valor_causa=10000)
    db.session.add(processo)
    db.session.commit()

    return dict(usuario_id=usuario_id, unidade_id=unidade_id, processo_id=processo.id)


DELIMITACAO_VALIDA = {
    "materia_fato": "Se houve entrega do produto.",
    "materia_direito": "Vício do produto (art. 18, CDC).",
    "tese_a_sustentar": "Ausência de vício — produto entregue conforme especificação.",
    "resultado_pretendido": "Improcedência total do pedido.",
}


def _fake_fila(monkeypatch):
    chamadas = []

    def _fake_enfileirar(func_path, *args, **kwargs):
        chamadas.append(args)
        return None

    import app.routes.processos as mod
    monkeypatch.setattr(mod, "enfileirar", _fake_enfileirar)
    monkeypatch.setattr(mod.agente_ia_router, "provedor_disponivel", lambda empresa: True)
    return chamadas


# ---------- item 7: delimitação do objeto ----------

def test_gerar_analise_recusa_rascunho_sem_delimitacao(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    with pytest.raises(ValueError, match="Delimite o objeto"):
        gerar_analise(processo, "rascunho_peticao", instrucao="contestação padrão")


def test_gerar_analise_com_delimitacao_inclui_bloco_no_prompt(app, cenario, monkeypatch):
    processo = db.session.get(Processo, cenario["processo_id"])
    capturado = {}

    def _fake_gerar_resposta(empresa, system, mensagens, max_tokens=None):
        capturado["system"] = system
        return "III — DOS PEDIDOS\nImprocedência."

    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta", _fake_gerar_resposta)

    gerar_analise(processo, "rascunho_peticao", instrucao="contestação padrão", delimitacao=DELIMITACAO_VALIDA)

    assert "Ausência de vício" in capturado["system"]
    assert "Improcedência total do pedido" in capturado["system"]


def test_resumo_nao_exige_delimitacao(app, cenario, monkeypatch):
    processo = db.session.get(Processo, cenario["processo_id"])
    import app.utils.analise_processo_ia as mod
    monkeypatch.setattr(mod.agente_ia_router, "gerar_resposta",
                         lambda empresa, system, mensagens, max_tokens=None: "SITUAÇÃO ATUAL\n—")
    # não levanta ValueError mesmo sem delimitacao nenhuma
    gerar_analise(processo, "resumo")


def test_rota_recusa_rascunho_sem_delimitacao(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("objeto@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {
        "tipo": "rascunho_peticao", "instrucao": "contestação padrão",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert "Delimite o objeto" in r.data.decode("utf-8")
    assert len(chamadas) == 0
    assert AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).count() == 0


def test_rota_com_delimitacao_cria_analise_e_delimitacao_linkadas(app, client, login, post_csrf, cenario,
                                                                    monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("objeto@teste.com")
    payload = {"tipo": "rascunho_peticao", "instrucao": "contestação padrão"}
    payload.update(DELIMITACAO_VALIDA)
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", payload,
                  get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    analise = AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert analise is not None
    delimitacao = DelimitacaoObjeto.query.filter_by(analise_id=analise.id).first()
    assert delimitacao is not None
    assert delimitacao.tese_a_sustentar == DELIMITACAO_VALIDA["tese_a_sustentar"]
    assert delimitacao.processo_id == cenario["processo_id"]

    # args = (analise.id, processo.id, tipo, instrucao, texto_referencia, tipo_peca, delimitacao_id)
    assert len(chamadas) == 1
    assert chamadas[0][6] == delimitacao.id


def test_resumo_nao_exige_delimitacao_na_rota(app, client, login, post_csrf, cenario, monkeypatch):
    chamadas = _fake_fila(monkeypatch)
    login("objeto@teste.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/analise-ia", {"tipo": "resumo"},
                  get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert len(chamadas) == 1
    assert AnaliseProcessoIA.query.filter_by(processo_id=cenario["processo_id"]).count() == 1


# ---------- item 4: dossiê por tipo de peça ----------

def test_dossie_contestacao_inclui_peticao_inicial_mais_antiga(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    db.session.add_all([
        Documento(processo_id=processo.id, nome_original="inicial.pdf", nome_arquivo="inicial.pdf",
                   categoria="peticao", tamanho_kb=1),
        Documento(processo_id=processo.id, nome_original="laudo_pericial.pdf", nome_arquivo="laudo.pdf",
                   categoria="laudo", tamanho_kb=1),
    ])
    db.session.commit()

    texto, _ = montar_digest_processo(processo, tipo_peca="contestacao")
    assert "inicial.pdf" in texto
    assert "laudo_pericial.pdf" in texto


def test_dossie_recurso_inclui_decisao_recorrida(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    db.session.add(Decisao(processo_id=processo.id, tipo="sentenca", resultado="improcedente",
                             tese="Prescrição da pretensão."))
    db.session.commit()

    texto, _ = montar_digest_processo(processo, tipo_peca="recurso")
    assert "Decisão recorrida" in texto
    assert "Prescrição da pretensão" in texto
    assert "improcedente" in texto


def test_dossie_sem_tipo_peca_nao_muda_digest_generico(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    texto_sem, _ = montar_digest_processo(processo)
    texto_com_none, _ = montar_digest_processo(processo, tipo_peca=None)
    assert texto_sem == texto_com_none


def test_dossie_ultimos_3_atos_sempre_presentes(app, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    from datetime import datetime, timedelta
    for i in range(5):
        db.session.add(Movimentacao(processo_id=processo.id, data=datetime.utcnow() - timedelta(days=i),
                                     texto_integral=f"Ato número {i}", hash_dedup=f"hash-{i}"))
    db.session.commit()

    texto, _ = montar_digest_processo(processo, tipo_peca="contestacao")
    assert "Últimos 3 atos" in texto
    assert "Ato número 0" in texto
    assert "Ato número 1" in texto
    assert "Ato número 2" in texto


# ---------- item 5: relatório estruturado ----------

def test_pedidos_e_causa_de_pedir_sao_salvos_na_edicao(app, client, login, post_csrf, cenario):
    login("objeto@teste.com")
    processo = db.session.get(Processo, cenario["processo_id"])
    r = post_csrf(f"/processos/{processo.id}/editar", {
        "area_direito": "Cível", "cliente_id": str(processo.cliente_id),
        "pedidos": "Condenação ao pagamento de indenização.",
        "causa_de_pedir": "Descumprimento contratual.",
    }, get_url=f"/processos/{processo.id}/editar")
    assert r.status_code == 200

    processo = db.session.get(Processo, cenario["processo_id"])
    assert processo.pedidos == "Condenação ao pagamento de indenização."
    assert processo.causa_de_pedir == "Descumprimento contratual."


def test_relatorio_mostra_pedidos_causa_de_pedir_e_links(app, client, login, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    processo.pedidos = "Pagamento de R$ 5.000,00."
    processo.causa_de_pedir = "Inadimplemento contratual."
    db.session.add(Decisao(processo_id=processo.id, tipo="decisao", tese="Tutela de urgência deferida."))
    db.session.add(Prazo(processo_id=processo.id, descricao="Apresentar contestação",
                          data_vencimento=__import__("datetime").date.today(), status="pendente"))
    db.session.commit()

    decisao = Decisao.query.filter_by(processo_id=processo.id).first()
    prazo = Prazo.query.filter_by(processo_id=processo.id).first()

    login("objeto@teste.com")
    r = client.get(f"/processos/{processo.id}/relatorio")
    html = r.data.decode("utf-8")
    assert r.status_code == 200
    assert "Pagamento de R$ 5.000,00" in html
    assert "Inadimplemento contratual" in html
    assert f"decisao-{decisao.id}" in html
    assert f"prazo-{prazo.id}" in html


def test_relatorio_processo_sem_dados_mostra_mensagens_honestas(app, client, login, cenario):
    login("objeto@teste.com")
    r = client.get(f"/processos/{cenario['processo_id']}/relatorio")
    html = r.data.decode("utf-8")
    assert r.status_code == 200
    assert "Não preenchido" in html or "Não preenchida" in html
    assert "Nenhuma decisão registrada" in html
    assert "Nenhum incidente" in html


def test_detalhe_tem_ancoras_de_decisao_prazo_e_movimentacao(app, client, login, cenario):
    processo = db.session.get(Processo, cenario["processo_id"])
    db.session.add(Decisao(processo_id=processo.id, tipo="decisao", tese="X"))
    db.session.add(Prazo(processo_id=processo.id, descricao="Y",
                          data_vencimento=__import__("datetime").date.today(), status="pendente"))
    from datetime import datetime
    db.session.add(Movimentacao(processo_id=processo.id, data=datetime.utcnow(), texto_integral="Z",
                                 hash_dedup="hash-unico"))
    db.session.commit()

    decisao = Decisao.query.filter_by(processo_id=processo.id).first()
    prazo = Prazo.query.filter_by(processo_id=processo.id).first()
    mov = Movimentacao.query.filter_by(processo_id=processo.id).first()

    login("objeto@teste.com")
    html = client.get(f"/processos/{processo.id}").data.decode("utf-8")
    assert f'id="decisao-{decisao.id}"' in html
    assert f'id="prazo-{prazo.id}"' in html
    assert f'id="mov-{mov.id}"' in html
