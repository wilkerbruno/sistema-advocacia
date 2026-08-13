from datetime import datetime
from app.extensions import db


class Feriado(db.Model):
    """
    Calendário de feriados forenses (seção 7), usado pelo motor de
    prazos para o cálculo em dias úteis (CPC art. 219). Alimentado por
    tabela e atualizável — inclui feriados nacionais e locais por
    tribunal, além do recesso forense (20/12 a 20/01).
    """
    __tablename__ = "feriados_forenses"

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.Date, nullable=False, index=True)
    descricao = db.Column(db.String(150), nullable=False)
    tribunal = db.Column(db.String(20), nullable=True)  # nulo = feriado nacional, vale para todos
    abrange_todo_periodo = db.Column(db.Boolean, default=False)  # ex: recesso forense (intervalo)
    data_fim = db.Column(db.Date, nullable=True)  # usado junto com abrange_todo_periodo


class LogCaptura(db.Model):
    """
    Observabilidade obrigatória das rotinas de captura automática
    (seção 14): log por execução, taxa de sucesso por tribunal/fonte,
    e é a partir daqui que se dispara alerta quando a captura falha
    2 dias seguidos.
    """
    __tablename__ = "logs_captura"

    id = db.Column(db.Integer, primary_key=True)
    fonte = db.Column(db.String(60), nullable=False)  # judit, escavador, digesto, codilo, dje-scraper...
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=True)
    tribunal = db.Column(db.String(30))
    status = db.Column(db.String(20), nullable=False)  # sucesso, falha, parcial
    mensagem = db.Column(db.String(500))
    duracao_ms = db.Column(db.Integer)
    executado_em = db.Column(db.DateTime, default=datetime.utcnow)
