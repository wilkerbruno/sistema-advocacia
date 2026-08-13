from datetime import datetime
from app.extensions import db


class SenhaProcesso(db.Model):
    """
    Cofre de senha de processo em segredo de justiça (seção 5.1).

    IMPORTANTE — limite do briefing: essa senha é a senha *do processo*
    (fornecida às partes pelo tribunal, ex: e-SAJ do TJSP), nunca uma
    credencial pessoal de terceiro. Acesso com credencial alheia é crime
    (Lei 12.737/2012) e está fora do escopo deste sistema.

    Nesta tabela o valor fica sempre criptografado (Fernet/AES a partir de
    uma chave mantida fora do banco — variável de ambiente ou, em
    produção, um Vault/KMS dedicado). Nunca gravar em texto puro.
    Acesso restrito ao usuário que cadastrou, com registro em log
    (ver LogAtividade) a cada leitura.
    """
    __tablename__ = "senhas_processo"

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False, unique=True)
    processo = db.relationship("Processo", back_populates="senha_processo")

    tribunal = db.Column(db.String(60))  # ex: e-SAJ TJSP
    valor_criptografado = db.Column(db.LargeBinary, nullable=False)

    cadastrado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    cadastrado_por = db.relationship("Usuario", foreign_keys=[cadastrado_por_id])
    cadastrado_em = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_acesso_em = db.Column(db.DateTime)
    ultimo_acesso_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    ultimo_acesso_por = db.relationship("Usuario", foreign_keys=[ultimo_acesso_por_id])
