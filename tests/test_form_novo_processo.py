"""
Verifica a nova experiência da tela "Novo processo" (PENDENCIAS.md, seção
-55): a princípio só aparecem o número CNJ e o botão de busca (Passo 1) —
o resto do formulário fica escondido (`#novo-processo-resto`,
`display:none`) até uma busca rodar ou o usuário clicar em "prosseguir sem
buscar". Campos que a busca por CNJ pode preencher (área do direito, tipo
de ação, instância, comarca, vara, valor da causa, data de distribuição,
descrição) ficam marcados como "lockable" (têm um lápis ao lado, escondido
até o JS travar o campo depois de preenchê-lo) — a trava em si é 100% no
JS/CSS (não dá pra testar via requisição HTTP), então aqui confirma-se só
a estrutura HTML que sustenta esse comportamento.

Também confirma que a tela "Editar processo" (que reusa o mesmo template)
continua mostrando tudo de cara, sem o Passo 1/Passo 2 — e que o cadastro
manual (sem buscar nada) continua funcionando normalmente, provando que a
reforma da tela foi só de apresentação, sem mudar nada da rota/modelo.
"""
import pytest

from app.extensions import db
from app.models import Cliente, Processo


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    adv_id = criar_usuario(unidade_id, "advform@teste.com", papel="advogado", nome="Advogado")
    cliente = Cliente(nome="Cliente Formulário", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.commit()
    return dict(adv_id=adv_id, cliente_id=cliente.id, unidade_id=unidade_id)


def test_novo_processo_mostra_so_passo_1_a_principio(client, login, cenario):
    login("advform@teste.com")
    r = client.get("/processos/novo")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")

    # Passo 1 (sempre visível) e Passo 2 (escondido até buscar/pular)
    assert 'id="novo-processo-passo1"' in corpo
    assert 'id="btn-buscar-cnj"' in corpo
    assert 'id="btn-preencher-manual"' in corpo
    assert 'id="novo-processo-resto" style="display:none;"' in corpo

    # Campos que a busca por CNJ pode preencher têm o lápis (escondido por
    # CSS até o JS travar o campo — ver .btn-lapis/.tem-valor-travado).
    assert corpo.count('class="btn-lapis"') >= 7  # área, tipo_ação, instância, comarca, vara, valor, data, descrição

    # Campos que o DataJud nunca preenche não têm lápis nenhum perto deles
    # — só confirma que a tela ainda pede cliente/número interno/etc.
    assert 'name="cliente_id"' in corpo
    assert 'name="numero_interno"' in corpo


def test_editar_processo_continua_mostrando_tudo_de_cara(client, login, cenario):
    processo = Processo(area_direito="Cível", unidade_id=cenario["unidade_id"], cliente_id=cenario["cliente_id"])
    db.session.add(processo)
    db.session.commit()

    login("advform@teste.com")
    r = client.get(f"/processos/{processo.id}/editar")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")

    # Tela de edição não usa o fluxo Passo 1/Passo 2 — tudo aparece de cara.
    assert 'id="novo-processo-passo1"' not in corpo
    assert 'id="novo-processo-resto"' not in corpo
    assert 'value="Cível"' in corpo


def test_cadastro_manual_sem_buscar_continua_funcionando(client, post_csrf, login, cenario):
    login("advform@teste.com")
    r = post_csrf("/processos/novo", data={
        "numero_processo": "",
        "numero_interno": "INT-001",
        "cliente_id": str(cenario["cliente_id"]),
        "area_direito": "Trabalhista",
        "tipo_acao": "",
        "fase": "",
        "instancia": "",
        "comarca": "",
        "vara": "",
        "tribunal_datajud": "",
        "polo_cliente": "Autor",
        "parte_contraria": "",
        "advogado_contrario": "",
        "valor_causa": "",
        "data_distribuicao": "",
        "responsavel_id": "",
        "descricao": "",
    }, get_url="/processos/novo")
    assert r.status_code == 200

    processo = Processo.query.filter_by(numero_interno="INT-001").first()
    assert processo is not None
    assert processo.area_direito == "Trabalhista"
    assert processo.forma_acompanhamento == "manual"  # sem número CNJ, nunca tenta buscar sozinho
