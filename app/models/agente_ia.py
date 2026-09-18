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

    # Documento de referência de estilo (PENDENCIAS.md, seção -53) — opcional,
    # só usado em rascunho_peticao. Guarda qual documento JÁ anexado ao
    # processo foi usado como referência de estrutura/estilo pro rascunho
    # (nunca de fato/conteúdo — ver app/utils/analise_processo_ia.py). Fica
    # NULL quando nenhuma referência foi escolhida, ou quando o texto do
    # documento escolhido não pôde ser extraído (nesse caso a geração segue
    # sem referência, nunca bloqueia por isso).
    documento_referencia_id = db.Column(db.Integer, db.ForeignKey("documentos.id"), nullable=True)
    documento_referencia = db.relationship("Documento")

    # Dossiê por tipo de peça (item 4 da lista de pipeline de IA jurídica —
    # PENDENCIAS.md, seção -101): quando informado, muda quais documentos
    # `montar_digest_processo` prioriza (ver app/utils/analise_processo_ia.py,
    # TIPOS_PECA_COM_DOSSIE). Só faz sentido em rascunho_peticao; fica NULL
    # em "resumo" e também em rascunhos sem tipo de peça específico escolhido
    # (segue o comportamento genérico anterior a esta funcionalidade).
    tipo_peca = db.Column(db.String(20), nullable=True)

    # Biblioteca de modelos do escritório (item 10 — PENDENCIAS.md, seção
    # -107): qual ModeloPeca foi aplicado automaticamente nesta geração
    # (ver app/models/modelo_peca.py::resolver_modelo_peca), só pra exibir
    # de forma transparente qual modelo influenciou o texto — NULL quando
    # nenhum modelo do escritório casou tipo_peca/área no momento da
    # geração (a geração nunca é bloqueada por falta de modelo).
    modelo_peca_id = db.Column(db.Integer, db.ForeignKey("modelos_peca.id"), nullable=True)
    modelo_peca = db.relationship("ModeloPeca")

    # Delimitação do objeto (item 7 da mesma lista — PENDENCIAS.md, seção
    # -101): "a minuta só começa depois disso resolvido". Obrigatório para
    # rascunho_peticao (ver validação em app/routes/processos.py::
    # gerar_analise_ia e em app/utils/analise_processo_ia.py::gerar_analise),
    # nunca preenchido em "resumo". `uselist=False` porque cada análise tem
    # NO MÁXIMO uma delimitação própria (histórico completo fica em
    # DelimitacaoObjeto.processo, não aqui).
    delimitacao_objeto = db.relationship("DelimitacaoObjeto", back_populates="analise", uselist=False)

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


class DelimitacaoObjeto(db.Model):
    """
    Delimitação do objeto da peça (item 7 da lista de pipeline de IA
    jurídica trazida pelo usuário — PENDENCIAS.md, seção -101): "matéria de
    fato e matéria de direito controvertidas, tese a sustentar, o que se
    ataca da decisão e o resultado pretendido. A minuta só começa depois
    disso resolvido."

    Preenchida pelo próprio advogado (texto livre — nada aqui é inferido
    automaticamente do processo) na mesma tela onde pede o rascunho de
    petição, e É OBRIGATÓRIA para gerar um rascunho: `gerar_analise_ia`
    (app/routes/processos.py) recusa a geração de "rascunho_peticao" sem
    isso preenchido, e `gerar_analise` (app/utils/analise_processo_ia.py)
    repete a mesma checagem como segunda camada de defesa (mesmo padrão já
    usado ali para `instrucao`).

    Guarda UMA linha por rascunho gerado (nunca sobrescreve a anterior) —
    processos que voltam a pedir rascunho depois (ex.: contestação e,
    meses depois, um recurso) legitimamente têm tese/objeto diferentes a
    cada vez, e apagar o histórico anterior perderia a auditoria de qual
    delimitação embasou qual peça já gerada.
    """
    __tablename__ = "delimitacoes_objeto"

    id = db.Column(db.Integer, primary_key=True)

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo")

    # Preenchido pela rota logo após criar a AnaliseProcessoIA correspondente
    # (precisa do id dela) — nullable só por causa dessa ordem de criação,
    # nunca fica None depois de salvo pela rota (ver processos.py).
    analise_id = db.Column(db.Integer, db.ForeignKey("analises_processo_ia.id"), nullable=True)
    analise = db.relationship("AnaliseProcessoIA", back_populates="delimitacao_objeto")

    materia_fato = db.Column(db.Text, nullable=False)  # matéria de fato controvertida
    materia_direito = db.Column(db.Text, nullable=False)  # matéria de direito controvertida
    tese_a_sustentar = db.Column(db.Text, nullable=False)
    # Só faz sentido em peça recursal ("o que se ataca da decisão") —
    # opcional porque nem toda peça é um recurso (ex.: contestação não
    # ataca decisão nenhuma, ataca a inicial).
    ataca_da_decisao = db.Column(db.Text, nullable=True)
    resultado_pretendido = db.Column(db.Text, nullable=False)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    criado_por = db.relationship("Usuario")
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<DelimitacaoObjeto {self.id} processo={self.processo_id}>"
