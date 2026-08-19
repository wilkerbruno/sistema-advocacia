"""
Controle de acesso por módulo — camada de negócio sobre Modulo/EmpresaModulo
(ver app/models/modulo.py). Complementa o bloqueio por licença que já existe
em app/__init__.py (bloquear_empresa_sem_licenca_ativa): aquele bloqueia a
empresa INTEIRA quando a licença não está ativa; este bloqueia só as TELAS
de um módulo específico que a empresa não contratou, mesmo com a licença em
dia.

Nada aqui roda para o admin desenvolvedor / empresa dona da plataforma —
eles sempre enxergam tudo, de todas as empresas (mesmo espírito do resto do
controle de acesso, ver app/utils/acesso.py).
"""
from datetime import datetime

from app.extensions import db
from app.models import Modulo, EmpresaModulo


def modulo_da_tela_atual(nome_blueprint):
    """Devolve o Modulo cujo `chave` bate com o blueprint da tela sendo
    acessada, ou None se essa tela não pertence a nenhum módulo do
    catálogo (telas de infraestrutura: login, dashboard, admin, api,
    plataforma, licenciamento, integrações — nunca são bloqueadas por
    módulo, só o cadastro de módulos em si controla os módulos "de
    produto"). Só considera módulos `ativo=True` no catálogo — um módulo
    retirado do catálogo não bloqueia nada (nem libera: ver
    `modulo_liberado_para`, que trata esse caso separadamente)."""
    if not nome_blueprint:
        return None
    return Modulo.query.filter_by(chave=nome_blueprint, ativo=True).first()


def modulo_liberado_para(empresa, modulo):
    """True se `empresa` pode acessar as telas de `modulo` agora."""
    if modulo is None:
        return True  # tela não pertence a nenhum módulo do catálogo — nunca bloqueia
    if modulo.obrigatorio:
        return True  # módulo-base, toda empresa tem
    if empresa is None:
        return False
    assoc = EmpresaModulo.query.filter_by(empresa_id=empresa.id, modulo_id=modulo.id).first()
    return assoc is not None and assoc.esta_liberado()


def chaves_liberadas(empresa):
    """Conjunto de `chave` de todos os módulos que `empresa` pode acessar
    agora (obrigatórios + incluído inicial/ativo) — usado nos menus/telas
    pra decidir o que mostrar, sem fazer uma query por item de menu."""
    obrigatorios = {m.chave for m in Modulo.query.filter_by(obrigatorio=True, ativo=True).all()}
    if empresa is None:
        return obrigatorios
    liberados = (
        db.session.query(Modulo.chave)
        .join(EmpresaModulo, EmpresaModulo.modulo_id == Modulo.id)
        .filter(
            EmpresaModulo.empresa_id == empresa.id,
            EmpresaModulo.status.in_(("incluido_inicial", "ativo")),
            Modulo.ativo.is_(True),
        )
        .all()
    )
    return obrigatorios | {chave for (chave,) in liberados}


def catalogo_ativo():
    """Lista de módulos do catálogo disponíveis pra seleção/solicitação
    (ativo=True), na ordem de exibição configurada."""
    return Modulo.query.filter_by(ativo=True).order_by(Modulo.ordem_exibicao, Modulo.nome).all()


def incluir_modulo_inicial(empresa, modulo, valor_adicional, definido_por):
    """Usado em /plataforma/empresas/nova: marca um módulo como parte do
    pacote negociado desde a criação da empresa, ANTES do primeiro
    pagamento. Não faz commit — quem chama decide quando salvar."""
    assoc = EmpresaModulo(
        empresa_id=empresa.id, modulo_id=modulo.id, status="incluido_inicial",
        valor_adicional=valor_adicional, ativado_em=datetime.utcnow(), definido_por_id=definido_por.id,
    )
    db.session.add(assoc)
    return assoc


