"""
Verificação de conflito de interesses (PENDENCIAS.md, seção -42) — item de
compliance da tabela de prioridades do relatório de 20/08/2026.

O conflito clássico que este módulo detecta: o escritório representa uma
pessoa/empresa como CLIENTE num processo, e ao mesmo tempo aparece como
PARTE CONTRÁRIA (a parte do outro lado) em outro processo — mesmo que os
dois processos sejam de clientes/unidades diferentes. Isso é uma
verificação de ÉTICA PROFISSIONAL (OAB), então precisa enxergar o
escritório inteiro (todas as unidades da mesma empresa), nunca só a
unidade de quem está cadastrando — diferente de `aplicar_escopo_unidade`,
que restringe usuário comum à própria unidade só por controle de acesso
operacional. Nunca cruza fronteira de EMPRESA (cada escritório cliente da
plataforma só vê conflito dentro do próprio escritório).

Este módulo NUNCA bloqueia nada sozinho — só detecta e mostra. A decisão
de aceitar ou recusar um caso é sempre humana (e frequentemente depende
de contexto que o sistema não tem, como já ter sido resolvido antes, ou
tratar-se de nome coincidente de pessoas diferentes).
"""
import unicodedata
from app.models import Cliente, Processo


def _normalizar_nome(nome):
    """
    minúsculas + sem acento + espaços colapsados/laterais removidos —
    pega o caso mais comum de "mesmo nome digitado diferente" (maiúscula,
    acento, espaço duplo) sem tentar ser esperto demais (nada de
    similaridade fuzzy/Levenshtein): um match "quase igual" que não é
    exatamente o mesmo nome normalizado gera mais ruído do que ajuda —
    prefere não sinalizar a sinalizar demais e a equipe parar de prestar
    atenção nos avisos.
    """
    if not nome:
        return ""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().split())


def _ids_unidades_ou_vazio(empresa_id):
    from app.utils.acesso import ids_unidades_da_empresa
    if empresa_id is None:
        return []
    return ids_unidades_da_empresa(empresa_id)


def _db_ne_or_null(coluna, valor):
    """coluna != valor, mas incluindo linha com coluna NULL (processo sem
    cliente vinculado não é "do mesmo cliente", então deve continuar
    valendo como candidato a conflito)."""
    from app.extensions import db
    return db.or_(coluna != valor, coluna.is_(None))


def conflitos_para_cliente(empresa_id, nome_cliente, cliente_id=None):
    """
    Processos (de QUALQUER cliente da empresa) cuja parte_contraria bate
    com o nome deste cliente — ou seja, este cliente aparece como "o outro
    lado" em algum caso do próprio escritório. Exclui processos que já são
    do próprio cliente (não é conflito representar a mesma pessoa duas
    vezes no mesmo lado).
    """
    alvo = _normalizar_nome(nome_cliente)
    if not alvo:
        return []
    ids_unidades = _ids_unidades_ou_vazio(empresa_id)
    if not ids_unidades:
        return []

    candidatos = Processo.query.filter(
        Processo.unidade_id.in_(ids_unidades),
        Processo.parte_contraria.isnot(None),
        Processo.parte_contraria != "",
    )
    if cliente_id is not None:
        candidatos = candidatos.filter(_db_ne_or_null(Processo.cliente_id, cliente_id))

    return [p for p in candidatos.all() if _normalizar_nome(p.parte_contraria) == alvo]


def conflitos_para_parte_contraria(empresa_id, nome_parte_contraria, processo_id=None, cliente_id_do_processo=None):
    """
    Clientes (de QUALQUER unidade da empresa) cujo nome bate com esta
    parte contrária — ou seja, a pessoa/empresa do outro lado deste
    processo já é cliente do escritório em outro caso. Exclui o próprio
    cliente do processo (não é conflito o cliente ser parte contrária dele
    mesmo — isso seria erro de cadastro, não conflito de interesse).
    """
    alvo = _normalizar_nome(nome_parte_contraria)
    if not alvo:
        return []
    ids_unidades = _ids_unidades_ou_vazio(empresa_id)
    if not ids_unidades:
        return []

    candidatos = Cliente.query.filter(Cliente.unidade_id.in_(ids_unidades))
    if cliente_id_do_processo is not None:
        candidatos = candidatos.filter(Cliente.id != cliente_id_do_processo)

    return [c for c in candidatos.all() if _normalizar_nome(c.nome) == alvo]


def varrer_conflitos_da_empresa(empresa_id):
    """
    Varredura completa: todo par (cliente, processo de OUTRO cliente cuja
    parte_contraria bate com o nome desse cliente) na empresa inteira.
    Usada pela tela dedicada "Verificação de conflitos" — mais pesada que
    as duas funções acima (que checam só um registro por vez), então só
    deve rodar sob demanda (botão), não em toda carga de página.
    """
    ids_unidades = _ids_unidades_ou_vazio(empresa_id)
    if not ids_unidades:
        return []

    clientes = Cliente.query.filter(Cliente.unidade_id.in_(ids_unidades)).order_by(Cliente.nome).all()
    processos = Processo.query.filter(
        Processo.unidade_id.in_(ids_unidades),
        Processo.parte_contraria.isnot(None),
        Processo.parte_contraria != "",
    ).all()

    por_parte_contraria = {}
    for p in processos:
        chave = _normalizar_nome(p.parte_contraria)
        if chave:
            por_parte_contraria.setdefault(chave, []).append(p)

    conflitos = []
    for c in clientes:
        chave = _normalizar_nome(c.nome)
        if not chave:
            continue
        processos_batendo = [p for p in por_parte_contraria.get(chave, []) if p.cliente_id != c.id]
        if processos_batendo:
            conflitos.append({"cliente": c, "processos": processos_batendo})

    return conflitos
