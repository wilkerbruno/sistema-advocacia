"""
Bug relatado pelo usuário: uma despesa criada JÁ como "pago" direto no
formulário "Novo lançamento" (em vez de ser marcada como paga depois, pela
ação rápida "Marcar pago" da listagem) não aparecia em NENHUM card de
resumo — nem "Recebido este mês"/"Repassado este mês" (que filtram por
mês/ano de `data_pagamento`), porque o formulário não tinha campo de data
de pagamento nenhum, então `data_pagamento` ficava sempre `None` mesmo com
`status="pago"`.

`financeiro.atualizar_status()` (a ação rápida da listagem) já preenchia
`data_pagamento` automaticamente com a data de hoje quando faltava — só a
criação direta (`financeiro.novo()`) não tinha essa mesma lógica. Corrigido
pra ter o mesmo comportamento nos dois caminhos, e o formulário ganhou um
campo explícito "Data de pagamento" pra quem quiser informar uma data
específica (ex: lançando uma despesa que já foi paga há alguns dias).
"""
from datetime import date

import pytest

from app.extensions import db
from app.models import Lancamento
from tests.conftest import extrair_csrf


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admin@datapagamento.com", papel="admin", nome="Admin Data Pagamento")
    return dict(admin_id=admin_id, unidade_id=unidade_id)


def _post(client, data, get_url="/financeiro/novo"):
    r = client.get(get_url)
    token = extrair_csrf(r.data.decode("utf-8"))
    payload = dict(data)
    payload["csrf_token"] = token
    return client.post("/financeiro/novo", data=payload, follow_redirects=True)


def test_despesa_terceiros_criada_ja_paga_sem_informar_data_usa_hoje(client, login, cenario):
    login("admin@datapagamento.com")
    r = _post(client, {
        "descricao": "Custo de aquisição de licenças de API", "valor": "1289.02", "natureza": "despesa",
        "status": "pago", "unidade_id": str(cenario["unidade_id"]), "conta_terceiros": "1",
    })
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Custo de aquisição de licenças de API").first()
    assert lanc is not None
    assert lanc.status == "pago"
    assert lanc.data_pagamento == date.today(), \
        "data_pagamento deveria ter sido preenchida automaticamente com hoje, igual atualizar_status() já fazia"


def test_despesa_terceiros_paga_aparece_em_repassado_este_mes(client, login, cenario):
    login("admin@datapagamento.com")
    _post(client, {
        "descricao": "Repasse já pago sem data explícita", "valor": "1289.02", "natureza": "despesa",
        "status": "pago", "unidade_id": str(cenario["unidade_id"]), "conta_terceiros": "1",
    })

    html = client.get("/financeiro/?conta=terceiros").data.decode("utf-8")
    idx = html.index("Repassado este mês")
    trecho = html[idx:idx + 300]
    assert "1.289,02" in trecho


def test_receita_operacional_criada_ja_paga_sem_informar_data_usa_hoje(client, login, cenario):
    login("admin@datapagamento.com")
    r = _post(client, {
        "descricao": "Honorário recebido na hora", "valor": "500.00", "natureza": "receita",
        "status": "pago", "unidade_id": str(cenario["unidade_id"]),
    })
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Honorário recebido na hora").first()
    assert lanc.data_pagamento == date.today()

    html = client.get("/financeiro/").data.decode("utf-8")
    idx = html.index("Recebido este mês")
    trecho = html[idx:idx + 300]
    assert "500,00" in trecho


def test_data_pagamento_explicita_no_formulario_e_respeitada(client, login, cenario):
    """Quando a pessoa informa a data de pagamento explicitamente (campo
    novo no formulário), essa data é a que vale — o auto-preenchimento só
    entra quando o campo fica em branco."""
    login("admin@datapagamento.com")
    outra_data = date(date.today().year, 1, 15) if date.today().month != 1 else date(date.today().year - 1, 1, 15)
    r = _post(client, {
        "descricao": "Pago em data específica", "valor": "42.00", "natureza": "despesa",
        "status": "pago", "unidade_id": str(cenario["unidade_id"]),
        "data_pagamento": outra_data.isoformat(),
    })
    assert r.status_code == 200

    lanc = Lancamento.query.filter_by(descricao="Pago em data específica").first()
    assert lanc.data_pagamento == outra_data


def test_lancamento_pendente_continua_sem_data_pagamento(client, login, cenario):
    """Não pode virar regressão pro caso comum (status pendente, sem data
    de pagamento nenhuma ainda) — continua None, como sempre foi."""
    login("admin@datapagamento.com")
    _post(client, {
        "descricao": "Ainda pendente", "valor": "10.00", "natureza": "receita",
        "status": "pendente", "unidade_id": str(cenario["unidade_id"]),
    })
    lanc = Lancamento.query.filter_by(descricao="Ainda pendente").first()
    assert lanc.data_pagamento is None
