import hashlib
import secrets
from datetime import datetime

from app.extensions import db


class TokenIntegracao(db.Model):
    """
    Token de acesso à API de leitura de integração (`/api/v1/*`, ver
    app/routes/api_integracao.py) — UM TOKEN POR EMPRESA.

    Substitui o token único global antigo (`DATALAKE_API_TOKEN` no .env),
    que dava acesso aos dados de TODAS as empresas clientes da plataforma
    para quem tivesse esse único valor, sem nenhum filtro por empresa —
    uma falha de isolamento entre clientes (ver PENDENCIAS.md, seção -28,
    e AUDITORIA_GRANDE_PORTE.md, item 1.1). Cada token agora pertence a
    UMA empresa (`empresa_id`), e toda consulta feita com ele em
    `api_integracao.py` é filtrada só pelos dados dessa empresa.

    Guarda só o HASH (SHA-256) do token, nunca o valor puro — mesmo padrão
    de token de API do GitHub/Stripe/etc: o valor completo só existe no
    momento em que é gerado (devolvido uma única vez pela rota que cria),
    depois disso é irrecuperável mesmo pelo próprio admin — só dá pra
    revogar e gerar outro. `prefixo` guarda só os primeiros caracteres,
    para o admin identificar qual token é qual numa lista sem conseguir
    reconstruir o valor inteiro a partir disso.
    """
    __tablename__ = "tokens_integracao"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    prefixo = db.Column(db.String(12), nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    ultimo_uso_em = db.Column(db.DateTime)
    revogado_em = db.Column(db.DateTime)

    empresa = db.relationship("Empresa")
    criado_por = db.relationship("Usuario")

    @staticmethod
    def _hash_de(valor):
        return hashlib.sha256(valor.encode("utf-8")).hexdigest()

    @classmethod
    def emitir_para(cls, empresa, usuario=None):
        """
        Cria e persiste (via db.session.add — quem chama precisa dar
        commit) um token novo para `empresa`. Devolve (registro, valor_puro)
        — só essa chamada tem acesso ao valor puro; guarde-o só para
        mostrar uma vez na tela, nunca em log nem em outro lugar.
        """
        valor = secrets.token_urlsafe(32)
        token = cls(
            empresa_id=empresa.id,
            token_hash=cls._hash_de(valor),
            prefixo=valor[:10],
            criado_por_id=usuario.id if usuario is not None else None,
        )
        db.session.add(token)
        return token, valor

    @classmethod
    def validar(cls, valor_recebido):
        """Devolve o TokenIntegracao ATIVO correspondente ao valor recebido
        (comparando por hash), ou None se não houver nenhum válido."""
        if not valor_recebido:
            return None
        return cls.query.filter_by(token_hash=cls._hash_de(valor_recebido), ativo=True).first()

    def revogar(self):
        self.ativo = False
        self.revogado_em = datetime.utcnow()
