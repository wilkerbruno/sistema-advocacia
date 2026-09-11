"""
Conector PJe-JT público (22 TRTs — sem certificado/token) —
PENDENCIAS.md, seção -90. Mesma cobertura de espírito de
test_conector_pje_publico.py, adaptada pra uma API REST JSON em vez de
formulário JSF: parsing do payload "encontrado", rejeição de número CNJ
de outro segmento, "não encontrado" (204) em todos os candidatos,
fallback de 1º pra 2º grau, indisponibilidade de rede, e a rota que
reaproveita o pipeline de captura marcando origem_captura="pje_jt_publico".

Os nomes de campo do payload "encontrado" usados nos fixtures abaixo
vêm da leitura do JS compilado do próprio app do TRT-1 (ver aviso
completo em app/utils/conector_pje_jt_publico.py sobre isto não estar
confirmado contra uma resposta real populada).
"""
from unittest.mock import patch, MagicMock

import pytest
import requests

from app.extensions import db
from app.models import Processo, LogCaptura, Movimentacao
from app.utils import conector_pje_jt_publico as mod
from app.utils.cnj import calcular_digito_verificador

PROCESSO_ENCONTRADO = {
    "classe": "RECLAMAÇÃO TRABALHISTA",
    "numero": "0001234-56.2023.5.01.0001",
    "orgaoJulgador": "1ª Vara do Trabalho de Niterói",
    "orgaoJulgadorColegiado": None,
    "poloAtivo": [{"nome": "Joao da Silva"}],
    "poloPassivo": [{"nome": "Empresa XYZ Ltda"}],
    "movimentos": [
        {"descricao": "Distribuído por sorteio", "atualizadoEm": "2023-04-20T10:00:00"},
        {"descricao": "Juntada de petição de contestação", "atualizadoEm": "2023-04-25T11:30:00"},
    ],
}


def _numero_cnj(segmento, tribunal_codigo, sequencial="1234567", ano="2023", origem="0001"):
    dv = calcular_digito_verificador(sequencial, ano, segmento, tribunal_codigo, origem)
    return f"{sequencial}-{dv}.{ano}.{segmento}.{tribunal_codigo}.{origem}"


def _numero_cnj_trabalho(tribunal_codigo="99"):
    """Código de tribunal fictício de propósito — este conector tenta os
    22 candidatos sem tentar adivinhar o TRT certo pelo número (mesma
    disciplina de app/utils/conector_pje_publico.py)."""
    return _numero_cnj("5", tribunal_codigo)


def _resp(status=204, corpo=None):
    r = MagicMock()
    r.status_code = status
    if corpo is not None:
        r.json.return_value = corpo
    else:
        r.json.side_effect = ValueError("no body")
    return r


def _mock_fluxo(por_dominio):
    """por_dominio: dict domínio-substring -> {"1": (status, corpo|None), "2": (status, corpo|None)}
    — domínios não listados devolvem 204 (não encontrado) em qualquer grau,
    igual ao comportamento real de um TRT onde o processo não está."""

    def _get(self, url, headers=None, **kwargs):
        grau = (headers or {}).get("X-Grau-Instancia")
        for dominio, cfg in por_dominio.items():
            if dominio in url:
                status, corpo = cfg.get(grau, (204, None))
                return _resp(status=status, corpo=corpo)
        return _resp(status=204)

    return _get


# ---------- Parsing (sem rede) ----------

def test_parse_processo_completo():
    resultado = mod._parse_processo(PROCESSO_ENCONTRADO, "12345678920235010001", "trt1", "1º grau")
    assert resultado["classe"] == "RECLAMAÇÃO TRABALHISTA"
    assert resultado["orgao_julgador"] == "1ª Vara do Trabalho de Niterói"
    assert resultado["tribunal_slug"] == "trt1"
    assert resultado["sistema"] == "PJe-JT"
    assert resultado["instancia"] == "1º grau"
    assert resultado["comarca"] is None

    partes = resultado["partes"]
    assert len(partes) == 2
    assert partes[0] == {"tipo": "Polo Ativo", "nome": "Joao da Silva", "advogados": []}
    assert partes[1]["tipo"] == "Polo Passivo"
    assert partes[1]["nome"] == "Empresa XYZ Ltda"
    assert partes[1]["advogados"] == []

    movs = resultado["movimentacoes"]
    assert len(movs) == 2
    assert movs[0].texto_integral == "Distribuído por sorteio"
    assert movs[0].codigo_tpu is None
    assert movs[0].data.year == 2023 and movs[0].data.month == 4 and movs[0].data.day == 20


