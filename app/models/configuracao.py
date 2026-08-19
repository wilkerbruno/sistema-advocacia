from datetime import datetime
from decimal import Decimal
from app.extensions import db


class ConfiguracaoPlataforma(db.Model):
    """
    Configurações globais da plataforma que só o admin desenvolvedor
    gerencia — hoje só os preços padrão mostrados no cadastro público
    self-service (/cadastrar-empresa, ver app/routes/auth.py), mas dá pra
    crescer com mais campos no futuro sem precisar de uma tabela nova a
    cada configuração.

    Singleton: sempre uma linha só, com id=1 fixo — não existe endpoint
    pra criar uma segunda. Use `ConfiguracaoPlataforma.obter()` em vez de
    instanciar/consultar direto, tanto pra ler quanto pra editar.

    Esses preços são só o valor de TABELA mostrado a quem se cadastra
    sozinho (sem falar com ninguém) — o admin desenvolvedor ainda pode
    negociar um valor diferente por empresa depois, em
    Licenca.valor_negociado (ver app/models/licenca.py), sem que isso
    mude o preço de tabela pra quem se cadastrar em seguida.
    """
    __tablename__ = "configuracoes_plataforma"

    id = db.Column(db.Integer, primary_key=True)  # sempre 1 (singleton)

    preco_padrao_mensal = db.Column(db.Numeric(10, 2), nullable=False)
    preco_padrao_trimestral = db.Column(db.Numeric(10, 2), nullable=False)
    preco_padrao_anual = db.Column(db.Numeric(10, 2), nullable=False)

    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    atualizado_por = db.relationship("Usuario")

    @classmethod
    def obter(cls):
        """Devolve a linha singleton (id=1) se ela já foi salva alguma vez
        pelo admin desenvolvedor, OU um objeto em memória (NÃO salvo no
        banco) com os valores de fallback de config.py/.env — assim toda
        tela que só precisa LER o preço atual (ex.: cadastro público) nunca
        precisa se importar se alguém já configurou isso pela tela
        administrativa ou não. Só vira uma linha de verdade no banco
        quando o admin desenvolvedor salva pela tela /plataforma/planos
        (ver app/routes/plataforma.py::editar_planos)."""
        config = db.session.get(cls, 1)
        if config is not None:
            return config
        from flask import current_app
        return cls(
            id=1,
            preco_padrao_mensal=Decimal(current_app.config["PRECO_PADRAO_MENSAL"]),
            preco_padrao_trimestral=Decimal(current_app.config["PRECO_PADRAO_TRIMESTRAL"]),
            preco_padrao_anual=Decimal(current_app.config["PRECO_PADRAO_ANUAL"]),
        )

    def como_dict_precos(self):
        return {
            "mensal": self.preco_padrao_mensal,
            "trimestral": self.preco_padrao_trimestral,
            "anual": self.preco_padrao_anual,
        }

    def __repr__(self):
        return f"<ConfiguracaoPlataforma mensal={self.preco_padrao_mensal} trimestral={self.preco_padrao_trimestral} anual={self.preco_padrao_anual}>"
