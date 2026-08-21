from datetime import datetime
from decimal import Decimal
from app.extensions import db


class Processo(db.Model):
    __tablename__ = "processos"

    STATUS = ("ativo", "suspenso", "arquivado", "encerrado")
    STATUS_COMERCIAL = ("contencioso_ativo", "suspenso", "encerrado")
    FORMAS_ACOMPANHAMENTO = ("automatico", "senha_processo", "manual", "nao_monitoravel")

    # Desfecho (BI/paridade item 4: taxa de sucesso, ganhos e perdas).
    # Só faz sentido preencher quando status == "encerrado"; um processo
    # ativo não tem desfecho ainda. "parcial" cobre acordo/ganho parcial,
    # que na prática jurídica não é nem vitória nem derrota total.
    DESFECHOS = ("ganho", "perda", "acordo", "parcial", "extinto_sem_resolucao")

    # Contingenciamento jurídico formal (seção 7 do briefing de paridade),
    # nos termos usuais de provisão contábil de contingência: provável,
    # possível ou remoto. Distinto de `classificacao_risco` (baixo/médio/alto),
    # que é uma leitura operacional e não segue a convenção de provisionamento.
    CLASSIFICACOES_CONTINGENCIA = ("provavel", "possivel", "remoto")
    PERCENTUAL_PADRAO_CONTINGENCIA = {"provavel": Decimal("100"), "possivel": Decimal("50"), "remoto": Decimal("0")}

    id = db.Column(db.Integer, primary_key=True)
    numero_processo = db.Column(db.String(40), index=True)  # nº CNJ, quando houver
    numero_interno = db.Column(db.String(40))  # nº de controle interno do escritório
    area_direito = db.Column(db.String(60), nullable=False)  # Cível, Trabalhista, Tributário...
    tipo_acao = db.Column(db.String(120))
    classe_processual = db.Column(db.String(120))  # classe CNJ (ex: Procedimento Comum Cível)
    assunto_cnj = db.Column(db.String(150))  # assunto conforme tabela CNJ
    fase = db.Column(db.String(60))  # Conhecimento, Recursal, Execução...
    estado_negocio_atual = db.Column(db.String(60))  # última tradução da máquina de estados (seção 6)
    instancia = db.Column(db.String(40))
    comarca = db.Column(db.String(100))
    vara = db.Column(db.String(100))
    tribunal = db.Column(db.String(60))
    # Slug do tribunal na API pública do DataJud (ver app/utils/tribunais_datajud.py
    # e app/utils/conector_datajud.py), ex: "trt2", "tjsp". Para processos da
    # Justiça do Trabalho isso é derivado automaticamente do próprio número CNJ;
    # para os demais segmentos (Estadual, Federal...) precisa ser escolhido aqui
    # manualmente para a captura automática funcionar — nunca é um chute do sistema.
    tribunal_datajud = db.Column(db.String(20))
    status = db.Column(db.String(20), default="ativo", nullable=False)
    status_comercial = db.Column(db.String(30), default="contencioso_ativo")  # seção 4
    polo_cliente = db.Column(db.String(20))  # Autor / Réu / Interessado
    parte_contraria = db.Column(db.String(150))
    advogado_contrario = db.Column(db.String(150))
    valor_causa = db.Column(db.Numeric(14, 2))
    data_distribuicao = db.Column(db.Date)
    descricao = db.Column(db.Text)
    segredo_justica = db.Column(db.Boolean, default=False)

    # Governança / captura automática (seções 3, 5, 8)
    forma_acompanhamento = db.Column(db.String(20), default="automatico")  # ver FORMAS_ACOMPANHAMENTO
    monitoravel = db.Column(db.Boolean, default=True)  # False = "buraco silencioso" sinalizado no painel
    motivo_nao_monitoravel = db.Column(db.String(255))  # ex: "segredo sem senha", "erro de captura"
    classificacao_risco = db.Column(db.String(20))  # baixo, medio, alto — preenchido por humano
    ultima_captura_em = db.Column(db.DateTime)  # última vez que a rotina de captura rodou com sucesso
    ultima_movimentacao_em = db.Column(db.DateTime)  # usado para "parado há N dias" (painel, seção 8)

    # BI: desfecho e duração (preenchidos quando o processo é encerrado)
    desfecho = db.Column(db.String(30))  # ver DESFECHOS — só relevante quando status == "encerrado"
    data_encerramento = db.Column(db.Date)
    observacao_desfecho = db.Column(db.String(255))

    # Contingenciamento jurídico formal (provisão contábil)
    classificacao_contingencia = db.Column(db.String(20))  # ver CLASSIFICACOES_CONTINGENCIA
    percentual_provisionamento = db.Column(db.Numeric(5, 2))  # override manual do % padrão da classificação

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

    # Núcleo de governança/captura automática (briefing)
    movimentacoes = db.relationship("Movimentacao", back_populates="processo",
                                     cascade="all, delete-orphan", order_by="desc(Movimentacao.data)")
    publicacoes = db.relationship("Publicacao", back_populates="processo",
                                   cascade="all, delete-orphan", order_by="desc(Publicacao.data_publicacao)")
    decisoes = db.relationship("Decisao", back_populates="processo",
                                cascade="all, delete-orphan", order_by="desc(Decisao.data)")
    historico_estados = db.relationship("HistoricoEstadoProcesso", back_populates="processo",
                                         cascade="all, delete-orphan",
                                         order_by="HistoricoEstadoProcesso.data_evento")
    senha_processo = db.relationship("SenhaProcesso", back_populates="processo",
                                      uselist=False, cascade="all, delete-orphan")

    # Lista de acesso explícita para processo sigiloso (segredo_justica=True)
    # — ver ProcessoAcessoRestrito abaixo e app/utils/acesso.py::
    # usuario_pode_ver_processo. Só é consultada quando segredo_justica é
    # True; num processo normal não tem efeito nenhum (acesso continua só
    # por unidade, como sempre).
    acessos_restritos = db.relationship("ProcessoAcessoRestrito", back_populates="processo",
                                         cascade="all, delete-orphan")

    @property
    def percentual_provisionamento_efetivo(self):
        """Percentual usado no cálculo da provisão: o valor definido manualmente
        pelo usuário, ou o padrão da classificação (provável=100%, possível=50%,
        remoto=0%) quando não houver override."""
        if self.percentual_provisionamento is not None:
            return self.percentual_provisionamento
        if self.classificacao_contingencia in self.PERCENTUAL_PADRAO_CONTINGENCIA:
            return self.PERCENTUAL_PADRAO_CONTINGENCIA[self.classificacao_contingencia]
        return None

    @property
    def valor_provisionado(self):
        """Valor de provisão de contingência: valor da causa × percentual
        efetivo da classificação. None quando falta valor da causa ou
        classificação (não entra nos totais até ser classificado)."""
        percentual = self.percentual_provisionamento_efetivo
        if percentual is None or self.valor_causa is None:
            return None
        return self.valor_causa * (percentual / Decimal("100"))

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
    """
    Prazos processuais e obrigações a cumprir — motor de prazos (seção 7).

    Governança central do projeto: o prazo só fecha como cumprido quando
    há evidência (movimentação de protocolo capturada automaticamente ou
    documento comprobatório anexado). Marcar "feito" no botão sozinho
    NÃO fecha o prazo — por isso `status` inclui "aguardando_evidencia" e
    o fechamento definitivo (`cumprido`) exige `evidencia_documento_id`
    ou `evidencia_movimentacao_id` preenchidos.

    "historico_anterior" (ver PENDENCIAS.md, seção -33): status distinto de
    "cumprido" e de "perdido" — usado SÓ para prazos gerados automaticamente
    a partir de movimentações históricas capturadas na carga inicial (ao
    cadastrar um processo pelo CNJ, o sistema busca o histórico completo do
    tribunal, e movimentações antigas que batem com uma regra cadastrada
    geram um Prazo com vencimento já no passado). Esse prazo nunca teve
    chance de ser fechado com evidência de verdade (o escritório não estava
    usando o sistema na época) — mas também não é honesto marcar como
    "perdido" de verdade, já que o processo seguiu tramitando normalmente.
    Por isso este status é NEUTRO: some da contagem de "pendentes"/
    "perdidos" dos painéis (que sempre filtram por status=="pendente"), mas
    nunca é apagado nem finge ter evidência de cumprimento — fica sempre
    visível na aba Prazos do processo, com o motivo e quem aplicou
    registrados (ver regularizar_prazos_historico em app/routes/processos.py).
    """
    __tablename__ = "prazos"

    STATUS = ("pendente", "em_elaboracao", "protocolado_aguardando_evidencia", "cumprido", "perdido", "historico_anterior")
    PRIORIDADES = ("baixa", "normal", "alta", "urgente")

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="prazos")

    # Origem do prazo (rastreabilidade até a publicação que o gerou)
    publicacao_id = db.Column(db.Integer, db.ForeignKey("publicacoes.id"), nullable=True)
    publicacao = db.relationship("Publicacao", back_populates="prazos")
    tipo_ato = db.Column(db.String(120))  # ex: "Citação/intimação para contestar"
    regra_aplicada_id = db.Column(db.Integer, db.ForeignKey("regras_proxima_acao.id"), nullable=True)

    descricao = db.Column(db.String(255), nullable=False)
    data_inicial = db.Column(db.Date)  # data de publicação/ciência que inicia a contagem
    data_vencimento = db.Column(db.Date, nullable=False)  # data fatal calculada (sempre editável)
    calculo_automatico = db.Column(db.Boolean, default=False)  # True quando veio do motor de prazos
    prioridade = db.Column(db.String(20), default="normal")
    status = db.Column(db.String(30), default="pendente")
    observacoes = db.Column(db.Text)

    # Evidência de cumprimento — mecanismo central de governança (seção 7.2).
    # Relationships (não são coluna nova, só leitura conveniente do que já
    # existe — não precisa de sincronizar_schema.py) adicionadas pra exibir
    # a evidência completa (data/texto da movimentação, ou nome/data do
    # documento) na aba Prazos, em vez de só o status "cumprido" sem
    # detalhe nenhum — ver PENDENCIAS.md, seção -36.
    evidencia_movimentacao_id = db.Column(db.Integer, db.ForeignKey("movimentacoes.id"), nullable=True)
    evidencia_movimentacao = db.relationship("Movimentacao", foreign_keys=[evidencia_movimentacao_id])
    evidencia_documento_id = db.Column(db.Integer, db.ForeignKey("documentos.id"), nullable=True)
    evidencia_documento = db.relationship("Documento", foreign_keys=[evidencia_documento_id])
    cumprido_em = db.Column(db.DateTime)

    # Auditoria de alteração manual da data fatal (seção 7: "sempre editável,
    # com registro no log de quem alterou e por quê")
    data_original_calculada = db.Column(db.Date)
    motivo_alteracao_data = db.Column(db.String(255))
    alterado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    alterado_por = db.relationship("Usuario", foreign_keys=[alterado_por_id])

    # Auditoria da regularização em lote pra status="historico_anterior" (ver
    # PENDENCIAS.md, seção -33 e docstring da classe acima) — quem, quando e
    # por quê, mesmo espírito do par alterado_por_id/motivo_alteracao_data
    # acima, mas em campos próprios pra não confundir os dois motivos
    # diferentes de alteração no mesmo prazo.
    motivo_regularizacao = db.Column(db.Text)
    regularizado_em = db.Column(db.DateTime)
    regularizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    regularizado_por = db.relationship("Usuario", foreign_keys=[regularizado_por_id])

    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    responsavel = db.relationship("Usuario", foreign_keys=[responsavel_id])

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    # Governança (seção 4 e 11): nunca exclusão física de prazo — somente soft delete.
    deletado_em = db.Column(db.DateTime, nullable=True)

    # Lembrete automático (ver PENDENCIAS.md, seção -44, e
    # enviar_lembretes_prazos_audiencias.py) — marca que o lembrete já foi
    # disparado pra este prazo, pra nunca mandar duas vezes mesmo que o job
    # rode várias vezes seguidas (mesmo padrão de
    # Compromisso.notificacao_enviada_em). Nullable de propósito, como toda
    # coluna nova deste projeto (ver sincronizar_schema.py).
    lembrete_enviado_em = db.Column(db.DateTime, nullable=True)


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

    # Lembrete automático (ver PENDENCIAS.md, seção -44) — mesmo mecanismo
    # de Prazo.lembrete_enviado_em acima.
    lembrete_enviado_em = db.Column(db.DateTime, nullable=True)


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


