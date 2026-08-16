"""
Disparo de lembretes de compromisso da Agenda (ver app/models/compromisso.py
e app/routes/agenda.py) na hora marcada pelo usuário em `notificar_em`.

Não roda sozinho — precisa ser AGENDADO. Já vem pronto para rodar dentro
do próprio container via cron (ver docker/lembretes-compromissos.cron e
Dockerfile), no mesmo esquema já usado pela recaptura diária do DataJud
(capturar_movimentacoes.py) — não depende de nenhum recurso externo de
agendamento nem de configuração manual no EasyPanel.

Roda a cada 5 minutos (granularidade do cron, ver o .cron) e dispara todo
compromisso cujo `notificar_em` já passou e ainda não foi notificado
(`notificacao_enviada_em is None`). Marca `notificacao_enviada_em` logo
depois de notificar, então nunca dispara duas vezes o mesmo lembrete
mesmo que o job rode várias vezes seguidas.

Canais:
  - Notificação dentro do sistema (sempre, para o usuário responsável —
    não depende de nenhuma credencial externa).
  - E-mail para o responsável, SE SMTP estiver configurado (ver
    app/utils/email.py e config.py). Sem SMTP configurado, só a
    notificação in-app é enviada mesmo assim — nunca falha o lembrete
    inteiro por falta de e-mail.
  - WhatsApp, SE o compromisso tiver `enviar_whatsapp=True`, estiver
    vinculado a um cliente com número cadastrado, E `WHATSAPP_BRIDGE_URL`
    estiver configurada (ver app/utils/whatsapp.py, whatsapp-bridge/ e
    PENDENCIAS.md — automação NÃO-OFICIAL, decisão explícita do dono do
    sistema, ciente do risco de banimento do número usado). Sem isso
    configurado, o envio por WhatsApp simplesmente não acontece — o
    lembrete continua saindo normalmente pelos outros canais.

Uso:
    python enviar_lembretes_compromissos.py
"""
import sys
from datetime import datetime

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.models import Compromisso
from app.utils.notificacoes import notificar
from app.utils.email import enviar_email, smtp_configurado
from app.utils.whatsapp import enviar_whatsapp, whatsapp_configurado


def enviar_lembretes():
    app = create_app()
    with app.app_context():
        agora = datetime.utcnow()
        pendentes = Compromisso.query.filter(
            Compromisso.status == "agendado",
            Compromisso.notificar_em.isnot(None),
            Compromisso.notificar_em <= agora,
            Compromisso.notificacao_enviada_em.is_(None),
        ).all()

        print(f"{len(pendentes)} lembrete(s) pendente(s).")

        for c in pendentes:
            try:
                titulo = f"Lembrete: {c.titulo}"
                detalhes = f" — {c.local}" if c.local else ""
                mensagem = f"{c.titulo} às {c.data_hora.strftime('%d/%m/%Y %H:%M')}{detalhes}"

                notificar(c.responsavel_id, titulo, mensagem=mensagem, tipo="compromisso",
                          link=f"/agenda/compromissos/{c.id}/editar")

                if smtp_configurado() and c.responsavel and c.responsavel.email:
                    enviar_email(c.responsavel.email, titulo, mensagem)

                # Diagnóstico explícito de cada motivo de não enviar por
                # WhatsApp — sem isso, um envio pulado ficava silencioso e
                # indistinguível de "funcionou mas o cliente não recebeu".
                if c.enviar_whatsapp:
                    if not whatsapp_configurado():
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: WHATSAPP_BRIDGE_URL não configurada "
                              f"no .env do app principal (ver PENDENCIAS.md, seção -4).")
                    elif not c.cliente:
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: nenhum cliente vinculado ao compromisso.")
                    elif not c.cliente.whatsapp:
                        print(f"  WHATSAPP PULADO compromisso #{c.id}: cliente '{c.cliente.nome}' não tem "
                              f"número de WhatsApp cadastrado.")
                    else:
                        if enviar_whatsapp(c.cliente.whatsapp, mensagem):
                            c.whatsapp_enviado_em = agora
                            print(f"  WHATSAPP OK compromisso #{c.id}: enviado para {c.cliente.nome}.")
                        else:
                            print(f"  WHATSAPP FALHOU compromisso #{c.id}: o WAHA recusou o envio "
                                  f"(veja o código HTTP no aviso 'WARNING in whatsapp' logo acima). "
                                  f"Causas mais comuns: (1) WHATSAPP_BRIDGE_TOKEN (no app principal) "
                                  f"diferente do WAHA_API_KEY (no serviço WAHA) — precisam ser "
                                  f"IDÊNTICOS, char por char; (2) a sessão 'default' não está com "
                                  f"status WORKING no dashboard do WAHA.")

                c.notificacao_enviada_em = agora
                db.session.commit()
                print(f"  OK  compromisso #{c.id}: {c.titulo}")
            except Exception as e:  # nunca deixa um compromisso travar a fila inteira
                db.session.rollback()
                print(f"  ERRO  compromisso #{c.id}: {e}")


if __name__ == "__main__":
    enviar_lembretes()
