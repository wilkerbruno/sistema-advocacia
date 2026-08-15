from datetime import datetime
from app.extensions import db


class Licenca(db.Model):
    """
    Licença de uso da plataforma para uma empresa. Uma empresa tem no
    máximo uma licença (o histórico de cobranças fica em Pagamento).

    `valor_negociado`: preço que ESSA empresa paga — definido pelo admin
    desenvolvedor por empresa, nunca exposto como "tabela padrão" para a
    própria empresa (ela só vê o próprio valor, nunca uma lista de preços
    que deixe evidente que é negociável).
    """
    __tablename__ = "licencas"

    PLANOS = ("mensal", "trimestral", "anual")
    STATUS = ("ativa", "pendente_pagamento", "vencida", "cancelada")

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, unique=True)

    plano = db.Column(db.String(20), nullable=False, default="mensal")
    valor_negociado = db.Column(db.Numeric(10, 2), nullable=False)  # valor cobrado por ciclo, definido pelo admin dev
    status = db.Column(db.String(30), nullable=False, default="pendente_pagamento")

    data_inicio = db.Column(db.Date)
    data_fim = db.Column(db.Date)  # quando a licença ativa vence

    definido_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))  # admin dev que definiu o valor
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = db.relationship("Empresa", back_populates="licenca")
    definido_por = db.relationship("Usuario")
    pagamentos = db.relationship("Pagamento", back_populates="licenca", order_by="Pagamento.criado_em.desc()")

    DIAS_POR_PLANO = {"mensal": 30, "trimestral": 90, "anual": 365}

    def esta_ativa(self):
        if self.status != "ativa":
            return False
        if self.data_fim is None:
            return False
        from datetime import date
        return self.data_fim >= date.today()

    def __repr__(self):
        return f"<Licenca empresa={self.empresa_id} {self.plano} {self.status}>"


class Pagamento(db.Model):
    """
    Uma cobrança individual (uma preferência de checkout do Mercado Pago
    e seu resultado). Uma licença acumula vários pagamentos ao longo do
    tempo (uma por ciclo renovado).
    """
    __tablename__ = "pagamentos"

    STATUS = ("pendente", "aprovado", "rejeitado", "cancelado", "estornado")

    id = db.Column(db.Integer, primary_key=True)
    licenca_id = db.Column(db.Integer, db.ForeignKey("licencas.id"), nullable=False)

    valor = db.Column(db.Numeric(10, 2), nullable=False)
    plano = db.Column(db.String(20), nullable=False)  # snapshot do plano pago (histórico não muda se o plano mudar depois)
    status = db.Column(db.String(20), nullable=False, default="pendente")

    mercadopago_preference_id = db.Column(db.String(100))
    mercadopago_payment_id = db.Column(db.String(100), index=True)
    mercadopago_status_detail = db.Column(db.String(100))

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    pago_em = db.Column(db.DateTime)

    licenca = db.relationship("Licenca", back_populates="pagamentos")

    def __repr__(self):
        return f"<Pagamento licenca={self.licenca_id} R${self.valor} {self.status}>"