class ProcessoAcessoRestrito(db.Model):
    """
    Lista de acesso de um processo marcado como sigiloso
    (`Processo.segredo_justica=True`) — correção de segurança (ver
    PENDENCIAS.md seção -28 e AUDITORIA_GRANDE_PORTE.md item 1.3):
    `segredo_justica` antes era só um rótulo visual, sem nenhum efeito
    real sobre quem conseguia abrir o processo — qualquer usuário da
    mesma unidade via.

    Cada linha aqui é "usuário X pode ver o processo Y", ALÉM das pessoas
    que já têm acesso automaticamente mesmo sem estar nesta lista: admin
    da empresa (admin sempre vê tudo da própria empresa, sigiloso ou não
    — mesma regra que já vale hoje pra qualquer outro dado), o
    responsável pelo processo (`Processo.responsavel_id`) e quem cadastrou
    (`Processo.criado_por_id`). Um processo SEM `segredo_justica` nunca
    consulta esta tabela — continua valendo só a regra de unidade de
    sempre.
    """
    __tablename__ = "processos_acesso_restrito"
    __table_args__ = (db.UniqueConstraint("processo_id", "usuario_id", name="uq_processo_usuario_acesso"),)

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo", back_populates="acessos_restritos")

    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])

    concedido_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    concedido_por = db.relationship("Usuario", foreign_keys=[concedido_por_id])
    concedido_em = db.Column(db.DateTime, default=datetime.utcnow)
