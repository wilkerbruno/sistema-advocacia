"""
Helpers financeiros compartilhados entre rotas (ver PENDENCIAS.md, seção
-39 e -41). Hoje só o filtro de conta_terceiros — extraído de
app/routes/financeiro.py pra também ser usado em app/routes/admin.py
(relatório consolidado), evitando duplicar (e um dia dessincronizar) a
mesma lógica de "o que conta como caixa próprio do escritório" em dois
lugares.
"""
from app.extensions import db
from app.models import Lancamento


def filtro_conta_terceiros(eh_terceiros):
    """
    Lancamento.conta_terceiros é nullable=True de propósito (ver
    comentário em app/models/financeiro.py) — lançamentos antigos, criados
    antes desta coluna existir, ficam com NULL depois do ALTER TABLE em
    produção. Em SQL, `NULL = 0` não é verdadeiro, então comparar direto
    com `== False` faria esses lançamentos antigos sumirem tanto da visão
    operacional quanto da de terceiros. Por isso NULL é sempre tratado
    como "não é de terceiros" (comportamento antigo, antes de existir a
    distinção) — em QUALQUER relatório/tela que soma valor de lançamento.
    """
    if eh_terceiros:
        return Lancamento.conta_terceiros.is_(True)
    return db.or_(Lancamento.conta_terceiros.is_(False), Lancamento.conta_terceiros.is_(None))
