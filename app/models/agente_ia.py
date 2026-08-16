from datetime import datetime
from app.extensions import db


class ConversaAgenteIA(db.Model):
    """
    Uma conversa com um dos agentes de IA jurídica (item 6 do briefing de
    paridade). Cada conversa pertence a um único usuário e a uma persona
    fixa — trocar de persona no meio de uma conversa muda o "especialista"
    que está respondendo, então abre uma conversa nova.
    """
    __tablename__ = "conversas_agente_ia"

    PERSONAS = ("operacao", "gestao", "negocios")

    id = db.Column(db.Integer, primary_key=True)

    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario")

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    persona = db.Column(db.String(20), nullable=False)  # ver PERSONAS
    titulo = db.Column(db.String(150))  # gerado a partir da 1ª pergunta, só pra listar

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mensagens = db.relationship("MensagemAgenteIA", back_populates="conversa",
                                 cascade="all, delete-orphan", order_by="MensagemAgenteIA.criado_em")

    def __repr__(self):
        return f"<ConversaAgenteIA {self.id} {self.persona} usuario={self.usuario_id}>"


class MensagemAgenteIA(db.Model):
    """Uma mensagem (do usuário ou do modelo) dentro de uma ConversaAgenteIA."""
    __tablename__ = "mensagens_agente_ia"

    PAPEIS = ("user", "assistant")

    id = db.Column(db.Integer, primary_key=True)

    conversa_id = db.Column(db.Integer, db.ForeignKey("conversas_agente_ia.id"), nullable=False)
    conversa = db.relationship("ConversaAgenteIA", back_populates="mensagens")

    papel = db.Column(db.String(10), nullable=False)  # ver PAPEIS
    conteudo = db.Column(db.Text, nullable=False)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<MensagemAgenteIA {self.id} {self.papel}>"
