"""
Testa a due diligence de cliente novo (PENDENCIAS.md, seção -53) — busca
de processo por CPF/CNPJ/nome em todo o Brasil, distinta da verificação
de conflito de interesses (que só cruza contra clientes já cadastrados
neste escritório). Sem nenhum provedor pago contratado (Judit/Escavador/
Digesto/Codilo/Jusbrasil Soluções), a tela explica isso com clareza em
vez de fingir uma busca que não roda — e o ponto de extensão
(`ConectorCaptura.buscar_processos_por_parte`) já funciona de ponta a
ponta assim que QUALQUER conector o implementar (testado aqui com um
conector falso, sem precisar de credencial real de nenhum provedor).
"""
from datetime import date

import pytest

from app.extensions import db
from app.models import Cliente, LogAtividade
from app.utils.captura_conectores import (
    ConectorCaptura, ConectorNaoConfiguradoError, ProcessoEncontradoDueDiligence, obter_conector,
)
from app.utils.conector_datajud import ConectorDataJud, FuncionalidadeNaoDisponivelError


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    admin_id = criar_usuario(unidade_id, "admindd@teste.com", papel="admin", nome="Admin")
    gestor_id = criar_usuario(unidade_id, "gestordd@teste.com", papel="gestor", nome="Gestor")
    adv_id = criar_usuario(unidade_id, "advdd@teste.com", papel="advogado", nome="Advogado")

    cliente = Cliente(nome="Cliente Due Diligence", cpf_cnpj="111.222.333-44", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.commit()

    return dict(admin_id=admin_id, gestor_id=gestor_id, adv_id=adv_id, cliente_id=cliente.id)


# ---------- interface / conector padrão (DataJud) ----------

def test_conector_datajud_recusa_busca_por_parte():
    conector = ConectorDataJud(api_key="qualquer")
    with pytest.raises(FuncionalidadeNaoDisponivelError):
        conector.buscar_processos_por_parte(cpf_cnpj="111.222.333-44")


def test_obter_conector_due_diligence_sem_provedor_lista_opcoes(app):
    with app.app_context():
        with pytest.raises(ConectorNaoConfiguradoError) as exc:
            obter_conector("due_diligence")
    msg = str(exc.value)
    for provedor in ("Judit", "Escavador", "Digesto", "Codilo", "Jusbrasil Soluções"):
        assert provedor in msg


# ---------- rota ----------

def test_tela_mostra_explicacao_sem_provedor_configurado(client, login, cenario):
    login("admindd@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/due-diligence")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "Judit" in corpo and "Jusbrasil Soluções" in corpo
    assert LogAtividade.query.filter_by(acao="consultou_due_diligence").count() == 0, \
        "sem provedor configurado, nenhuma busca de verdade aconteceu — não deveria logar"


def test_gestor_tambem_acessa(client, login, cenario):
    login("gestordd@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/due-diligence")
    assert r.status_code == 200


def test_advogado_comum_recebe_403(client, login, cenario):
    login("advdd@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/due-diligence")
    assert r.status_code == 403


def test_com_conector_configurado_mostra_resultados_e_loga(app, client, login, cenario, monkeypatch):
    """
    Simula um provedor JÁ contratado (sem precisar de credencial real de
    nenhum provedor de verdade) pra provar que a tela e o log funcionam de
    ponta a ponta assim que `obter_conector("due_diligence")` devolver
    algo de verdade — é exatamente o que vai acontecer quando um provedor
    pago for contratado e implementado.
    """
    class ConectorFake(ConectorCaptura):
        nome_fonte = "fake_due_diligence"

        def consultar_processo(self, numero_cnj):
            raise NotImplementedError

        def monitorar_publicacoes_por_oab(self, numero_oab, uf):
            raise NotImplementedError

        def buscar_processos_por_parte(self, cpf_cnpj=None, nome=None):
            return [
                ProcessoEncontradoDueDiligence(
                    numero_processo="0001234-56.2024.8.26.0100", tribunal="TJSP", classe="Execução Fiscal",
                    assunto="Dívida Ativa", situacao="ativo", data_distribuicao=date(2024, 3, 10),
                    polo_da_parte_buscada="réu", fonte="fake_due_diligence",
                )
            ]

    import app.routes.clientes as mod
    monkeypatch.setattr(mod, "obter_conector", lambda nome_fonte, empresa=None: ConectorFake())

    login("admindd@teste.com")
    r = client.get(f"/clientes/{cenario['cliente_id']}/due-diligence")
    assert r.status_code == 200
    corpo = r.data.decode("utf-8")
    assert "0001234-56.2024.8.26.0100" in corpo
    assert "TJSP" in corpo

    log = LogAtividade.query.filter_by(acao="consultou_due_diligence", entidade_id=cenario["cliente_id"]).first()
    assert log is not None
    assert log.usuario_id == cenario["admin_id"]
