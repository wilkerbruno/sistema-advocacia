"""
Pipeline de triagem da vigilância do Diário Oficial da União (DOU) — ver
app/utils/captura_dou_pipeline.py. Cobertura:
  1. _normalizar / _regex_cnpj: normalização de texto e tolerância a
     pontuação/espaço no CNPJ.
  2. _termos_monitorados: escopo por unidade, exclui cliente inativo/
     anonimizado, exclui palavra-chave inativa, respeita TAMANHO_MINIMO_TERMO.
  3. processar_materia_capturada: match por nome, match por CNPJ (várias
     formatações), dedup em reprocessamento, múltiplos termos na mesma
     matéria, nunca cruza unidade, recorte do trecho.
"""
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, Cliente, PalavraChaveDou, PublicacaoDouCapturada
from app.utils.captura_dou_pipeline import (
    _normalizar, _regex_cnpj, _termos_monitorados, processar_materia_capturada,
    TAMANHO_MINIMO_TERMO,
)

SENHA = "senha123"


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


def _materia(id_materia_fonte="mat-1", titulo="", ementa="", texto_plano="", orgao="", secao="DO1"):
    return {
        "id_materia_fonte": id_materia_fonte, "secao": secao, "orgao": orgao,
        "titulo": titulo, "ementa": ementa, "subtitulo": "", "texto_plano": texto_plano,
        "data_publicacao_str": "22/09/2026", "data_publicacao_fallback": date(2026, 9, 22),
    }


# ---------------------------------------------------------------------------
# 1. _normalizar / _regex_cnpj
# ---------------------------------------------------------------------------

def test_normalizar_remove_acento_e_colapsa_espacos():
    assert _normalizar("  José   da  Silva  ") == "jose da silva"
    assert _normalizar("ÓRGÃO NACIONAL") == "orgao nacional"
    assert _normalizar(None) == ""
    assert _normalizar("") == ""


def test_regex_cnpj_bate_com_pontuacao_padrao():
    regex = _regex_cnpj("12345678000190")
    assert regex.search("CNPJ: 12.345.678/0001-90 vencido")
    assert regex.search("12345678000190")
    assert regex.search("12 345 678 0001 90")


def test_regex_cnpj_nao_bate_com_digitos_fora_de_ordem():
    regex = _regex_cnpj("12345678000190")
    assert not regex.search("09100087654321")


def test_regex_cnpj_nao_concatena_numeros_vizinhos_nao_relacionados():
    """Regressão do risco documentado: números de processo/data adjacentes
    não devem "colar" e formar um falso positivo de CNPJ."""
    regex = _regex_cnpj("12345678000190")
    texto = "Processo nº 123 de 2026, protocolo 45678, item 000190 do edital."
    assert not regex.search(texto)


# ---------------------------------------------------------------------------
# 2. _termos_monitorados
# ---------------------------------------------------------------------------

@pytest.fixture()
def cenario_duas_unidades(app):
    empresa_a, unidade_a = _criar_empresa_unidade("EmpresaA", "UNA")
    empresa_b, unidade_b = _criar_empresa_unidade("EmpresaB", "UNB")

    cliente_a = Cliente(nome="Construtora Alfa Ltda", unidade_id=unidade_a.id, cpf_cnpj="12.345.678/0001-90",
                         tipo_pessoa="PJ")
    cliente_a_inativo = Cliente(nome="Empresa Inativa SA", unidade_id=unidade_a.id, ativo=False, tipo_pessoa="PJ")
    cliente_a_anonimizado = Cliente(nome="Anonimo", unidade_id=unidade_a.id, tipo_pessoa="PF")
    from datetime import datetime
    cliente_a_anonimizado.anonimizado_em = datetime.utcnow()
    cliente_a_curto = Cliente(nome="Ana", unidade_id=unidade_a.id, tipo_pessoa="PF")  # abaixo do mínimo
    cliente_b = Cliente(nome="Beta Comércio Ltda", unidade_id=unidade_b.id, cpf_cnpj="98.765.432/0001-10",
                         tipo_pessoa="PJ")
    db.session.add_all([cliente_a, cliente_a_inativo, cliente_a_anonimizado, cliente_a_curto, cliente_b])
    db.session.flush()

    palavra_ativa = PalavraChaveDou(unidade_id=unidade_a.id, termo="Lei 14.133/2021", ativa=True)
    palavra_inativa = PalavraChaveDou(unidade_id=unidade_a.id, termo="termo desativado", ativa=False)
    db.session.add_all([palavra_ativa, palavra_inativa])
    db.session.commit()

    return dict(unidade_a_id=unidade_a.id, unidade_b_id=unidade_b.id, cliente_a_id=cliente_a.id,
                cliente_b_id=cliente_b.id)


def test_termos_monitorados_inclui_clientes_ativos_e_palavras_ativas(app, cenario_duas_unidades):
    with app.app_context():
        termos = _termos_monitorados()
        originais = {(t[0], t[2], t[4]) for t in termos}

        assert (cenario_duas_unidades["unidade_a_id"], "Construtora Alfa Ltda", False) in originais
        assert (cenario_duas_unidades["unidade_a_id"], "12.345.678/0001-90", True) in originais
        assert (cenario_duas_unidades["unidade_a_id"], "Lei 14.133/2021", False) in originais
        assert (cenario_duas_unidades["unidade_b_id"], "Beta Comércio Ltda", False) in originais


