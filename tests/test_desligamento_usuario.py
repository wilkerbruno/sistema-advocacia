"""
Testa a reatribuição de casos no desligamento de usuário (PENDENCIAS.md,
seção -46): desligar direto quando não há pendência, bloquear e exigir
reatribuição quando há, nunca mexer em item já fechado/histórico, nunca
deixar reatribuir pra usuário de outra empresa (isolamento multi-tenant).
"""
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Prazo, Audiencia, Tarefa, Compromisso, Usuario, Unidade, Empresa, Licenca


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    empresa_id = empresa_basica["empresa_id"]

    unidade2 = Unidade(nome="Filial", codigo="F1", empresa_id=empresa_id)
    db.session.add(unidade2)
    db.session.flush()

    admin_id = criar_usuario(unidade_id, "admin@des.com", papel="admin")
    gestor_id = criar_usuario(unidade_id, "gestor@des.com", papel="gestor")
    limpo_id = criar_usuario(unidade_id, "limpo@des.com", papel="advogado")
    ocupado_id = criar_usuario(unidade_id, "ocupado@des.com", papel="advogado")
    substituto_id = criar_usuario(unidade_id, "substituto@des.com", papel="advogado")
    outra_unidade_id = criar_usuario(unidade2.id, "outraunidade@des.com", papel="advogado")

    cliente = Cliente(nome="Cliente Desligamento", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    hoje = date.today()

    proc_ativo = Processo(numero_interno="P-ATIVO-1", cliente_id=cliente.id, unidade_id=unidade_id,
                           area_direito="Cível", responsavel_id=ocupado_id, criado_por_id=admin_id, status="ativo")
    proc_encerrado = Processo(numero_interno="P-ENCERRADO-1", cliente_id=cliente.id, unidade_id=unidade_id,
                               area_direito="Cível", responsavel_id=ocupado_id, criado_por_id=admin_id, status="encerrado")
    db.session.add_all([proc_ativo, proc_encerrado])
    db.session.flush()

    prazo_pendente = Prazo(processo_id=proc_ativo.id, descricao="Contestar", data_vencimento=hoje + timedelta(days=5),
                            status="pendente", responsavel_id=ocupado_id)
    prazo_cumprido = Prazo(processo_id=proc_ativo.id, descricao="Já feito", data_vencimento=hoje - timedelta(days=1),
                            status="cumprido", responsavel_id=ocupado_id)
    prazo_historico = Prazo(processo_id=proc_ativo.id, descricao="Histórico", data_vencimento=hoje - timedelta(days=100),
                             status="historico_anterior", responsavel_id=ocupado_id)
    db.session.add_all([prazo_pendente, prazo_cumprido, prazo_historico])

    aud_agendada = Audiencia(processo_id=proc_ativo.id, tipo="Instrução", data_hora=datetime.utcnow() + timedelta(days=3),
                              status="agendada", responsavel_id=ocupado_id)
    aud_cancelada = Audiencia(processo_id=proc_ativo.id, tipo="Julgamento", data_hora=datetime.utcnow() + timedelta(days=3),
                               status="cancelada", responsavel_id=ocupado_id)
    db.session.add_all([aud_agendada, aud_cancelada])

    tarefa_pendente = Tarefa(titulo="Revisar peça", status="pendente", unidade_id=unidade_id,
                              responsavel_id=ocupado_id, criado_por_id=admin_id)
    tarefa_concluida = Tarefa(titulo="Já concluída", status="concluida", unidade_id=unidade_id,
                               responsavel_id=ocupado_id, criado_por_id=admin_id)
    db.session.add_all([tarefa_pendente, tarefa_concluida])

    compromisso_futuro = Compromisso(unidade_id=unidade_id, criado_por_id=admin_id, responsavel_id=ocupado_id,
                                      titulo="Reunião cliente", data_hora=datetime.utcnow() + timedelta(days=2),
                                      status="agendado")
    compromisso_passado = Compromisso(unidade_id=unidade_id, criado_por_id=admin_id, responsavel_id=ocupado_id,
                                       titulo="Reunião antiga", data_hora=datetime.utcnow() - timedelta(days=10),
                                       status="agendado")
    db.session.add_all([compromisso_futuro, compromisso_passado])
    db.session.commit()

    return dict(
        empresa_id=empresa_id, unidade_id=unidade_id, admin_id=admin_id, gestor_id=gestor_id,
        limpo_id=limpo_id, ocupado_id=ocupado_id, substituto_id=substituto_id, outra_unidade_id=outra_unidade_id,
        proc_ativo_id=proc_ativo.id, proc_encerrado_id=proc_encerrado.id,
        prazo_pendente_id=prazo_pendente.id, prazo_cumprido_id=prazo_cumprido.id, prazo_historico_id=prazo_historico.id,
        aud_agendada_id=aud_agendada.id, aud_cancelada_id=aud_cancelada.id,
        tarefa_pendente_id=tarefa_pendente.id, tarefa_concluida_id=tarefa_concluida.id,
        compromisso_futuro_id=compromisso_futuro.id, compromisso_passado_id=compromisso_passado.id,
    )


def test_desligamento_direto_sem_pendencia(client, login, post_csrf, cenario):
    r = login("admin@des.com")
    assert r.status_code == 200
    r = post_csrf(f"/admin/usuarios/{cenario['limpo_id']}/editar",
                   {"nome": "Limpo Sem Casos", "papel": "advogado", "unidade_id": str(cenario["unidade_id"]),
                    "ativo": "1"})
    assert r.status_code == 200
    assert db.session.get(Usuario, cenario["limpo_id"]).ativo is True, \
        "editar outro campo mantendo a caixa \"ativo\" marcada não deveria desligar ninguém"

    r = post_csrf(f"/admin/usuarios/{cenario['limpo_id']}/editar",
                   {"nome": "Limpo Sem Casos", "papel": "advogado", "unidade_id": str(cenario["unidade_id"])})
    # sem "ativo" no payload == checkbox desmarcada
    assert r.status_code == 200
    assert db.session.get(Usuario, cenario["limpo_id"]).ativo is False, \
        "usuário sem nenhum item em aberto deveria ter sido desligado direto pelo checkbox"


def test_desligamento_bloqueado_com_pendencia_mas_outros_campos_sao_salvos(client, login, post_csrf, cenario):
    login("admin@des.com")
    r = post_csrf(f"/admin/usuarios/{cenario['ocupado_id']}/editar",
                   {"nome": "Ocupado Com Casos", "papel": "advogado", "unidade_id": str(cenario["unidade_id"]),
                    "telefone": "11999998888"})
    assert r.status_code == 200
    assert "Desligar usuário" in r.data.decode("utf-8")

    ocupado = db.session.get(Usuario, cenario["ocupado_id"])
    assert ocupado.ativo is True, "usuário com pendência NÃO deveria ter sido desligado pelo checkbox"
    assert ocupado.telefone == "11999998888", "os outros campos do formulário deveriam ter sido salvos mesmo assim"


def test_tela_de_desligamento_mostra_contagens_certas(client, login, cenario):
    login("admin@des.com")
    r = client.get(f"/admin/usuarios/{cenario['ocupado_id']}/desligar")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "1 processo(s) ativo(s)" in body
    assert "1 prazo(s) pendente(s)" in body
    assert "1 audiência(s) agendada(s)" in body
    assert "1 tarefa(s) em aberto" in body
    assert "1 compromisso(s) futuro(s)" in body
    assert "substituto" in body.lower(), "candidato a substituto deveria aparecer no dropdown"


def test_gestor_so_ve_candidato_da_propria_unidade(client, login, cenario):
    login("gestor@des.com")
    r = client.get(f"/admin/usuarios/{cenario['ocupado_id']}/desligar")
    assert r.status_code == 200
    body = r.data.decode("utf-8").lower()
    assert "substituto" in body
    assert "outraunidade" not in body, "gestor não deveria poder escolher substituto de outra unidade"


def test_post_sem_substituto_nao_desliga(client, login, post_csrf, cenario):
    login("admin@des.com")
    r = post_csrf(f"/admin/usuarios/{cenario['ocupado_id']}/desligar", {"ciente": "1"})
    assert r.status_code == 200
    assert db.session.get(Usuario, cenario["ocupado_id"]).ativo is True


def test_post_sem_marcar_ciencia_nao_desliga(client, login, post_csrf, cenario):
    login("admin@des.com")
    r = post_csrf(f"/admin/usuarios/{cenario['ocupado_id']}/desligar",
                   {"novo_responsavel_id": str(cenario["substituto_id"])})
    assert r.status_code == 200
    assert db.session.get(Usuario, cenario["ocupado_id"]).ativo is True


def test_desligamento_completo_reatribui_so_o_que_esta_aberto(client, login, post_csrf, cenario):
    login("admin@des.com")
    r = post_csrf(f"/admin/usuarios/{cenario['ocupado_id']}/desligar",
                   {"novo_responsavel_id": str(cenario["substituto_id"]), "ciente": "1"})
    assert r.status_code == 200
    assert "desligado" in r.data.decode("utf-8").lower()

    ocupado_id, substituto_id = cenario["ocupado_id"], cenario["substituto_id"]
    assert db.session.get(Usuario, ocupado_id).ativo is False

    # itens ABERTOS foram movidos pro substituto
    assert db.session.get(Processo, cenario["proc_ativo_id"]).responsavel_id == substituto_id
    assert db.session.get(Prazo, cenario["prazo_pendente_id"]).responsavel_id == substituto_id
    assert db.session.get(Audiencia, cenario["aud_agendada_id"]).responsavel_id == substituto_id
    assert db.session.get(Tarefa, cenario["tarefa_pendente_id"]).responsavel_id == substituto_id
    assert db.session.get(Compromisso, cenario["compromisso_futuro_id"]).responsavel_id == substituto_id

    # itens FECHADOS/históricos/passados continuam intactos com o usuário desligado
    assert db.session.get(Processo, cenario["proc_encerrado_id"]).responsavel_id == ocupado_id
    assert db.session.get(Prazo, cenario["prazo_cumprido_id"]).responsavel_id == ocupado_id
    assert db.session.get(Prazo, cenario["prazo_historico_id"]).responsavel_id == ocupado_id
    assert db.session.get(Audiencia, cenario["aud_cancelada_id"]).responsavel_id == ocupado_id
    assert db.session.get(Tarefa, cenario["tarefa_concluida_id"]).responsavel_id == ocupado_id
    assert db.session.get(Compromisso, cenario["compromisso_passado_id"]).responsavel_id == ocupado_id


def test_nao_pode_desligar_o_proprio_usuario(client, login, cenario):
    login("admin@des.com")
    r = client.get(f"/admin/usuarios/{cenario['admin_id']}/desligar", follow_redirects=True)
    assert r.status_code == 200
    assert "não pode desligar o próprio usuário" in r.data.decode("utf-8").lower()
    assert db.session.get(Usuario, cenario["admin_id"]).ativo is True


def test_desligar_usuario_ja_inativo_so_avisa(client, login, post_csrf, cenario):
    login("admin@des.com")
    post_csrf(f"/admin/usuarios/{cenario['limpo_id']}/editar",
              {"nome": "Limpo Sem Casos", "papel": "advogado", "unidade_id": str(cenario["unidade_id"])})
    # (sem "ativo" -> desliga direto, já que "limpo" não tem pendência)
    assert db.session.get(Usuario, cenario["limpo_id"]).ativo is False

    r = client.get(f"/admin/usuarios/{cenario['limpo_id']}/desligar", follow_redirects=True)
    assert r.status_code == 200
    assert "já está inativo" in r.data.decode("utf-8").lower()


def test_usuario_comum_nao_acessa_tela_de_desligamento(client, login, cenario):
    login("outraunidade@des.com")
    r = client.get(f"/admin/usuarios/{cenario['substituto_id']}/desligar")
    assert r.status_code == 403


def test_admin_desenvolvedor_nao_escolhe_substituto_de_outra_empresa(client, login, criar_usuario, cenario):
    empresa_dev = Empresa(nome="Plataforma", dono_da_plataforma=True)
    db.session.add(empresa_dev)
    db.session.flush()
    db.session.add(Licenca(empresa_id=empresa_dev.id, plano="mensal", valor_negociado=0, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=365)))
    unidade_dev = Unidade(nome="Plataforma HQ", codigo="DEV", empresa_id=empresa_dev.id)
    db.session.add(unidade_dev)
    db.session.flush()
    dev_id = criar_usuario(unidade_dev.id, "dev@plataforma.com", papel="admin")

    empresa2 = Empresa(nome="Outro Escritório")
    db.session.add(empresa2)
    db.session.flush()
    db.session.add(Licenca(empresa_id=empresa2.id, plano="mensal", valor_negociado=100, status="ativa",
                            data_inicio=date.today(), data_fim=date.today() + timedelta(days=30)))
    unidade_emp2 = Unidade(nome="Sede", codigo="E2", empresa_id=empresa2.id)
    db.session.add(unidade_emp2)
    db.session.flush()
    criar_usuario(unidade_emp2.id, "outraempresa@des.com", papel="advogado", nome="Usuario De Outra Empresa")
    db.session.commit()

    login("dev@plataforma.com")
    # Usa o "ocupado" (tem pendência de verdade) como alvo — só assim a
    # tela chega a montar/mostrar o <select> de candidatos; o "substituto"
    # não tem nenhum item em aberto, então a tela nem chega a oferecer
    # escolha de substituto (mostra direto "pode desligar sem reatribuir").
    r = client.get(f"/admin/usuarios/{cenario['ocupado_id']}/desligar")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    assert "outraunidade@des.com".split("@")[0] in body.lower() or "outra" in body.lower(), \
        "admin dev deveria ver candidato de outra unidade DA MESMA empresa"
    assert "Usuario De Outra Empresa" not in body, \
        "admin dev NÃO deveria ver candidato de outra empresa cliente como substituto"
