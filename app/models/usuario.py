from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class Usuario(db.Model, UserMixin):
    """
    Usuário do sistema.

    Papéis (campo `papel`):
      - admin       -> acesso total, todas as unidades, gestão de usuários/unidades
      - gestor      -> gerencia sua própria unidade (equipe, financeiro da unidade)
      - advogado    -> cria/edita processos e demais dados da sua unidade
      - funcionario -> uso operacional (secretaria/estagiário) restrito à sua unidade
    """
    __tablename__ = "usuarios"

    PAPEIS = ("admin", "gestor", "advogado", "funcionario")

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    papel = db.Column(db.String(20), nullable=False, default="funcionario")
    oab = db.Column(db.String(30))  # nº OAB, quando aplicável (advogado)
    telefone = db.Column(db.String(30))
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_login = db.Column(db.DateTime)

    # Admin não pertence a nenhuma unidade específica (enxerga todas)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=True)
    unidade = db.relationship("Unidade", back_populates="usuarios")

    def set_senha(self, senha_texto_puro):
        self.senha_hash = generate_password_hash(senha_texto_puro)

    def checar_senha(self, senha_texto_puro):
        return check_password_hash(self.senha_hash, senha_texto_puro)

    @property
    def is_admin(self):
        return self.papel == "admin"

    @property
    def is_gestor(self):
        return self.papel == "gestor"

    @property
    def empresa(self):
        """Empresa a que este usuário pertence, derivada da própria unidade."""
        return self.unidade.empresa if self.unidade else None

    @property
    def empresa_id_atual(self):
        return self.unidade.empresa_id if self.unidade else None

    @property
    def is_admin_desenvolvedor(self):
        """
        Admin da empresa DONA da plataforma — enxerga e administra todas
        as empresas clientes. Nunca aparece para nenhuma outra empresa,
        porque simplesmente não pertence à unidade de nenhuma delas.
        """
        empresa = self.empresa
        return self.papel == "admin" and empresa is not None and empresa.dono_da_plataforma

    def pode_ver_todas_unidades(self):
        return self.papel == "admin"

    def pode_gerenciar_usuarios(self):
        return self.papel in ("admin", "gestor")

    def pode_excluir(self):
        return self.papel == "admin"

    def get_id(self):
        # exigido pelo Flask-Login
        return str(self.id)

    def __repr__(self):
        return f"<Usuario {self.email} ({self.papel})>"
