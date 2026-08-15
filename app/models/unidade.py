from datetime import datetime
from app.extensions import db


class Unidade(db.Model):
    """Unidade / filial de uma empresa cliente."""
    __tablename__ = "unidades"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=True)  # nunca fica nulo na prática — toda rota de criação exige; fica NULL-permitido no schema só para a migração inicial não quebrar dado existente (ver migrar_multitenant.py)
    nome = db.Column(db.String(120), nullable=False)
    codigo = db.Column(db.String(20), unique=True, nullable=False)  # ex: SP-01, RJ-01
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    endereco = db.Column(db.String(255))
    telefone = db.Column(db.String(30))
    email = db.Column(db.String(120))
    responsavel = db.Column(db.String(120))  # sócio/gestor responsável pela unidade
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    criada_em = db.Column(db.DateTime, default=datetime.utcnow)

    empresa = db.relationship("Empresa", back_populates="unidades")
    usuarios = db.relationship("Usuario", back_populates="unidade", lazy="dynamic")
    clientes = db.relationship("Cliente", back_populates="unidade", lazy="dynamic")
    processos = db.relationship("Processo", back_populates="unidade", lazy="dynamic")

    def __repr__(self):
        return f"<Unidade {self.codigo} - {self.nome}>"
