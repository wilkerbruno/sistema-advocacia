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

    # ---- Integrações "traga sua própria chave" (BYOK) — ver
    # app/utils/agente_ia_router.py, app/utils/captura_conectores.py e
    # app/routes/integracoes.py. Colunas NULLABLE de propósito (mesmo as
    # que têm um valor padrão em código): sincronizar_schema.py só sabe
    # adicionar coluna sem DEFAULT no banco, então uma coluna NOT NULL aqui
    # quebraria a sincronização em bancos com empresas já cadastradas.
    # Trate None como o valor padrão (ver as properties *_efetivo abaixo).
    PROVEDOR_IA_LOCAL = "local"
    PROVEDOR_IA_CLAUDE_BYOK = "claude_byok"
    PROVEDOR_DATAJUD_PADRAO = "padrao"
    PROVEDOR_DATAJUD_CHAVE_PROPRIA = "chave_propria"

    agente_ia_provedor = db.Column(db.String(20))  # None/"local" ou "claude_byok"
    agente_ia_claude_chave_cifrada = db.Column(db.LargeBinary)  # Fernet, ver app/utils/cofre.py
    agente_ia_claude_modelo = db.Column(db.String(60))  # None usa o padrão de app/utils/claude_api.py

    datajud_provedor = db.Column(db.String(20))  # None/"padrao" ou "chave_propria"
    datajud_chave_propria_cifrada = db.Column(db.LargeBinary)  # Fernet, ver app/utils/cofre.py

    unidades = db.relationship("Unidade", back_populates="empresa", lazy="dynamic")
    licenca = db.relationship("Licenca", back_populates="empresa", uselist=False)

    @property
    def agente_ia_provedor_efetivo(self):
        return self.agente_ia_provedor or self.PROVEDOR_IA_LOCAL

    @property
    def datajud_provedor_efetivo(self):
        return self.datajud_provedor or self.PROVEDOR_DATAJUD_PADRAO

    def __repr__(self):
        return f"<Empresa {self.nome}>"