def solicitar_modulo(empresa, modulo, solicitado_por):
    """Cliente (admin da própria empresa) pede um módulo novo depois de já
    estar pagando — fica pendente até o admin desenvolvedor aprovar e
    definir o valor adicional (ver /plataforma/empresas/<id>/modulos).
    Reaproveita a linha se já existir uma cancelada pra esse módulo (evita
    violar a constraint única empresa+módulo)."""
    assoc = EmpresaModulo.query.filter_by(empresa_id=empresa.id, modulo_id=modulo.id).first()
    agora = datetime.utcnow()
    if assoc is None:
        assoc = EmpresaModulo(empresa_id=empresa.id, modulo_id=modulo.id)
        db.session.add(assoc)
    assoc.status = "solicitado"
    assoc.solicitado_em = agora
    assoc.solicitado_por_id = solicitado_por.id
    assoc.valor_adicional = None
    assoc.ativado_em = None
    assoc.cancelado_em = None
    return assoc


def aprovar_modulo(assoc, valor_adicional, definido_por):
    assoc.status = "ativo"
    assoc.valor_adicional = valor_adicional
    assoc.ativado_em = datetime.utcnow()
    assoc.definido_por_id = definido_por.id
    assoc.cancelado_em = None


def cancelar_modulo(assoc, definido_por, observacao=None):
    assoc.status = "cancelado"
    assoc.cancelado_em = datetime.utcnow()
    assoc.definido_por_id = definido_por.id
    if observacao:
        assoc.observacao = observacao


# ---------------------- Catálogo inicial (seed) ----------------------
# (chave, nome, descricao, obrigatorio, ordem_exibicao). `chave` precisa
# bater exatamente com o nome de um blueprint (ver app/__init__.py).
# "clientes" e "processos" marcados como obrigatorios=True porque são a
# base mínima do produto — sem eles não existe cadastro nenhum pra
# sustentar qualquer outro módulo. Isso é uma decisão de produto tomada
# aqui por padrão, não uma regra técnica — totalmente ajustável depois
# pelo admin desenvolvedor em /plataforma/modulos (inclusive pra outros
# módulos que também devem ser obrigatórios, se fizer mais sentido pro
# seu modelo de negócio).
MODULOS_CATALOGO_INICIAL = [
    ("clientes", "Clientes", "Cadastro de clientes — base do sistema.", True, 1),
    ("processos", "Processos", "Cadastro e acompanhamento de processos — base do sistema.", True, 2),
    ("financeiro", "Financeiro", "Lançamentos financeiros, honorários e contas do escritório.", False, 3),
    ("tarefas", "Tarefas", "Quadro de tarefas da equipe.", False, 4),
    ("agenda", "Agenda", "Compromissos e lembretes automáticos por WhatsApp.", False, 5),
    ("timesheet", "Timesheet", "Apontamento de horas trabalhadas por processo/cliente.", False, 6),
    ("governanca", "Governança de carteira",
     "Painel de triagem, prazos, produtividade e regras de captura automática.", False, 7),
    ("agente_ia", "Agente de IA",
     "Assistente de IA para resumo de processos e rascunho de peças.", False, 8),
]


def semear_catalogo_inicial():
    """Insere só os módulos do catálogo inicial que AINDA NÃO existem
    (procura por `chave`) — nunca atualiza nem sobrescreve uma linha já
    existente, mesmo que o admin desenvolvedor já tenha mudado preço,
    obrigatorio ou ativo por lá depois de rodar isso uma vez. Seguro
    rodar quantas vezes quiser (idempotente). Devolve quantos módulos
    novos foram criados (não faz commit — quem chama decide quando
    salvar)."""
    criados = 0
    for chave, nome, descricao, obrigatorio, ordem in MODULOS_CATALOGO_INICIAL:
        if Modulo.query.filter_by(chave=chave).first() is not None:
            continue
        db.session.add(Modulo(
            chave=chave, nome=nome, descricao=descricao,
            obrigatorio=obrigatorio, ativo=True, ordem_exibicao=ordem,
        ))
        criados += 1
    return criados
