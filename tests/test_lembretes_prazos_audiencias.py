"""
Testa o script agendado (.cron) de lembretes de prazos e audiências
(enviar_lembretes_prazos_audiencias.py): dispara só dentro da janela
configurada, ignora item já cumprido/cancelado/soft-deletado, sempre
dispara pra item já vencido e ainda pendente (atrasado), cria notificação
in-app pro responsável, e é idempotente (rodar de novo não duplica
lembrete já enviado). SMTP/WhatsApp não configurados neste teste de
propósito — o script degrada graciosamente sem eles (mesmo padrão do
resto do projeto), então isso também confirma que a notificação in-app
não depende de nenhuma credencial externa.
"""
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import Cliente, Processo, Prazo, Audiencia, Notificacao

from enviar_lembretes_prazos_audiencias import enviar_lembretes


@pytest.fixture()
def cenario(app, empresa_basica, criar_usuario):
    unidade_id = empresa_basica["unidade_id"]
    resp_id = criar_usuario(unidade_id, "resp@teste.com", papel="advogado", nome="Responsavel Teste")

    cliente = Cliente(nome="Cliente Lembrete", email="cliente@teste.com", unidade_id=unidade_id)
    db.session.add(cliente)
    db.session.flush()

    processo = Processo(numero_processo="0000060-11.2026.8.26.0100", cliente_id=cliente.id,
                         unidade_id=unidade_id, area_direito="Cível",
                         responsavel_id=resp_id, criado_por_id=resp_id)
    db.session.add(processo)
    db.session.flush()

    hoje = date.today()

    # Prazo 1: dentro do limite padrão (2 dias) -> DEVE disparar
    prazo_dentro = Prazo(processo_id=processo.id, descricao="Contestar", data_vencimento=hoje + timedelta(days=1),
                          status="pendente", responsavel_id=resp_id)
    # Prazo 2: fora do limite (10 dias) -> NÃO deve disparar ainda
    prazo_fora = Prazo(processo_id=processo.id, descricao="Recorrer", data_vencimento=hoje + timedelta(days=10),
                        status="pendente", responsavel_id=resp_id)
    # Prazo 3: já cumprido -> NÃO deve disparar mesmo estando na janela
    prazo_cumprido = Prazo(processo_id=processo.id, descricao="Ja feito", data_vencimento=hoje + timedelta(days=1),
                            status="cumprido", responsavel_id=resp_id)
    # Prazo 4: já vencido há 5 dias, ainda pendente, nunca lembrado -> DEVE disparar
    prazo_atrasado = Prazo(processo_id=processo.id, descricao="Atrasado", data_vencimento=hoje - timedelta(days=5),
                            status="pendente", responsavel_id=resp_id)
    # Prazo 5: dentro da janela mas soft-deletado -> NÃO deve disparar
    prazo_deletado = Prazo(processo_id=processo.id, descricao="Deletado", data_vencimento=hoje + timedelta(days=1),
                            status="pendente", responsavel_id=resp_id, deletado_em=datetime.utcnow())

    db.session.add_all([prazo_dentro, prazo_fora, prazo_cumprido, prazo_atrasado, prazo_deletado])

    # Audiência 1: dentro do limite padrão (1 dia) -> DEVE disparar
    aud_dentro = Audiencia(processo_id=processo.id, tipo="Instrução", data_hora=datetime.utcnow() + timedelta(hours=12),
                            local="Forum Central", status="agendada", responsavel_id=resp_id)
    # Audiência 2: fora do limite -> NÃO deve disparar ainda
    aud_fora = Audiencia(processo_id=processo.id, tipo="Julgamento", data_hora=datetime.utcnow() + timedelta(days=10),
                          status="agendada", responsavel_id=resp_id)
    # Audiência 3: cancelada -> NÃO deve disparar
    aud_cancelada = Audiencia(processo_id=processo.id, tipo="Conciliação", data_hora=datetime.utcnow() + timedelta(hours=6),
                               status="cancelada", responsavel_id=resp_id)

    db.session.add_all([aud_dentro, aud_fora, aud_cancelada])
    db.session.commit()

    return dict(
        prazo_dentro=prazo_dentro.id, prazo_fora=prazo_fora.id, prazo_cumprido=prazo_cumprido.id,
        prazo_atrasado=prazo_atrasado.id, prazo_deletado=prazo_deletado.id,
        aud_dentro=aud_dentro.id, aud_fora=aud_fora.id, aud_cancelada=aud_cancelada.id,
        resp_id=resp_id,
    )


def test_regras_de_disparo_de_prazo_e_audiencia_e_idempotencia(cenario):
    def prazo(chave):
        return db.session.get(Prazo, cenario[chave])

    def aud(chave):
        return db.session.get(Audiencia, cenario[chave])

    enviar_lembretes()

    assert prazo("prazo_dentro").lembrete_enviado_em is not None, \
        "prazo dentro do limite deveria ter lembrete enviado"
    assert prazo("prazo_fora").lembrete_enviado_em is None, \
        "prazo fora do limite NÃO deveria ter lembrete enviado ainda"
    assert prazo("prazo_cumprido").lembrete_enviado_em is None, \
        "prazo já cumprido NÃO deveria receber lembrete"
    assert prazo("prazo_atrasado").lembrete_enviado_em is not None, \
        "prazo já vencido mas ainda pendente DEVERIA receber lembrete (atrasado)"
    assert prazo("prazo_deletado").lembrete_enviado_em is None, \
        "prazo soft-deletado NÃO deveria receber lembrete"

    assert aud("aud_dentro").lembrete_enviado_em is not None, \
        "audiência dentro do limite deveria ter lembrete enviado"
    assert aud("aud_fora").lembrete_enviado_em is None, \
        "audiência fora do limite NÃO deveria ter lembrete enviado ainda"
    assert aud("aud_cancelada").lembrete_enviado_em is None, \
        "audiência cancelada NÃO deveria receber lembrete"

    notifs = Notificacao.query.filter_by(usuario_id=cenario["resp_id"]).all()
    tipos = [n.tipo for n in notifs]
    assert tipos.count("prazo") == 2, f"esperava 2 notificações de prazo (dentro + atrasado), veio {tipos.count('prazo')}"
    assert tipos.count("audiencia") == 1, f"esperava 1 notificação de audiência, veio {tipos.count('audiencia')}"

    # Roda de novo - idempotência: não deve duplicar notificação pros mesmos registros
    enviar_lembretes()

    notifs_depois = Notificacao.query.filter_by(usuario_id=cenario["resp_id"]).all()
    tipos_depois = [n.tipo for n in notifs_depois]
    assert tipos_depois.count("prazo") == 2, "rodar de novo NÃO deveria duplicar notificação de prazo já lembrado"
    assert tipos_depois.count("audiencia") == 1, "rodar de novo NÃO deveria duplicar notificação de audiência já lembrada"
