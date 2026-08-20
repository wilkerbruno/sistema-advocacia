from datetime import datetime
from app.extensions import db


class ConversaAgenteIA(db.Model):
    """
    Uma conversa com um dos agentes de IA jurídica (item 6 do briefing de
    paridade). Cada conversa pertence a um único usuário e a uma persona
    fixa — trocar de persona no meio de uma conversa muda o "especialista"
    que está respondendo, então abre uma conversa nova.
    """
    __tablename__ = "conversas_agente_ia"

    PERSONAS = ("operacao", "gestao", "negocios")

    id = db.Column(db.Integer, primary_key=True)

    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario")

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    persona = db.Column(db.String(20), nullable=False)  # ver PERSONAS
    titulo = db.Column(db.String(150))  # gerado a partir da 1ª pergunta, só pra listar

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mensagens = db.relationship("MensagemAgenteIA", back_populates="conversa",
                                 cascade="all, delete-orphan", order_by="MensagemAgenteIA.criado_em")

    def __repr__(self):
        return f"<ConversaAgenteIA {self.id} {self.persona} usuario={self.usuario_id}>"


class MensagemAgenteIA(db.Model):
    """Uma mensagem (do usuário ou do modelo) dentro de uma ConversaAgenteIA."""
    __tablename__ = "mensagens_agente_ia"

    PAPEIS = ("user", "assistant")

    id = db.Column(db.Integer, primary_key=True)

    conversa_id = db.Column(db.Integer, db.ForeignKey("conversas_agente_ia.id"), nullable=False)
    conversa = db.relationship("ConversaAgenteIA", back_populates="mensagens")

    papel = db.Column(db.String(10), nullable=False)  # ver PAPEIS
    conteudo = db.Column(db.Text, nullable=False)

    # "processando" | "pronta" — ver PENDENCIAS.md, seção -32 (fila de IA em
    # segundo plano). NULLABLE de propósito, mesmo tendo um "padrão" em
    # código: sincronizar_schema.py só sabe adicionar coluna sem DEFAULT no
    # banco, então NOT NULL quebraria a sincronização em bancos com
    # mensagens já cadastradas (seu caso em produção). `None`/qualquer valor
    # diferente de "processando" é tratado como "pronta" em todo o código —
    # nunca testar "== 'pronta'", sempre "== 'processando'" (ver
    # app/routes/agente_ia.py e templates/agente_ia/conversa.html).
    status = db.Column(db.String(20), default="pronta")

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<MensagemAgenteIA {self.id} {self.papel}>"


class AnaliseProcessoIA(db.Model):
    """
    Resumo dos autos ou rascunho de petição gerado pelo Agente de IA para UM
    processo específico — distinto das conversas de portfólio das personas
    Operação/Gestão/Negócios (que enxergam a carteira inteira, não um
    processo). Lê o histórico real do processo (andamentos, movimentações,
    publicações, decisões, prazos — ver app/utils/analise_processo_ia.py)
    e gera o texto pedido usando o mesmo motor local gratuito (ver
    app/utils/ia_local.py).

    Persistido para dar histórico/auditoria de cada geração — nunca é
    considerado texto final: `resultado` é sempre um rascunho para revisão
    humana antes de qualquer uso real (resumir para decisão, ou protocolar
    petição).
    """
    __tablename__ = "analises_processo_ia"

    TIPOS = ("resumo", "rascunho_peticao")

    id = db.Column(db.Integer, primary_key=True)

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo")

    solicitado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    solicitado_por = db.relationship("Usuario")

    tipo = db.Column(db.String(20), nullable=False)  # ver TIPOS
    instrucao = db.Column(db.Text)  # pedido de quem solicitou (obrigatório para rascunho_peticao)
    # nullable=False mas pode ser "" enquanto status="processando" (ver
    # abaixo) — o job de fundo preenche de verdade quando terminar.
    resultado = db.Column(db.Text, nullable=False, default="")
    # True quando o histórico do processo teve que ser cortado para caber na
    # janela de contexto do modelo local — sinaliza que o resumo/rascunho
    # pode não cobrir movimentações/decisões mais antigas.
    digest_truncado = db.Column(db.Boolean, default=False)

    # "processando" | "pronta" — mesma lógica/motivo de MensagemAgenteIA.status
    # acima (ver PENDENCIAS.md, seção -32) — NULLABLE de propósito, `None` é
    # tratado como "pronta" em todo o código.
    status = db.Column(db.String(20), default="pronta")

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<AnaliseProcessoIA {self.id} processo={self.processo_id} tipo={self.tipo}>"