def test_parse_junta_orgao_colegiado_quando_presente():
    dados = dict(PROCESSO_ENCONTRADO, orgaoJulgadorColegiado="1ª Turma")
    resultado = mod._parse_processo(dados, "12345678920235010001", "trt1", "2º grau")
    assert resultado["orgao_julgador"] == "1ª Turma - 1ª Vara do Trabalho de Niterói"


def test_rejeita_numero_cnj_de_outro_segmento():
    conector = mod.ConectorPjeJtPublico()
    with pytest.raises(mod.ErroPjeJtPublico, match="só sabe consultar a Justiça do Trabalho"):
        conector.consultar_processo("1234567-89.2023.8.19.0001")


def test_metodos_nao_cobertos_levantam_notimplemented():
    conector = mod.ConectorPjeJtPublico()
    with pytest.raises(NotImplementedError):
        conector.monitorar_publicacoes_por_oab("123456", "RJ")
    with pytest.raises(NotImplementedError):
        conector.buscar_processos_por_parte(nome="Fulano")


# ---------- Fluxo completo (com rede mockada) ----------

def test_consulta_publica_sem_nenhuma_credencial():
    numero = _numero_cnj_trabalho()
    respostas = {"pje.trt1.jus.br": {"1": (200, [PROCESSO_ENCONTRADO])}}
    _get = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get):
        resultado = mod.ConectorPjeJtPublico().consultar_processo(numero)
    assert resultado["tribunal_slug"] == "trt1"
    assert resultado["instancia"] == "1º grau"


def test_tenta_proximo_tribunal_ate_achar():
    numero = _numero_cnj_trabalho()
    dados_trt2 = dict(PROCESSO_ENCONTRADO, classe="EXECUÇÃO DE TÍTULO EXTRAJUDICIAL")
    respostas = {
        "pje.trt1.jus.br": {"1": (204, None), "2": (204, None)},
        "pje.trt2.jus.br": {"1": (200, [dados_trt2])},
    }
    _get = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get):
        resultado = mod.ConectorPjeJtPublico().consultar_processo(numero)
    assert resultado["tribunal_slug"] == "trt2"
    assert resultado["classe"] == "EXECUÇÃO DE TÍTULO EXTRAJUDICIAL"


def test_fallback_para_segundo_grau():
    numero = _numero_cnj_trabalho()
    respostas = {
        "pje.trt1.jus.br": {"1": (204, None), "2": (200, [PROCESSO_ENCONTRADO])},
    }
    _get = _mock_fluxo(respostas)
    with patch.object(requests.Session, "get", _get):
        resultado = mod.ConectorPjeJtPublico().consultar_processo(numero)
    assert resultado["tribunal_slug"] == "trt1"
    assert resultado["instancia"] == "2º grau"


def test_numero_com_digito_invalido_400_conta_como_nao_encontrado():
    numero = _numero_cnj_trabalho()

    def _get(self, url, headers=None, **kwargs):
        return _resp(status=400, corpo={"codigoErro": "ARQ-033", "mensagem": "dígitos inválidos"})

    with patch.object(requests.Session, "get", _get):
        with pytest.raises(mod.ErroPjeJtPublico, match="não encontrado em nenhum dos 22 TRTs"):
            mod.ConectorPjeJtPublico().consultar_processo(numero)


def test_nao_encontrado_em_nenhum_trt():
    numero = _numero_cnj_trabalho()
    with patch.object(requests.Session, "get", _mock_fluxo({})):
        with pytest.raises(mod.ErroPjeJtPublico, match="não encontrado em nenhum dos 22 TRTs") as exc:
            mod.ConectorPjeJtPublico().consultar_processo(numero)
    assert "segredo de justiça" in str(exc.value)


