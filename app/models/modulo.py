from datetime import datetime
from app.extensions import db


class Modulo(db.Model):
    """
    Catálogo de módulos que a plataforma vende — gerenciado pelo admin
    desenvolvedor em /plataforma/modulos, NUNCA por empresa cliente.

    `chave`: precisa ser EXATAMENTE igual ao nome do blueprint Flask que
    esse módulo controla (ver app/__init__.py, lista de
    app.register_blueprint(...)) — ex.: "processos", "financeiro",
    "agenda". É assim que o bloqueio de acesso (ver
    app/utils/modulos.py::modulo_liberado_para) descobre a que módulo uma
    tela pertence: olha só `request.blueprint` e procura essa chave aqui,
    sem precisar de nenhum mapa manual redundante.

    `obrigatorio`: módulo que TODA empresa tem, sempre — não aparece como
    opção de seleção/solicitação, o gate nunca bloqueia (ex.: Clientes,
    o cadastro básico que sustenta o resto do sistema). Módulos não
    obrigatórios só ficam liberados para uma empresa através de uma linha
    em EmpresaModulo com status "incluido_inicial" ou "ativo".

    `preco_sugerido`: só um valor de referência pro admin desenvolvedor se
    basear ao negociar (auto-preenche o campo, mas sempre editável) — nunca
    é mostrado a nenhuma empresa cliente como "tabela de preços", no mesmo
    espírito de Licenca.valor_negociado (ver app/models/licenca.py).
    """
    __tablename__ = "modulos"

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(40), nullable=False, unique=True)
    nome = db.Column(db.String(80), nullable=False)
    descricao = db.Column(db.String(300))
    preco_sugerido = db.Column(db.Numeric(10, 2))
    obrigatorio = db.Column(db.Boolean, nullable=False, default=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True)  # False = retirado do catálogo (histórico preservado)
    ordem_exibicao = db.Column(db.Integer, nullable=False, default=0)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    empresas_associadas = db.relationship("EmpresaModulo", back_populates="modulo")

    def __repr__(self):
        return f"<Modulo {self.chave}>"


class EmpresaModulo(db.Model):
    """
    Associação empresa↔módulo — uma linha por combinação (única, ver
    UniqueConstraint abaixo), o `status` é que muda ao longo do tempo em
    vez de acumular linhas novas (mesmo espírito de Licenca: um registro
    vivo por empresa, não um log de eventos).

    Ciclo de vida do `status`:
      "incluido_inicial" -> selecionado pelo admin desenvolvedor no
        cadastro da empresa (/plataforma/empresas/nova), ANTES do primeiro
        pagamento — faz parte do pacote negociado desde o início.
      "solicitado" -> a própria empresa cliente pediu esse módulo depois
        (ver /licenciamento/modulos), ainda sem preço/aprovação definidos.
      "ativo" -> liberado de fato pro uso, seja porque veio do pacote
        inicial e a licença já está paga, seja porque um pedido posterior
        foi aprovado e precificado pelo admin desenvolvedor.
      "cancelado" -> desativado (removido do pacote, ou pedido recusado).

    Módulo liberado pro gate de acesso = status em
    ("incluido_inicial", "ativo") — ver app/utils/modulos.py.
    """
    __tablename__ = "empresa_modulos"
    __table_args__ = (db.UniqueConstraint("empresa_id", "modulo_id", name="uq_empresa_modulo"),)

    STATUS = ("incluido_inicial", "solicitado", "ativo", "cancelado")

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    modulo_id = db.Column(db.Integer, db.ForeignKey("modulos.id"), nullable=False)

    status = db.Column(db.String(20), nullable=False, default="solicitado")
    valor_adicional = db.Column(db.Numeric(10, 2))  # quanto esse módulo soma na mensalidade da empresa

    solicitado_em = db.Column(db.DateTime)
    solicitado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))  # usuário da própria empresa cliente

    ativado_em = db.Column(db.DateTime)
    definido_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))  # admin desenvolvedor que incluiu/aprovou

    cancelado_em = db.Column(db.DateTime)
    observacao = db.Column(db.String(300))

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = db.relationship("Empresa", back_populates="modulos_associados")
    modulo = db.relationship("Modulo", back_populates="empresas_associadas")
    solicitado_por = db.relationship("Usuario", foreign_keys=[solicitado_por_id])
    definido_por = db.relationship("Usuario", foreign_keys=[definido_por_id])

    def esta_liberado(self):
        return self.status in ("incluido_inicial", "ativo")

    def __repr__(self):
        return f"<EmpresaModulo empresa={self.empresa_id} modulo={self.modulo_id} {self.status}>"
