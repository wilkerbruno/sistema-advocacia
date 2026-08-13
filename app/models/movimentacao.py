from datetime import datetime
from app.extensions import db


class Movimentacao(db.Model):
    """
    Movimentação processual capturada automaticamente da fonte pública
    (tribunal / provedor de dados processuais), distinta do `Andamento`
    (que é a anotação manual/interna da equipe).

    Requisito do briefing (seção 4 e 5): a fonte primária da verdade é a
    consulta pública / diário eletrônico. O humano nunca digita o que
    aconteceu no processo — só classifica e decide em cima do que chegou
    aqui.
    """
    __tablename__ = "movimentacoes"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="movimentacoes")

    data = db.Column(db.DateTime, nullable=False)
    codigo_tpu = db.Column(db.String(20), index=True)  # código da Tabela Processual Unificada (CNJ)
    texto_integral = db.Column(db.Text, nullable=False)
    origem_captura = db.Column(db.String(60))  # ex: judit, escavador, digesto, codilo, manual
    hash_dedup = db.Column(db.String(64), unique=True, index=True)  # hash do conteúdo p/ deduplicação

    # Tradução para estado de negócio (camada de vocabulário, seção 6)
    estado_negocio_resultante = db.Column(db.String(60))
    triagem_pendente = db.Column(db.Boolean, default=False)  # True quando código TPU não mapeado

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    # Governança (seção 4): nunca exclusão física — somente soft delete.
    deletado_em = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<Movimentacao {self.processo_id} {self.data}>"


class Publicacao(db.Model):
    """Publicação em diário oficial / DJE vinculada a um processo."""
    __tablename__ = "publicacoes"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="publicacoes")

    diario = db.Column(db.String(100))  # ex: DJE-SP, DOU, DEJT
    data_disponibilizacao = db.Column(db.Date)
    data_publicacao = db.Column(db.Date)  # início da contagem de prazo
    teor = db.Column(db.Text)
    oab_destinataria = db.Column(db.String(30))
    origem_captura = db.Column(db.String(60))
    hash_dedup = db.Column(db.String(64), unique=True, index=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    prazos = db.relationship("Prazo", back_populates="publicacao")


class Decisao(db.Model):
    """
    Decisões/sentenças/acórdãos — base da camada de jurimetria (seção 13).
    Persistir inteiro teor quando publicamente disponível.
    """
    __tablename__ = "decisoes"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="decisoes")

    tipo = db.Column(db.String(30))  # despacho, decisao, sentenca, acordao
    orgao_julgador = db.Column(db.String(150))
    magistrado_relator = db.Column(db.String(150))
    data = db.Column(db.Date)
    resultado = db.Column(db.String(60))  # procedente, improcedente, parcial, provido, negado...
    tese = db.Column(db.Text)
    inteiro_teor = db.Column(db.Text)  # quando publicamente disponível
    origem_captura = db.Column(db.String(60))
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
