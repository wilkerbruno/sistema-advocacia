"""
Compromisso — evento livre da Agenda (reunião, ligação, visita etc.),
pedido pelo usuário: "quero agendar uma reunião com um horário em uma
data da agenda, colocar o nome da agenda e até inserir um horário para
enviar uma notificação".

Diferente de Prazo/Audiência/Tarefa (que sempre nascem de um processo),
o Compromisso é solto — pode opcionalmente estar ligado a um cliente
(que é quem recebe o lembrete por e-mail/WhatsApp, se configurado), mas
não depende de processo nenhum.

O lembrete em si é dois campos independentes:
  - `notificar_em`: um horário (data + hora) ESCOLHIDO PELO USUÁRIO,
    normalmente antes de `data_hora`, em que o lembrete deve ser
    disparado. Fica nulo se o usuário não quiser lembrete nenhum.
  - `notificacao_enviada_em`: marcado pelo job de lembrete
    (enviar_lembretes_compromissos.py, rodando via cron — ver
    docker/lembretes-compromissos.cron) na hora em que o lembrete
    realmente sai, pra nunca mandar o mesmo lembrete duas vezes mesmo
    que o job rode de novo antes do próximo compromisso.

Canal do lembrete:
  - In-app (Notificacao / notificar()): sempre, automático, já que essa
    infraestrutura já existe e não depende de nenhuma credencial externa.
  - E-mail: só se SMTP_HOST/SMTP_USER/SMTP_PASSWORD estiverem configurados
    (ver config.py) — mesmo padrão "some/degrada honestamente sem
    credencial" usado em DATAJUD_API_KEY e no modelo de IA local.
  - WhatsApp (`enviar_whatsapp`): campo de INTENÇÃO do usuário, mas o
    envio de fato depende de qual canal for escolhido/configurado depois
    (API oficial paga da Meta, ou não enviar) — ver PENDENCIAS.md. Nunca
    finge que enviou: `whatsapp_enviado_em` só é preenchido quando (e se)
    um envio de verdade acontecer.
"""
from datetime import datetime
from app.extensions import db


class Compromisso(db.Model):
    __tablename__ = "compromissos"

    id = db.Column(db.Integer, primary_key=True)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)

    titulo = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.String(500))
    local = db.Column(db.String(150))

    data_hora = db.Column(db.DateTime, nullable=False)

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"))

    notificar_em = db.Column(db.DateTime)
    notificacao_enviada_em = db.Column(db.DateTime)

    enviar_whatsapp = db.Column(db.Boolean, default=False)
    whatsapp_enviado_em = db.Column(db.DateTime)

    status = db.Column(db.String(20), default="agendado")  # agendado, cancelado, realizado

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    unidade = db.relationship("Unidade")
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])
    responsavel = db.relationship("Usuario", foreign_keys=[responsavel_id])
    cliente = db.relationship("Cliente")

    @property
    def tem_lembrete_pendente(self):
        return (self.status == "agendado" and self.notificar_em is not None
                and self.notificacao_enviada_em is None)
