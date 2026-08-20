from datetime import datetime
from app.extensions import db


class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    tipo_pessoa = db.Column(db.String(2), nullable=False, default="PF")  # PF ou PJ
    nome = db.Column(db.String(150), nullable=False)  # nome ou razão social
    cpf_cnpj = db.Column(db.String(20), index=True)
    rg_ie = db.Column(db.String(30))
    email = db.Column(db.String(120))
    telefone = db.Column(db.String(30))
    whatsapp = db.Column(db.String(30))
    endereco = db.Column(db.String(255))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    cep = db.Column(db.String(10))
    observacoes = db.Column(db.Text)
    # Taxa horária padrão deste cliente (ver PENDENCIAS.md, seção -39) — usada
    # só como SUGESTÃO inicial de valor na tela "Gerar cobrança a partir das
    # horas do período" (app/routes/financeiro.py::gerar_cobranca_horas);
    # nunca gera lançamento sozinha, o valor final é sempre revisado e pode
    # ser editado antes de confirmar. Nula quando o cliente não tem uma taxa
    # fixa combinada (ex: contrato de êxito, valor fechado por caso).
    valor_hora_padrao = db.Column(db.Numeric(10, 2), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    # Ferramentas de LGPD (ver PENDENCIAS.md, seção -43) — base legal de
    # tratamento e consentimento são só REGISTRO/documentação (o sistema não
    # decide sozinho se há base legal válida, isso é avaliação jurídica de
    # quem cadastra); todas nullable de propósito, tanto porque cliente
    # cadastrado antes desta seção existir não tem esse dado quanto pelo
    # motivo de sempre (sincronizar_schema.py não aplica NOT NULL sem
    # DEFAULT numa tabela `clientes` que já tem linha).
    BASES_LEGAIS = ("consentimento", "contrato", "obrigacao_legal", "legitimo_interesse", "outra")
    base_legal_tratamento = db.Column(db.String(30), nullable=True)
    consentimento_obtido_em = db.Column(db.Date, nullable=True)
    consentimento_observacoes = db.Column(db.Text, nullable=True)

    # Anonimização (direito ao esquecimento, art. 18 LGPD) — ver
    # app/utils/lgpd.py::anonimizar_cliente. Quando preenchido, os campos de
    # dado pessoal identificável deste cliente (nome, cpf_cnpj, contatos,
    # endereço) já foram sobrescritos; processos/lançamentos vinculados são
    # mantidos intactos (obrigação legal/fiscal de guarda de registro),
    # só a identificação pessoal é removida.
    anonimizado_em = db.Column(db.DateTime, nullable=True)
    anonimizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    anonimizado_por = db.relationship("Usuario", foreign_keys=[anonimizado_por_id])

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade", back_populates="clientes")

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])

    processos = db.relationship("Processo", back_populates="cliente", lazy="dynamic")

    def __repr__(self):
        return f"<Cliente {self.nome}>"
