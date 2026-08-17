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

    # Número de WhatsApp PRÓPRIO da empresa pros lembretes da Agenda (ver
    # app/utils/whatsapp.py) — cada empresa conecta o próprio número
    # escaneando um QR code em "Minhas Integrações", em vez de todas as
    # empresas compartilharem o mesmo número da plataforma (cliente de uma
    # empresa recebendo mensagem de um número que não é dela, sem ninguém
    # pra responder dúvida). Guarda só o NOME da sessão do WAHA — nenhuma
    # credencial nova: o WAHA é um único servidor compartilhado (mesma
    # WHATSAPP_BRIDGE_URL/TOKEN do .env de sempre), só que agora com uma
    # sessão (= um número conectado) por empresa em vez de uma só global.
    whatsapp_sessao = db.Column(db.String(80))

    unidades = db.relationship("Unidade", back_populates="empresa", lazy="dynamic")
    licenca = db.relationship("Licenca", back_populates="empresa", uselist=False)

    @property
    def agente_ia_provedor_efetivo(self):
        return self.agente_ia_provedor or self.PROVEDOR_IA_LOCAL

    @property
    def datajud_provedor_efetivo(self):
        return self.datajud_provedor or self.PROVEDOR_DATAJUD_PADRAO

    @property
    def whatsapp_sessao_efetiva(self):
        """Nome da sessão do WAHA a usar pra esta empresa, ou None se ela
        ainda não conectou nenhum número. "default" é a sessão histórica
        da própria plataforma (dono_da_plataforma) — conectada manualmente
        no dashboard do WAHA antes desta funcionalidade existir; as demais
        empresas sempre têm um nome de sessão próprio (ex.: "empresa-42"),
        criado em app/routes/integracoes.py quando clicam "Conectar"."""
        if self.whatsapp_sessao:
            return self.whatsapp_sessao
        return "default" if self.dono_da_plataforma else None

    def __repr__(self):
        return f"<Empresa {self.nome}>"
