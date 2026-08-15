"""
Núcleo do controle de acesso do sistema (multi-tenant).

Hierarquia: Empresa (tenant) -> Unidade -> Usuario.

Regra central do produto:
- admin desenvolvedor (papel="admin" na empresa dona da plataforma)
      -> enxerga e filtra por QUALQUER empresa/unidade. Nenhuma empresa
         cliente consegue ver, listar ou atribuir nada a esse usuário —
         ele não pertence à unidade de nenhuma delas.
- admin de empresa (papel="admin" em qualquer outra empresa)
      -> enxerga e filtra por QUALQUER unidade DA PRÓPRIA EMPRESA, nunca
         de outra.
- demais papéis (gestor/advogado/funcionario)
      -> toda consulta é automaticamente restrita à unidade_id do
         próprio usuário logado (que já pertence a uma única empresa).

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


def apenas_admin_desenvolvedor(f):
    """Restringe a view aos admins da empresa dona da plataforma."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin_desenvolvedor:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def _ids_unidades_da_empresa(empresa_id):
    from app.models import Unidade
    return [u.id for u in Unidade.query.filter_by(empresa_id=empresa_id).all()]


def aplicar_escopo_unidade(query, modelo, unidade_field="unidade_id"):
    """
    Filtra uma query SQLAlchemy pela unidade do usuário logado.
    - admin desenvolvedor: sem filtro nenhum (vê tudo, de todas as empresas).
    - admin de empresa: filtra por todas as unidades da PRÓPRIA empresa.
    - demais papéis: filtra só pela própria unidade.
    """
    if not current_user.is_authenticated:
        return query.filter(False)  # nunca deveria chegar aqui sem login

    campo = getattr(modelo, unidade_field)

    if current_user.is_admin_desenvolvedor:
        return query

    if current_user.is_admin:
        ids = _ids_unidades_da_empresa(current_user.empresa_id_atual)
        return query.filter(campo.in_(ids))

    return query.filter(campo == current_user.unidade_id)


def unidade_permitida(unidade_id):
    """Verifica se o usuário logado pode acessar dados de uma unidade específica."""
    if current_user.is_admin_desenvolvedor:
        return True
    if current_user.is_admin:
        from app.models import Unidade
        alvo = Unidade.query.get(unidade_id)
        return alvo is not None and alvo.empresa_id == current_user.empresa_id_atual
    return current_user.unidade_id == unidade_id


def checar_acesso_unidade_ou_403(unidade_id):
    if not unidade_permitida(unidade_id):
        abort(403)


def unidades_do_escopo(apenas_ativas=True):
    """
    Lista de unidades que o usuário logado pode enxergar/escolher em
    dropdowns de formulário:
    - admin desenvolvedor: todas as unidades, de todas as empresas.
    - admin de empresa: só as unidades da própria empresa.
    - demais papéis: só a própria unidade.
    """
    from app.models import Unidade
    query = Unidade.query
    if apenas_ativas:
        query = query.filter_by(ativa=True)

    if current_user.is_admin_desenvolvedor:
        return query.order_by(Unidade.nome).all()
    if current_user.is_admin:
        return query.filter_by(empresa_id=current_user.empresa_id_atual).order_by(Unidade.nome).all()
    return query.filter_by(id=current_user.unidade_id).all()


def usuarios_do_escopo(apenas_ativos=True):
    """Mesma regra de unidades_do_escopo(), mas para usuários (evita
    vazar equipe de uma empresa para outra em campos 'responsável')."""
    from app.models import Usuario, Unidade
    query = Usuario.query
    if apenas_ativos:
        query = query.filter_by(ativo=True)

    if current_user.is_admin_desenvolvedor:
        return query.order_by(Usuario.nome).all()
    if current_user.is_admin:
        return query.join(Unidade).filter(Unidade.empresa_id == current_user.empresa_id_atual).order_by(Usuario.nome).all()
    return query.filter_by(unidade_id=current_user.unidade_id).order_by(Usuario.nome).all()


def unidade_id_para_novo_registro():
    """
    Determina a unidade_id a usar ao CRIAR um registro.
    - Usuário comum: sempre a própria unidade (não pode escolher outra).
    - Admin (empresa ou desenvolvedor): escolhe via formulário, mas o valor
      é sempre validado por checar_acesso_unidade_ou_403 antes de usar.
    """
    if current_user.is_admin:
        valor = request.form.get("unidade_id") or request.args.get("unidade_id")
        return int(valor) if valor else None
    return current_user.unidade_id
