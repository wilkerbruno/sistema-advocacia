from datetime import datetime
from app.extensions import db


class Lancamento(db.Model):
    """Lançamento financeiro: honorários a receber, custas, despesas da unidade."""
    __tablename__ = "lancamentos_financeiros"

    TIPOS = ("honorario", "custas", "despesa", "outro")
    NATUREZAS = ("receita", "despesa")
    STATUS = ("pendente", "pago", "atrasado", "cancelado")

    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(255), nullable=False)
    tipo = db.Column(db.String(20), default="honorario")
    natureza = db.Column(db.String(10), default="receita")  # receita ou despesa
    valor = db.Column(db.Numeric(14, 2), nullable=False)
    status = db.Column(db.String(20), default="pendente")
    data_vencimento = db.Column(db.Date)
    data_pagamento = db.Column(db.Date)
    forma_pagamento = db.Column(db.String(40))
    parcela = db.Column(db.String(20))  # ex: "2/6"
    observacoes = db.Column(db.Text)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=True)
    processo = db.relationship("Processo", back_populates="lancamentos")

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)
    cliente = db.relationship("Cliente")

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario")
