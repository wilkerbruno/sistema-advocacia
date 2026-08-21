"""
Alçada de aprovação em múltiplos níveis para DESPESAS (PENDENCIAS.md,
seção -50) — item "Governança" da tabela de prioridades do relatório de
20/08/2026.

Escopo deliberado: só se aplica a `Lancamento.natureza == "despesa"`
(dinheiro SAINDO do escritório) — receita (dinheiro entrando, o cliente
pagando) nunca precisa de aprovação interna pra ser marcada como
recebida, então fica fora, de propósito.

Desligada por padrão pra toda empresa (`Empresa.alcada_nivel1_valor is
None`): sem configurar nada em /admin/alcada-aprovacao, toda despesa
continua podendo ser marcada como paga direto, exatamente o
comportamento de sempre — mesmo padrão "opt-in, nunca trava sozinho" do
resto do projeto.

Regra, com as duas alçadas configuradas pela empresa:
  - despesa <= nível 1 (ou nível 1 não configurado): sem aprovação.
  - nível 1 < despesa <= nível 2 (ou nível 2 não configurado): 1 aprovação.
  - despesa > nível 2: 2 aprovações, de dois usuários DISTINTOS entre si.

Quem pode aprovar: só admin ou gestor (autoridade de alçada é papel de
gestão, não o mesmo que "acesso a dado financeiro" — ver
Usuario.pode_ver_financeiro/seção -45, que também libera um sócio sem
papel de gestor pra só ENXERGAR o financeiro, não pra aprovar despesa
alheia). Segregação de função básica: quem LANÇOU a despesa nunca pode
aprovar a própria alçada, e o mesmo aprovador nunca conta duas vezes pro
mesmo lançamento (mesmo que a alçada exija 2 aprovações de verdade
distintas).
"""


def nivel_aprovacao_necessario(lancamento):
    """
    Quantas aprovações DISTINTAS este lançamento precisa antes de poder
    ser marcado como pago: 0 (não precisa), 1 ou 2.
    """
    if lancamento.natureza != "despesa":
        return 0

    empresa = lancamento.unidade.empresa if lancamento.unidade else None
    if empresa is None or empresa.alcada_nivel1_valor is None:
        return 0  # alçada desligada pra esta empresa — não configurada

    if lancamento.valor <= empresa.alcada_nivel1_valor:
        return 0

    if empresa.alcada_nivel2_valor is not None and lancamento.valor > empresa.alcada_nivel2_valor:
        return 2

    return 1


def aprovacoes_concedidas(lancamento):
    return list(lancamento.aprovacoes)


def aprovacoes_faltando(lancamento):
    necessario = nivel_aprovacao_necessario(lancamento)
    if necessario == 0:
        return 0
    concedidas = len(aprovacoes_concedidas(lancamento))
    return max(0, necessario - concedidas)


def pode_ser_marcado_pago(lancamento):
    return aprovacoes_faltando(lancamento) == 0


def usuario_ja_aprovou(lancamento, usuario):
    return any(a.aprovador_id == usuario.id for a in lancamento.aprovacoes)


def usuario_pode_aprovar(lancamento, usuario):
    """
    Devolve (pode: bool, motivo: str|None) — motivo só é preenchido
    quando pode é False, já pronto pra virar mensagem de flash.
    """
    if nivel_aprovacao_necessario(lancamento) == 0:
        return False, "Este lançamento não precisa de aprovação de alçada."
    if pode_ser_marcado_pago(lancamento):
        return False, "Este lançamento já reuniu todas as aprovações necessárias."
    if not (usuario.is_admin or usuario.is_gestor):
        return False, "Só admin ou gestor pode aprovar um lançamento de alçada."
    if lancamento.criado_por_id == usuario.id:
        return False, "Quem lançou a despesa não pode aprovar a própria alçada."
    if usuario_ja_aprovou(lancamento, usuario):
        return False, "Você já aprovou este lançamento."
    return True, None
