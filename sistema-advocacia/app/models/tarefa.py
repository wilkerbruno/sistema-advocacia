from datetime import datetime
from app.extensions import db


class Tarefa(db.Model):
    __tablename__ = "tarefas"

    STATUS = ("pendente", "em_andamento", "concluida", "cancelada")
    PRIORIDADES = ("baixa", "normal", "alta", "urgente")

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    descricao = db.Column(db.Text)
    status = db.Column(db.String(20), default="pendente")
    prioridade = db.Column(db.String(20), default="normal")
    data_vencimento = db.Column(db.Date)
    concluida_em = db.Column(db.DateTime)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=True)
    processo = db.relationship("Processo")

    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    responsavel = db.relationship("Usuario", foreign_keys=[responsavel_id])

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])
