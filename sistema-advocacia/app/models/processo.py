from datetime import datetime
from app.extensions import db


class Processo(db.Model):
    __tablename__ = "processos"

    STATUS = ("ativo", "suspenso", "arquivado", "encerrado")

    id = db.Column(db.Integer, primary_key=True)
    numero_processo = db.Column(db.String(40), index=True)  # nº CNJ, quando houver
    numero_interno = db.Column(db.String(40))  # nº de controle interno do escritório
    area_direito = db.Column(db.String(60), nullable=False)  # Cível, Trabalhista, Tributário...
    tipo_acao = db.Column(db.String(120))
    fase = db.Column(db.String(60))  # Conhecimento, Recursal, Execução...
    instancia = db.Column(db.String(40))
    comarca = db.Column(db.String(100))
    vara = db.Column(db.String(100))
    tribunal = db.Column(db.String(60))
    status = db.Column(db.String(20), default="ativo", nullable=False)
    polo_cliente = db.Column(db.String(20))  # Autor / Réu / Interessado
    parte_contraria = db.Column(db.String(150))
    advogado_contrario = db.Column(db.String(150))
    valor_causa = db.Column(db.Numeric(14, 2))
    data_distribuicao = db.Column(db.Date)
    descricao = db.Column(db.Text)
    segredo_justica = db.Column(db.Boolean, default=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade", back_populates="processos")

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    cliente = db.relationship("Cliente", back_populates="processos")

    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    responsavel = db.relationship("Usuario", foreign_keys=[responsavel_id])

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])

    andamentos = db.relationship("Andamento", back_populates="processo",
                                  cascade="all, delete-orphan", order_by="desc(Andamento.data)")
    prazos = db.relationship("Prazo", back_populates="processo",
                              cascade="all, delete-orphan", order_by="Prazo.data_vencimento")
    audiencias = db.relationship("Audiencia", back_populates="processo",
                                  cascade="all, delete-orphan", order_by="Audiencia.data_hora")
    documentos = db.relationship("Documento", back_populates="processo",
                                  cascade="all, delete-orphan", order_by="desc(Documento.enviado_em)")
    lancamentos = db.relationship("Lancamento", back_populates="processo", lazy="dynamic")

    def __repr__(self):
        return f"<Processo {self.numero_processo or self.numero_interno}>"


class Andamento(db.Model):
    """Linha do tempo / movimentações do processo."""
    __tablename__ = "andamentos"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="andamentos")

    data = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    tipo = db.Column(db.String(40), default="movimentacao")  # movimentacao, peticao, decisao, contato
    descricao = db.Column(db.Text, nullable=False)

    registrado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    registrado_por = db.relationship("Usuario")


class Prazo(db.Model):
    """Prazos processuais e obrigações a cumprir."""
    __tablename__ = "prazos"

    STATUS = ("pendente", "cumprido", "perdido")
    PRIORIDADES = ("baixa", "normal", "alta", "urgente")

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="prazos")

    descricao = db.Column(db.String(255), nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    prioridade = db.Column(db.String(20), default="normal")
    status = db.Column(db.String(20), default="pendente")
    observacoes = db.Column(db.Text)
    cumprido_em = db.Column(db.DateTime)

    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    responsavel = db.relationship("Usuario")

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)


class Audiencia(db.Model):
    __tablename__ = "audiencias"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="audiencias")

    tipo = db.Column(db.String(60))  # conciliação, instrução, julgamento...
    data_hora = db.Column(db.DateTime, nullable=False)
    local = db.Column(db.String(200))
    modalidade = db.Column(db.String(20), default="presencial")  # presencial, virtual, hibrida
    link_virtual = db.Column(db.String(255))
    status = db.Column(db.String(20), default="agendada")  # agendada, realizada, cancelada, remarcada
    observacoes = db.Column(db.Text)

    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    responsavel = db.relationship("Usuario")


class Documento(db.Model):
    __tablename__ = "documentos"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="documentos")

    nome_original = db.Column(db.String(255), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)  # nome salvo em disco (único)
    categoria = db.Column(db.String(60), default="outros")  # peticao, procuracao, contrato, decisao...
    tamanho_kb = db.Column(db.Integer)
    enviado_em = db.Column(db.DateTime, default=datetime.utcnow)

    enviado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    enviado_por = db.relationship("Usuario")
