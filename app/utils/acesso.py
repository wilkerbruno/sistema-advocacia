"""
Núcleo do controle de acesso do sistema.

Regra central do produto:
- usuário papel = admin -> enxerga e filtra por QUALQUER unidade
- demais papéis         -> toda consulta é automaticamente restrita à
                            unidade_id do próprio usuário logado

Todas as views de listagem/detalhe DEVEM passar as queries por
`aplicar_escopo_unidade()` para que essa regra nunca seja esquecida
em uma tela nova.
"""
from functools import wraps
from flask import abort, request
from flask_login import current_user


def login_papel_requerido(*papeis):
    """Restringe uma view a determinados papéis (ex: 'admin', 'gestor')."""
    def decorador(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.papel not in papeis:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorador


def apenas_admin(f):
    return login_papel_requerido("admin")(f)


def aplicar_escopo_unidade(query, modelo, unidade_field="unidade_id"):
    """
    Filtra uma query SQLAlchemy pela unidade do usuário logado,
    a menos que ele seja admin (que vê tudo).

    Uso: aplicar_escopo_unidade(Processo.query, Processo)
    """
    if current_user.is_authenticated and current_user.is_admin:
        return query
    campo = getattr(modelo, unidade_field)
    return query.filter(campo == current_user.unidade_id)


def unidade_permitida(unidade_id):
    """Verifica se o usuário logado pode acessar dados de uma unidade específica."""
    if current_user.is_admin:
        return True
    return current_user.unidade_id == unidade_id


def checar_acesso_unidade_ou_403(unidade_id):
    if not unidade_permitida(unidade_id):
        abort(403)


def unidade_id_para_novo_registro():
    """
    Determina a unidade_id a usar ao CRIAR um registro.
    - Usuário comum: sempre a própria unidade (não pode escolher outra).
    - Admin: pode escolher via formulário (campo 'unidade_id' no POST).
    """
    if current_user.is_admin:
        valor = request.form.get("unidade_id") or request.args.get("unidade_id")
        return int(valor) if valor else None
    return current_user.unidade_id
