from datetime import datetime
from app.extensions import db


class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    tipo_pessoa = db.Column(db.String(2), nullable=False, default="PF")  # PF ou PJ
    nome = db.Column(db.String(150), nullable=False)  # nome ou razão social
    cpf_cnpj = db.Column(db.String(20), index=True)
    rg_ie = db.Column(db.String(30))
    email = db.Column(db.String(120))
    telefone = db.Column(db.String(30))
    whatsapp = db.Column(db.String(30))
    endereco = db.Column(db.String(255))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    cep = db.Column(db.String(10))
    observacoes = db.Column(db.Text)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade", back_populates="clientes")

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])

    processos = db.relationship("Processo", back_populates="cliente", lazy="dynamic")

    def __repr__(self):
        return f"<Cliente {self.nome}>"
