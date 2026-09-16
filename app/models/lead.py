"""
Captação de clientes (CRM de pipeline) — pedido explícito: depois de olhar o
concorrente DeskcommCRM (github.com/melgarafael/DeskcommCRM, um "CRM de
vendas" com kanban de negócios via WhatsApp), o usuário perguntou se valia a
pena trazer algo parecido pro sistema. A resposta foi que sim, mas adaptado
pro que um escritório de advocacia realmente precisa: não é "vender", é
CAPTAR um caso novo — então o pipeline aqui já nasce com vocabulário e
etapas de escritório (não "negócio"/"venda"), sem nenhuma automação de
WhatsApp/IA por enquanto (fica registrado como ideia futura em
PENDENCIAS.md, não fez parte do escopo aprovado desta rodada).

Um Lead é um CONTATO AINDA NÃO CLIENTE (alguém que ligou, mandou mensagem
ou foi indicado, interessado num caso, mas que ainda não virou Cliente/
Processo de verdade). Quando o escritório decide seguir com o caso, o botão
"Converter em cliente" (ver app/routes/leads.py::converter) cria o Cliente
de verdade a partir dos dados já coletados aqui — evita digitar tudo de
novo e mantém um rastro de "de onde veio esse cliente".
"""
from datetime import datetime
from app.extensions import db


class Lead(db.Model):
    __tablename__ = "leads"

    # Etapas do funil — pensadas pro dia a dia de captação de um
    # escritório, não pro vocabulário genérico de "vendas" (deal/won/lost)
    # do CRM que inspirou isso. Ordem = ordem das colunas no quadro kanban.
    ETAPA_NOVO = "novo"
    ETAPA_QUALIFICANDO = "qualificando"
    ETAPA_PROPOSTA_ENVIADA = "proposta_enviada"
    ETAPA_CONVERTIDO = "convertido"
    ETAPA_PERDIDO = "perdido"
    ETAPAS = (ETAPA_NOVO, ETAPA_QUALIFICANDO, ETAPA_PROPOSTA_ENVIADA, ETAPA_CONVERTIDO, ETAPA_PERDIDO)
    ETAPAS_ATIVAS = (ETAPA_NOVO, ETAPA_QUALIFICANDO, ETAPA_PROPOSTA_ENVIADA)  # ainda "em jogo"
    NOMES_ETAPAS = {
        ETAPA_NOVO: "Novo contato",
        ETAPA_QUALIFICANDO: "Qualificando",
        ETAPA_PROPOSTA_ENVIADA: "Proposta enviada",
        ETAPA_CONVERTIDO: "Convertido em cliente",
        ETAPA_PERDIDO: "Perdido",
    }

    ORIGENS = ("indicacao", "site", "whatsapp", "telefone", "redes_sociais", "outro")
    NOMES_ORIGENS = {
        "indicacao": "Indicação",
        "site": "Site",
        "whatsapp": "WhatsApp",
        "telefone": "Telefone",
        "redes_sociais": "Redes sociais",
        "outro": "Outro",
    }

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)  # pessoa ou empresa interessada
    telefone = db.Column(db.String(30))
    whatsapp = db.Column(db.String(30))
    email = db.Column(db.String(120))
    # Área de interesse é texto livre (não um enum fechado) porque cada
    # escritório usa suas próprias categorias internas de área do direito
    # — o sistema não tenta impor uma taxonomia aqui.
    area_interesse = db.Column(db.String(120))
    origem = db.Column(db.String(20))  # um de ORIGENS, ou None (não informado)
    valor_estimado_causa = db.Column(db.Numeric(12, 2), nullable=True)
    etapa = db.Column(db.String(20), nullable=False, default=ETAPA_NOVO)
    motivo_perda = db.Column(db.Text, nullable=True)  # só preenchido quando etapa == "perdido"
    observacoes = db.Column(db.Text)

    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    responsavel = db.relationship("Usuario", foreign_keys=[responsavel_id])

    # Preenchido só depois de convertido (ver app/routes/leads.py::converter)
    # — mantém o rastro de qual Cliente nasceu deste Lead, sem duplicar
    # nenhum dado: a partir da conversão, o cadastro "de verdade" passa a
    # ser o Cliente, este registro vira só o histórico de como ele chegou.
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=True)
    cliente = db.relationship("Cliente", foreign_keys=[cliente_id])

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    unidade = db.relationship("Unidade", back_populates="leads")

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    criado_por = db.relationship("Usuario", foreign_keys=[criado_por_id])

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def nome_etapa(self):
        return self.NOMES_ETAPAS.get(self.etapa, self.etapa)

    @property
    def nome_origem(self):
        return self.NOMES_ORIGENS.get(self.origem) if self.origem else None

    def __repr__(self):
        return f"<Lead {self.nome} ({self.etapa})>"