def test_termos_monitorados_exclui_inativo_anonimizado_curto_e_palavra_inativa(app, cenario_duas_unidades):
    with app.app_context():
        termos = _termos_monitorados()
        originais_nome = {t[2] for t in termos}
        assert "Empresa Inativa SA" not in originais_nome
        assert "Anonimo" not in originais_nome
        assert "Ana" not in originais_nome
        assert "termo desativado" not in originais_nome
        assert len("ana") < TAMANHO_MINIMO_TERMO or "Ana" not in originais_nome


# ---------------------------------------------------------------------------
# 3. processar_materia_capturada
# ---------------------------------------------------------------------------

def test_processar_materia_bate_por_nome_de_cliente(app, cenario_duas_unidades):
    with app.app_context():
        materia = _materia(
            id_materia_fonte="mat-nome",
            titulo="Homologação de licitação",
            texto_plano="A empresa Construtora Alfa Ltda venceu o certame nº 45/2026 para reforma predial.",
        )
        criadas = processar_materia_capturada(materia)
        db.session.commit()

        assert len(criadas) == 1
        pub = criadas[0]
        assert pub.unidade_id == cenario_duas_unidades["unidade_a_id"]
        assert pub.cliente_id == cenario_duas_unidades["cliente_a_id"]
        assert pub.termo_encontrado == "Construtora Alfa Ltda"
        assert "Construtora Alfa Ltda" in pub.texto_trecho


def test_processar_materia_bate_por_cnpj_com_formatacao_diferente(app, cenario_duas_unidades):
    with app.app_context():
        materia = _materia(
            id_materia_fonte="mat-cnpj",
            texto_plano="Fica autorizada a empresa inscrita no CNPJ 12345678000190 a prestar o serviço.",
        )
        criadas = processar_materia_capturada(materia)
        db.session.commit()

        assert len(criadas) == 1
        assert criadas[0].cliente_id == cenario_duas_unidades["cliente_a_id"]
        assert criadas[0].termo_encontrado == "12.345.678/0001-90"


def test_processar_materia_nao_bate_no_nada_devolve_lista_vazia(app, cenario_duas_unidades):
    with app.app_context():
        materia = _materia(id_materia_fonte="mat-nada", texto_plano="Assunto totalmente alheio ao escritório.")
        criadas = processar_materia_capturada(materia)
        assert criadas == []


def test_processar_materia_dedup_em_reprocessamento(app, cenario_duas_unidades):
    with app.app_context():
        materia = _materia(id_materia_fonte="mat-dup",
                            texto_plano="Construtora Alfa Ltda é citada nesta portaria.")
        primeira = processar_materia_capturada(materia)
        db.session.commit()
        assert len(primeira) == 1

        segunda = processar_materia_capturada(materia)
        db.session.commit()
        assert segunda == []
        assert PublicacaoDouCapturada.query.filter_by(id_materia_fonte="mat-dup").count() == 1


def test_processar_materia_multiplos_termos_mesma_materia(app, cenario_duas_unidades):
    with app.app_context():
        materia = _materia(
            id_materia_fonte="mat-multi",
            texto_plano=(
                "Construtora Alfa Ltda e a empresa cadastrada no CNPJ 98.765.432/0001-10 "
                "firmaram convênio conjunto, conforme a Lei 14.133/2021."
            ),
        )
        criadas = processar_materia_capturada(materia)
        db.session.commit()

        termos_encontrados = {c.termo_encontrado for c in criadas}
        assert "Construtora Alfa Ltda" in termos_encontrados
        assert "98.765.432/0001-10" in termos_encontrados
        assert "Lei 14.133/2021" in termos_encontrados
        # nunca cruza unidade: cada PublicacaoDouCapturada foi criada na unidade certa
        unidades_das_criadas = {c.unidade_id for c in criadas}
        assert cenario_duas_unidades["unidade_a_id"] in unidades_das_criadas
        assert cenario_duas_unidades["unidade_b_id"] in unidades_das_criadas


def test_processar_materia_sem_id_materia_fonte_devolve_vazio(app, cenario_duas_unidades):
    with app.app_context():
        materia = _materia(id_materia_fonte=None, texto_plano="Construtora Alfa Ltda")
        assert processar_materia_capturada(materia) == []


def test_processar_materia_recorta_trecho_ao_redor_do_termo(app, cenario_duas_unidades):
    with app.app_context():
        texto_longo = ("x " * 500) + "Construtora Alfa Ltda apareceu bem no meio do texto." + (" y" * 500)
        materia = _materia(id_materia_fonte="mat-trecho", texto_plano=texto_longo)
        criadas = processar_materia_capturada(materia)
        db.session.commit()

        assert len(criadas) == 1
        trecho = criadas[0].texto_trecho
        assert "Construtora Alfa Ltda" in trecho
        assert len(trecho) < len(texto_longo)
