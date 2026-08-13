from datetime import datetime
from app.extensions import db


class Notificacao(db.Model):
    __tablename__ = "notificacoes"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario")

    titulo = db.Column(db.String(150), nullable=False)
    mensagem = db.Column(db.String(500))
    tipo = db.Column(db.String(30), default="info")  # info, prazo, audiencia, tarefa
    link = db.Column(db.String(255))
    lida = db.Column(db.Boolean, default=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
