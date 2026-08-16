from datetime import datetime
from app.extensions import db


class LogAtividade(db.Model):
    """Auditoria: registra ações importantes de cada usuário no sistema."""
    __tablename__ = "logs_atividade"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    usuario = db.relationship("Usuario")

    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"))
    acao = db.Column(db.String(60), nullable=False)  # criou, editou, excluiu, login...
    entidade = db.Column(db.String(60), nullable=False)  # Processo, Cliente, Usuario...
    entidade_id = db.Column(db.Integer)
    detalhes = db.Column(db.String(500))
    ip = db.Column(db.String(45))
    mac_address = db.Column(db.String(20))  # best-effort via tabela ARP — só resolve em rede local, ver app/utils/rede.py
    user_agent = db.Column(db.String(255))  # navegador/SO informado pelo próprio cliente — sempre disponível, mesmo pela internet
    dispositivo_id = db.Column(db.String(36), index=True)  # cookie de 1ª parte, gerado no 1º acesso — correlaciona ações do mesmo navegador/dispositivo ao longo do tempo, mesmo com IP variando
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
