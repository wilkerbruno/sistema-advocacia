"""
Captação de clientes (CRM de pipeline) — ver app/models/lead.py e
app/routes/leads.py pro contexto completo do pedido (usuário perguntou se
valia trazer algo do concorrente DeskcommCRM; a resposta foi um pipeline
manual de leads, sem automação de WhatsApp/IA nesta rodada). Estes testes
cobrem: isolamento multi-tenant (mesmo padrão de
test_isolamento_multi_tenant_prazos.py), o fluxo normal de cadastro/mover
etapa, e a conversão em Cliente de verdade.
"""
from datetime import date, timedelta

from app.extensions import db
from app.models import Empresa, Licenca, Unidade, Usuario, Lead, Cliente

SENHA = "senha123"


def _criar_empresa(nome, codigo):
    empresa = Empresa(nome=nome, dono_da_plataforma=False)
    db.session.add(empresa)
    db.session.flush()
    db.session.add(Licenca(empresa_id=empresa.id, plano="mensal", valor_negociado=100, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade = Unidade(nome=f"Matriz {nome}", codigo=codigo, empresa_id=empresa.id)
    db.session.add(unidade)
    db.session.flush()
    return empresa, unidade


def _criar_usuario_e_logar(unidade_id, email, papel, login):
    u = Usuario(email=email, unidade_id=unidade_id, papel=papel, nome=email.split("@")[0])
    u.set_senha(SENHA)
    db.session.add(u)
    db.session.commit()
    login(email)
    return u


def test_cadastro_de_lead_grava_na_unidade_do_usuario(client, login, post_csrf, app):
    _, unidade = _criar_empresa("Escritório A", "A1")
    _criar_usuario_e_logar(unidade.id, "adv@leadsteste.com", "advogado", login)

    resp = post_csrf("/leads/novo", {"nome": "Maria Interessada", "telefone": "11999990000",
                                      "area_interesse": "Trabalhista", "origem": "whatsapp"})

    assert resp.status_code == 200
    lead = Lead.query.filter_by(nome="Maria Interessada").first()
    assert lead is not None
    assert lead.unidade_id == unidade.id
    assert lead.etapa == Lead.ETAPA_NOVO


def test_cadastro_sem_nome_e_recusado(client, login, post_csrf, app):
    _, unidade = _criar_empresa("Escritório B", "B1")
    _criar_usuario_e_logar(unidade.id, "adv2@leadsteste.com", "advogado", login)

    resp = post_csrf("/leads/novo", {"nome": "  "})

    assert resp.status_code == 200
    assert Lead.query.count() == 0


def test_admin_de_empresa_nao_ve_lead_de_outra_empresa(client, login, app):
    """Mesma regra de isolamento de test_isolamento_multi_tenant_prazos.py
    — um admin só pode ver/mexer nos leads da PRÓPRIA empresa."""
    empresa_a, unidade_a = _criar_empresa("Empresa A", "EA1")
    empresa_b, unidade_b = _criar_empresa("Empresa B", "EB1")

    lead_a = Lead(nome="Lead da Empresa A", unidade_id=unidade_a.id)
    lead_b = Lead(nome="Lead da Empresa B", unidade_id=unidade_b.id)
    db.session.add_all([lead_a, lead_b])
    db.session.commit()

    _criar_usuario_e_logar(unidade_a.id, "admina@leadsteste.com", "admin", login)

    html = client.get("/leads/").data.decode("utf-8")
    assert "Lead da Empresa A" in html
    assert "Lead da Empresa B" not in html

    resp_proibido = client.get(f"/leads/{lead_b.id}")
    assert resp_proibido.status_code == 403


def test_mover_etapa_atualiza_lead(client, login, post_csrf, app):
    _, unidade = _criar_empresa("Escritório C", "C1")
    _criar_usuario_e_logar(unidade.id, "adv3@leadsteste.com", "advogado", login)
    lead = Lead(nome="Contato C", unidade_id=unidade.id)
    db.session.add(lead)
    db.session.commit()

    resp = post_csrf(f"/leads/{lead.id}/mover", {"etapa": "qualificando"}, get_url=f"/leads/{lead.id}")

    assert resp.status_code == 200
    lead_recarregado = db.session.get(Lead, lead.id)
    assert lead_recarregado.etapa == "qualificando"


def test_mover_para_perdido_salva_motivo(client, login, post_csrf, app):
    _, unidade = _criar_empresa("Escritório D", "D1")
    _criar_usuario_e_logar(unidade.id, "adv4@leadsteste.com", "advogado", login)
    lead = Lead(nome="Contato D", unidade_id=unidade.id)
    db.session.add(lead)
    db.session.commit()

    resp = post_csrf(f"/leads/{lead.id}/mover", {"etapa": "perdido", "motivo_perda": "Não retornou contato"},
                      get_url=f"/leads/{lead.id}")

    assert resp.status_code == 200
    lead_recarregado = db.session.get(Lead, lead.id)
    assert lead_recarregado.etapa == "perdido"
    assert lead_recarregado.motivo_perda == "Não retornou contato"


def test_mover_direto_para_convertido_e_bloqueado(client, login, post_csrf, app):
    """A etapa "convertido" só pode ser alcançada pela rota de conversão de
    verdade (que cria o Cliente) — nunca só trocando o valor do <select>,
    senão o lead ficaria marcado como convertido sem nenhum Cliente
    vinculado."""
    _, unidade = _criar_empresa("Escritório E", "E1")
    _criar_usuario_e_logar(unidade.id, "adv5@leadsteste.com", "advogado", login)
    lead = Lead(nome="Contato E", unidade_id=unidade.id)
    db.session.add(lead)
    db.session.commit()

    resp = post_csrf(f"/leads/{lead.id}/mover", {"etapa": "convertido"}, get_url=f"/leads/{lead.id}")

    assert resp.status_code == 200
    lead_recarregado = db.session.get(Lead, lead.id)
    assert lead_recarregado.etapa == "novo"
    assert lead_recarregado.cliente_id is None


def test_converter_cria_cliente_e_fecha_o_lead(client, login, post_csrf, app):
    _, unidade = _criar_empresa("Escritório F", "F1")
    _criar_usuario_e_logar(unidade.id, "adv6@leadsteste.com", "advogado", login)
    lead = Lead(nome="Contato F", telefone="11988887777", whatsapp="11988887777",
                email="contatof@example.com", observacoes="Caso trabalhista urgente",
                unidade_id=unidade.id)
    db.session.add(lead)
    db.session.commit()

    resp = post_csrf(f"/leads/{lead.id}/converter", {"tipo_pessoa": "PF"}, get_url=f"/leads/{lead.id}")

    assert resp.status_code == 200
    lead_recarregado = db.session.get(Lead, lead.id)
    assert lead_recarregado.etapa == "convertido"
    assert lead_recarregado.cliente_id is not None

    cliente = db.session.get(Cliente, lead_recarregado.cliente_id)
    assert cliente is not None
    assert cliente.nome == "Contato F"
    assert cliente.telefone == "11988887777"
    assert cliente.email == "contatof@example.com"
    assert cliente.unidade_id == unidade.id


def test_converter_duas_vezes_nao_duplica_cliente(client, login, post_csrf, app):
    _, unidade = _criar_empresa("Escritório G", "G1")
    _criar_usuario_e_logar(unidade.id, "adv7@leadsteste.com", "advogado", login)
    lead = Lead(nome="Contato G", unidade_id=unidade.id)
    db.session.add(lead)
    db.session.commit()

    post_csrf(f"/leads/{lead.id}/converter", {"tipo_pessoa": "PF"}, get_url=f"/leads/{lead.id}")
    cliente_id_primeira_vez = db.session.get(Lead, lead.id).cliente_id

    post_csrf(f"/leads/{lead.id}/converter", {"tipo_pessoa": "PF"}, get_url=f"/leads/{lead.id}")

    assert Cliente.query.count() == 1
    assert db.session.get(Lead, lead.id).cliente_id == cliente_id_primeira_vez


def test_menu_mostra_item_captacao_dentro_de_operacao(client, login, app):
    _, unidade = _criar_empresa("Escritório H", "H1")
    _criar_usuario_e_logar(unidade.id, "adv8@leadsteste.com", "advogado", login)

    html = client.get("/").data.decode("utf-8")

    assert "Captação" in html
    assert 'href="/leads/"' in html
