from datetime import datetime
from app.extensions import db


class Apontamento(db.Model):
    """
    Apontamento de horas trabalhadas (timesheet) — item 7 do briefing de
    paridade ("timesheet, controle de horas trabalhadas"). Pode ou não
    estar vinculado a um processo (trabalho administrativo/comercial
    também é apontável). Cada apontamento pertence a um único usuário —
    só quem apontou (ou um admin) pode excluir, mesma regra usada no
    cofre de senha de processo.
    """
    __tablename__ = "apontamentos_horas"

    id = db.Column(db.Integer, primary_key=True)

    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario")

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=True)
    processo = db.relationship("Processo")

    data = db.Column(db.Date, nullable=False)
    horas = db.Column(db.Numeric(5, 2), nullable=False)  # ex: 1.50 = 1h30
    descricao = db.Column(db.String(255), nullable=False)
    faturavel = db.Column(db.Boolean, default=True, nullable=False)

    # Vínculo com o lançamento financeiro que cobrou estas horas (ver
    # PENDENCIAS.md, seção -39, e financeiro.gerar_cobranca_horas) — nulo
    # enquanto o apontamento ainda não foi faturado. Um apontamento só pode
    # ser vinculado a UM lançamento por vez; a tela de gerar cobrança só
    # oferece apontamentos com este campo vazio, pra nunca cobrar a mesma
    # hora duas vezes.
    lancamento_id = db.Column(db.Integer, db.ForeignKey("lancamentos_financeiros.id"), nullable=True)
    lancamento = db.relationship("Lancamento", back_populates="apontamentos")

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Apontamento usuario={self.usuario_id} {self.data} {self.horas}h>"
