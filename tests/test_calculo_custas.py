"""
Item 9 da lista de pipeline de IA jurídica trazida pelo usuário
(PENDENCIAS.md, seção -108): "Cálculo — Base de cálculo, valor da causa,
custas, preparo e guias quando aplicável, com memória de cálculo aberta
para conferência."

Cobre três frentes:
- app/utils/calculo_custas.py::calcular_custa — as três formas de regra
  (percentual com mínimo/máximo, valor fixo simples, faixas por valor),
  CustaNaoCadastradaError quando não há regra ativa (nunca inventa um
  valor), e a memória de cálculo linha a linha.
- CRUD de TabelaCustas em app/routes/governanca.py — admin-only, GLOBAL
  (mesma regra de RegraProximaAcao/MapaEstadoTPU: não é escopado por
  empresa, é fato objetivo da lei de custas do tribunal), e o botão
  "Carregar tabela padrão TJSP" (idempotente).
- app/routes/processos.py::calcular_custas — grava CalculoCustas com a
  memória completa, nunca bloqueia por regra ausente sem avisar.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Cliente, Processo, TabelaCustas, CalculoCustas
from app.utils.calculo_custas import calcular_custa, CustaNaoCadastradaError, TABELA_PADRAO_TJSP


# ---------- fixtures ----------

@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    empresa_id = empresa_basica["empresa_id"]
    admin_id = criar_usuario(unidade_id, "admin@custas.com", papel="admin", nome="Admin Custas")
    adv_id = criar_usuario(unidade_id, "adv@custas.com", papel="advogado", nome="Advogado Custas")

    cliente = Cliente(nome="Cliente Custas", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(numero_interno="P-CUSTAS-1", cliente_id=cliente.id, unidade_id=unidade_id,
                         area_direito="Cível", responsavel_id=adv_id, criado_por_id=adv_id,
                         tribunal="TJSP")
    processo_sem_tribunal = Processo(numero_interno="P-CUSTAS-2", cliente_id=cliente.id, unidade_id=unidade_id,
                                      area_direito="Cível", responsavel_id=adv_id, criado_por_id=adv_id)
    db.session.add_all([processo, processo_sem_tribunal])
    db.session.commit()

    return dict(admin_id=admin_id, adv_id=adv_id, unidade_id=unidade_id, empresa_id=empresa_id,
                processo_id=processo.id, processo_sem_tribunal_id=processo_sem_tribunal.id)


def _criar_linha(tribunal="TJSP", tipo_custa="teste", descricao="Linha de teste", ativo=True, **kw):
    linha = TabelaCustas(tribunal=tribunal, tipo_custa=tipo_custa, descricao=descricao, ativo=ativo, **kw)
    db.session.add(linha)
    db.session.commit()
    return linha


# ---------- calcular_custa: regra percentual ----------

def test_calcula_percentual_dentro_da_faixa(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="distribuicao", percentual=Decimal("1.5"),
                      valor_minimo=Decimal("100.00"), valor_maximo=Decimal("100000.00"))
        resultado = calcular_custa("TJSP", "distribuicao", valor_base=Decimal("10000.00"))
        assert resultado["valor"] == Decimal("150.00")
        assert any("Percentual aplicado" in linha and "1.5" in linha for linha in resultado["memoria"])


def test_calcula_percentual_aplica_valor_minimo(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="distribuicao", percentual=Decimal("1.5"),
                      valor_minimo=Decimal("192.10"), valor_maximo=Decimal("115260.00"))
        resultado = calcular_custa("TJSP", "distribuicao", valor_base=Decimal("1000.00"))
        assert resultado["valor"] == Decimal("192.10")
        assert any("mínimo" in linha.lower() for linha in resultado["memoria"])


def test_calcula_percentual_aplica_valor_maximo(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="distribuicao", percentual=Decimal("1.5"),
                      valor_minimo=Decimal("192.10"), valor_maximo=Decimal("115260.00"))
        resultado = calcular_custa("TJSP", "distribuicao", valor_base=Decimal("50000000.00"))
        assert resultado["valor"] == Decimal("115260.00")
        assert any("máximo" in linha.lower() for linha in resultado["memoria"])


def test_calcula_percentual_sem_valor_base_levanta_erro(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="distribuicao", percentual=Decimal("1.5"))
        with pytest.raises(ValueError):
            calcular_custa("TJSP", "distribuicao", valor_base=None)


# ---------- calcular_custa: valor fixo simples ----------

def test_calcula_valor_fixo_simples(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="agravo", valor_fixo=Decimal("576.30"))
        resultado = calcular_custa("TJSP", "agravo")
        assert resultado["valor"] == Decimal("576.30")


def test_calcula_valor_fixo_multiplica_por_quantidade(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="porte", valor_fixo=Decimal("64.24"))
        resultado = calcular_custa("TJSP", "porte", quantidade=3)
        assert resultado["valor"] == Decimal("192.72")
        assert any("Quantidade: 3" in linha for linha in resultado["memoria"])


# ---------- calcular_custa: faixas ----------

def test_calcula_faixas_escolhe_a_faixa_correta(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="inventario", valor_fixo=Decimal("384.20"), faixa_ate=Decimal("50000.00"),
                      descricao="até 50 mil")
        _criar_linha(tipo_custa="inventario", valor_fixo=Decimal("3842.00"), faixa_ate=Decimal("500000.00"),
                      descricao="50 a 500 mil")
        _criar_linha(tipo_custa="inventario", valor_fixo=Decimal("115260.00"), faixa_ate=None,
                      descricao="acima de 5 milhões")

        barato = calcular_custa("TJSP", "inventario", valor_base=Decimal("30000.00"))
        assert barato["valor"] == Decimal("384.20")

        medio = calcular_custa("TJSP", "inventario", valor_base=Decimal("200000.00"))
        assert medio["valor"] == Decimal("3842.00")

        caro = calcular_custa("TJSP", "inventario", valor_base=Decimal("9999999.00"))
        assert caro["valor"] == Decimal("115260.00")


def test_calcula_faixas_sem_valor_base_levanta_erro(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="inventario", valor_fixo=Decimal("384.20"), faixa_ate=Decimal("50000.00"))
        with pytest.raises(ValueError):
            calcular_custa("TJSP", "inventario", valor_base=None)


# ---------- calcular_custa: sem regra cadastrada ----------

def test_sem_regra_cadastrada_levanta_erro_proprio_nunca_inventa(app, cenario):
    with app.app_context():
        with pytest.raises(CustaNaoCadastradaError):
            calcular_custa("TJSP", "tipo_inexistente", valor_base=Decimal("1000"))


def test_regra_inativa_e_ignorada(app, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="desativada", valor_fixo=Decimal("100.00"), ativo=False)
        with pytest.raises(CustaNaoCadastradaError):
            calcular_custa("TJSP", "desativada")


def test_regra_de_outro_tribunal_nao_e_usada(app, cenario):
    with app.app_context():
        _criar_linha(tribunal="TJRJ", tipo_custa="distribuicao", percentual=Decimal("2.0"))
        with pytest.raises(CustaNaoCadastradaError):
            calcular_custa("TJSP", "distribuicao", valor_base=Decimal("1000"))


# ---------- TABELA_PADRAO_TJSP: sanidade dos dados pesquisados ----------

def test_tabela_padrao_tjsp_tem_dados_consistentes():
    for linha in TABELA_PADRAO_TJSP:
        assert linha["tribunal"] == "TJSP"
        assert linha["tipo_custa"]
        assert linha["descricao"]
        assert linha["observacao"], "toda linha da tabela padrão precisa citar a base legal/fonte"
        tem_percentual = linha.get("percentual") is not None
        tem_valor_fixo = linha.get("valor_fixo") is not None
        assert tem_percentual != tem_valor_fixo, "uma linha é percentual OU valor fixo, nunca os dois nem nenhum"


def test_tabela_padrao_tjsp_carrega_via_calcular_custa(app, cenario):
    with app.app_context():
        for linha in TABELA_PADRAO_TJSP:
            db.session.add(TabelaCustas(**linha, ativo=True))
        db.session.commit()

        resultado = calcular_custa("TJSP", "distribuicao_civel", valor_base=Decimal("50000.00"))
        assert resultado["valor"] == Decimal("750.00")  # 1,5% de 50.000 (acima do mínimo de 192,10)

        resultado_agravo = calcular_custa("TJSP", "agravo_instrumento")
        assert resultado_agravo["valor"] == Decimal("576.30")


# ---------- rota /governanca/tabela-custas (CRUD admin-only, global) ----------

def test_carregar_tabela_padrao_e_idempotente(app, client, login, post_csrf, cenario):
    login("admin@custas.com")
    r1 = post_csrf("/governanca/tabela-custas/carregar-padrao-tjsp", {}, get_url="/governanca/tabela-custas")
    assert r1.status_code == 200
    total_apos_primeira = TabelaCustas.query.count()
    assert total_apos_primeira == len(TABELA_PADRAO_TJSP)

    r2 = post_csrf("/governanca/tabela-custas/carregar-padrao-tjsp", {}, get_url="/governanca/tabela-custas")
    assert r2.status_code == 200
    assert TabelaCustas.query.count() == total_apos_primeira, "carregar de novo não deve duplicar linhas"


def test_nova_linha_editar_alternar_excluir(app, client, login, post_csrf, cenario):
    login("admin@custas.com")
    r = post_csrf("/governanca/tabela-custas/nova", {
        "tribunal": "tjsp", "tipo_custa": "Distribuição Cível", "descricao": "Teste manual",
        "percentual": "1,5", "valor_minimo": "192,10", "valor_maximo": "115260,00",
    }, get_url="/governanca/tabela-custas/nova")
    assert r.status_code == 200

    linha = TabelaCustas.query.filter_by(descricao="Teste manual").first()
    assert linha is not None
    assert linha.tribunal == "TJSP"
    assert linha.tipo_custa == "distribuição_cível"
    assert linha.percentual == Decimal("1.5")

    r = post_csrf(f"/governanca/tabela-custas/{linha.id}/editar", {
        "tribunal": "TJSP", "tipo_custa": "distribuicao_civel", "descricao": "Teste manual editado",
        "percentual": "2,0", "valor_minimo": "", "valor_maximo": "",
    }, get_url=f"/governanca/tabela-custas/{linha.id}/editar")
    assert r.status_code == 200
    linha = db.session.get(TabelaCustas, linha.id)
    assert linha.descricao == "Teste manual editado"
    assert linha.percentual == Decimal("2.0")
    assert linha.valor_minimo is None

    r = post_csrf(f"/governanca/tabela-custas/{linha.id}/alternar-ativo", {},
                   get_url="/governanca/tabela-custas")
    assert r.status_code == 200
    assert db.session.get(TabelaCustas, linha.id).ativo is False

    r = post_csrf(f"/governanca/tabela-custas/{linha.id}/excluir", {}, get_url="/governanca/tabela-custas")
    assert r.status_code == 200
    assert db.session.get(TabelaCustas, linha.id) is None


def test_advogado_comum_nao_acessa_tabela_custas(app, client, login, cenario):
    login("adv@custas.com")
    r = client.get("/governanca/tabela-custas")
    assert r.status_code == 403


def test_tabela_custas_e_global_visivel_por_admin_de_outra_empresa(app, client, login, post_csrf, cenario,
                                                                     criar_usuario):
    from app.models import Empresa, Licenca, Unidade
    outra_empresa = Empresa(nome="Outro Escritório Custas")
    db.session.add(outra_empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=outra_empresa.id, plano="mensal", valor_negociado=100,
                            status="ativa", data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    outra_unidade = Unidade(nome="Outra Matriz Custas", codigo="OMC1", empresa_id=outra_empresa.id)
    db.session.add(outra_unidade)
    db.session.commit()
    admin_outro_id = criar_usuario(outra_unidade.id, "admin_outro@custas.com", papel="admin")

    login("admin@custas.com")
    post_csrf("/governanca/tabela-custas/nova", {
        "tribunal": "TJSP", "tipo_custa": "global_teste", "descricao": "Linha global",
        "valor_fixo": "100,00",
    }, get_url="/governanca/tabela-custas/nova")

    login("admin_outro@custas.com")
    r = client.get("/governanca/tabela-custas")
    assert r.status_code == 200
    assert "Linha global" in r.data.decode("utf-8"), \
        "TabelaCustas é global (regra de tribunal, não config do escritório) — outro admin precisa ver a mesma linha"


# ---------- rota /processos/<id>/custas ----------

def test_rota_calcula_e_salva_memoria(app, client, login, post_csrf, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="distribuicao_civel", percentual=Decimal("1.5"),
                      valor_minimo=Decimal("192.10"), valor_maximo=Decimal("115260.00"),
                      observacao="Lei 11.608/2003")

    login("adv@custas.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/custas", {
        "tipo_custa": "distribuicao_civel", "valor_base": "50000,00",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200

    calculo = CalculoCustas.query.filter_by(processo_id=cenario["processo_id"]).first()
    assert calculo is not None
    assert calculo.valor_calculado == Decimal("750.00")
    assert "Lei 11.608/2003" in calculo.memoria_calculo
    assert calculo.calculado_por_id is not None


def test_rota_sem_regra_cadastrada_avisa_e_nao_salva(app, client, login, post_csrf, cenario):
    login("adv@custas.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/custas", {
        "tipo_custa": "tipo_sem_regra", "valor_base": "10000,00",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert CalculoCustas.query.filter_by(processo_id=cenario["processo_id"]).count() == 0
    texto = r.data.decode("utf-8").lower()
    assert "nenhuma regra" in texto or "cadastr" in texto


def test_rota_sem_tribunal_no_processo_avisa(app, client, login, post_csrf, cenario):
    login("adv@custas.com")
    r = post_csrf(f"/processos/{cenario['processo_sem_tribunal_id']}/custas", {
        "tipo_custa": "distribuicao_civel", "valor_base": "10000,00",
    }, get_url=f"/processos/{cenario['processo_sem_tribunal_id']}")
    assert r.status_code == 200
    assert CalculoCustas.query.filter_by(processo_id=cenario["processo_sem_tribunal_id"]).count() == 0
    assert "tribunal" in r.data.decode("utf-8").lower()


def test_rota_valor_base_invalido_avisa(app, client, login, post_csrf, cenario):
    with app.app_context():
        _criar_linha(tipo_custa="distribuicao_civel", percentual=Decimal("1.5"))
    login("adv@custas.com")
    r = post_csrf(f"/processos/{cenario['processo_id']}/custas", {
        "tipo_custa": "distribuicao_civel", "valor_base": "não é um número",
    }, get_url=f"/processos/{cenario['processo_id']}")
    assert r.status_code == 200
    assert CalculoCustas.query.filter_by(processo_id=cenario["processo_id"]).count() == 0