def test_erro_de_rede_vira_pje_jt_indisponivel():
    numero = _numero_cnj_trabalho()

    def _get_falha(self, url, **kwargs):
        raise requests.RequestException("timeout")

    with patch.object(requests.Session, "get", _get_falha):
        with pytest.raises(mod.PjeJtIndisponivelError):
            mod.ConectorPjeJtPublico().consultar_processo(numero)


def test_resposta_nao_json_nao_quebra():
    numero = _numero_cnj_trabalho()

    def _get(self, url, headers=None, **kwargs):
        if "pje.trt1.jus.br" in url:
            r = MagicMock()
            r.status_code = 200
            r.json.side_effect = ValueError("not json")
            return r
        return _resp(status=204)

    with patch.object(requests.Session, "get", _get):
        with pytest.raises(mod.ErroPjeJtPublico, match="não encontrado em nenhum dos 22 TRTs"):
            mod.ConectorPjeJtPublico().consultar_processo(numero)


def test_candidatos_tem_22_trts_e_exclui_trt3_e_trt23():
    slugs = {t.slug for t in mod.CANDIDATOS}
    assert len(mod.CANDIDATOS) == 22
    assert "trt3" not in slugs
    assert "trt23" not in slugs


# ---------- Rota (reaproveita o pipeline de captura) ----------

@pytest.fixture()
def processo_trt1(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin-pjejt@teste.com", papel="admin", nome="Admin PjeJt")
    from app.models import Cliente
    cliente = Cliente(nome="Cliente Teste PJe-JT", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()
    processo = Processo(
        numero_processo="0001234-56.2023.5.01.0001", cliente_id=cliente.id,
        unidade_id=unidade_id, area_direito="Trabalhista", status="ativo",
        monitoravel=False, forma_acompanhamento=None,
        motivo_nao_monitoravel="Sem conector confiável ainda",
    )
    db.session.add(processo)
    db.session.commit()
    return {"processo_id": processo.id, "admin_id": admin_id}


def test_rota_tentar_captura_pje_jt_sucesso(client, login, post_csrf, processo_trt1):
    login("admin-pjejt@teste.com")
    with patch.object(mod.ConectorPjeJtPublico, "consultar_processo",
                       return_value=mod._parse_processo(PROCESSO_ENCONTRADO, "12345678920235010001", "trt1", "1º grau")):
        r = post_csrf(f"/governanca/processos/{processo_trt1['processo_id']}/tentar-captura-pje-jt", {},
                      get_url=f"/processos/{processo_trt1['processo_id']}")
    assert r.status_code == 200

    processo = db.session.get(Processo, processo_trt1["processo_id"])
    assert processo.classe_processual == "RECLAMAÇÃO TRABALHISTA"

    movs = Movimentacao.query.filter_by(processo_id=processo.id).all()
    assert len(movs) == 2
    assert all(m.origem_captura == "pje_jt_publico" for m in movs)

    log = LogCaptura.query.filter_by(processo_id=processo.id, fonte="pje_jt_publico").first()
    assert log is not None
    assert log.status == "sucesso"
    assert log.tribunal == "trt1"


def test_rota_tentar_captura_pje_jt_falha_registra_log(client, login, post_csrf, processo_trt1):
    login("admin-pjejt@teste.com")
    with patch.object(mod.ConectorPjeJtPublico, "consultar_processo",
                       side_effect=mod.PjeJtIndisponivelError("PJe-JT fora do ar")):
        r = post_csrf(f"/governanca/processos/{processo_trt1['processo_id']}/tentar-captura-pje-jt", {},
                      get_url=f"/processos/{processo_trt1['processo_id']}")
    assert r.status_code == 200

    log = LogCaptura.query.filter_by(processo_id=processo_trt1["processo_id"], fonte="pje_jt_publico").first()
    assert log is not None
    assert log.status == "falha"
