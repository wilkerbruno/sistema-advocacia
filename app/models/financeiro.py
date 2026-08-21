from datetime import datetime
from app.extensions import db


class Lancamento(db.Model):
    """Lançamento financeiro: honorários a receber, custas, despesas da unidade."""
    __tablename__ = "lancamentos_financeiros"

    TIPOS = ("honorario", "custas", "despesa", "outro")
    NATUREZAS = ("receita", "despesa")
    STATUS = ("pendente", "pago", "atrasado", "cancelado")
    MODELOS_COBRANCA = ("fixo", "exito", "retainer")

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

    # Conta de terceiros (ver PENDENCIAS.md, seção -39): valor que PASSA
    # pelo escritório mas não é receita/despesa própria — ex: depósito
    # judicial, valor recebido em nome do cliente para repasse. Segregado
    # do caixa operacional: todo total/soma do painel financeiro (a
    # receber, recebido no mês, atrasado) filtra explicitamente por essa
    # coluna, e uma seção separada mostra só os valores de terceiros.
    # Nunca migra sozinho de um tipo pro outro — é uma escolha explícita
    # no momento do lançamento.
    #
    # nullable=True DE PROPÓSITO (mesmo o valor "de fato" sendo sempre
    # True/False, nunca ambíguo): sincronizar_schema.py aplica coluna nova
    # via `ALTER TABLE ... ADD COLUMN ... NOT NULL` SEM cláusula DEFAULT —
    # em MySQL, isso quebra (erro 1364) numa tabela `lancamentos_financeiros`
    # que já tem linhas, que é exatamente o caso em produção. Deixando
    # opcional, o ALTER sempre funciona (linhas antigas ficam com NULL) e o
    # código trata NULL como equivalente a "não é de terceiros" em todo
    # lugar que filtra por este campo — nunca comparar com `== False` puro
    # (em SQL, `NULL = 0` não é verdadeiro, then filtraria pra fora as
    # linhas antigas); usar sempre `.is_(True)` / `.is_(False) ou is_(None)`.
    conta_terceiros = db.Column(db.Boolean, default=False, nullable=True)

    # Modelo de cobrança (ver PENDENCIAS.md, seção -40): registra COMO o
    # valor deste lançamento foi combinado com o cliente — não muda em
    # nada o cálculo do caixa (o campo `valor` continua sendo sempre o que
    # de fato entra/sai), é só rastreabilidade e apoio visual.
    # None/"fixo" (padrão, comportamento de sempre): valor combinado direto.
    # "exito": percentual sobre um valor-base (normalmente valor da causa
    # ou valor recuperado no acordo) — `percentual_exito` e
    # `valor_base_exito` guardam como o valor sugerido foi calculado, só
    # para conferência futura; o valor final digitado por quem lançou é
    # sempre o que vale, igual ao padrão já usado em
    # `gerar_cobranca_horas` (sugestão nunca é aplicada sozinha).
    # "retainer": mensalidade fixa recorrente — sem tabela de recorrência
    # própria (não há fila/agendador neste projeto); o botão "Gerar
    # cobrança do próximo mês" na tela Financeiro duplica manualmente o
    # lançamento com o vencimento do mês seguinte, sempre uma ação
    # explícita de quem está usando o sistema.
    modelo_cobranca = db.Column(db.String(20), nullable=True)
    percentual_exito = db.Column(db.Numeric(5, 2), nullable=True)
    valor_base_exito = db.Column(db.Numeric(14, 2), nullable=True)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade")

    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=True)
    processo = db.relationship("Processo", back_populates="lancamentos")

    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)
    cliente = db.relationship("Cliente")

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario")

    apontamentos = db.relationship("Apontamento", back_populates="lancamento")

    # Alçada de aprovação em múltiplos níveis (ver app/utils/alcada.py e
    # PENDENCIAS.md, seção -50) — cada linha aqui é UMA aprovação
    # concedida por UM usuário; quantas são exigidas antes do lançamento
    # poder ser marcado como pago depende do valor e da configuração da
    # empresa (Empresa.alcada_nivel1_valor/alcada_nivel2_valor), nunca
    # deste modelo. cascade="all, delete-orphan": se o lançamento em si
    # for excluído, as aprovações associadas somem junto (não fazem
    # sentido soltas, sem o lançamento que aprovavam).
    aprovacoes = db.relationship("AprovacaoLancamento", back_populates="lancamento",
                                  cascade="all, delete-orphan", order_by="AprovacaoLancamento.aprovado_em")


class AprovacaoLancamento(db.Model):
    """
    Uma aprovação concedida por um usuário a um lançamento financeiro
    (sempre despesa — ver app/utils/alcada.py) que ultrapassou a alçada
    configurada pela empresa. Nunca é criada/apagada por conta própria do
    sistema — é sempre uma ação explícita de um admin/gestor, via
    /financeiro/<id>/aprovar (ver app/routes/financeiro.py).
    """
    __tablename__ = "aprovacoes_lancamento"

    id = db.Column(db.Integer, primary_key=True)
    lancamento_id = db.Column(db.Integer, db.ForeignKey("lancamentos_financeiros.id"), nullable=False)
    aprovador_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    aprovado_em = db.Column(db.DateTime, default=datetime.utcnow)
    comentario = db.Column(db.Text, nullable=True)

    lancamento = db.relationship("Lancamento", back_populates="aprovacoes")
    aprovador = db.relationship("Usuario")
