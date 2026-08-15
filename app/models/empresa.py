from datetime import datetime
from app.extensions import db


class Empresa(db.Model):
    """
    Empresa cliente da plataforma (tenant). Cada empresa pode ter várias
    unidades. Todo dado do sistema (processos, clientes, financeiro etc)
    pertence, transitivamente, a uma empresa através da unidade.

    `dono_da_plataforma`: marca a empresa que É a própria plataforma (o
    escritório dono do sistema). Só pode haver uma. Fica isenta de
    licenciamento, e os usuários dela com papel "admin" são os
    "admins desenvolvedores" — enxergam e administram TODAS as empresas.
    Nenhuma outra empresa consegue ver, listar ou atribuir qualquer coisa
    a esses usuários; eles simplesmente não pertencem à unidade/empresa
    de ninguém mais, então nunca aparecem em nenhuma tela de outra empresa.
    """
    __tablename__ = "empresas"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cnpj = db.Column(db.String(20))
    email_contato = db.Column(db.String(150))
    telefone = db.Column(db.String(30))
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    dono_da_plataforma = db.Column(db.Boolean, default=False, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    unidades = db.relationship("Unidade", back_populates="empresa", lazy="dynamic")
    licenca = db.relationship("Licenca", back_populates="empresa", uselist=False)

    def __repr__(self):
        return f"<Empresa {self.nome}>"
