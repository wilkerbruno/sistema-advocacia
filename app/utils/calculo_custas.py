"""
Item 9 da lista de pipeline de IA jurídica trazida pelo usuário
(PENDENCIAS.md, seção -108): "Cálculo — Base de cálculo, valor da causa,
custas, preparo e guias quando aplicável, com memória de cálculo aberta
para conferência."

`calcular_custa` é o motor puro (sem I/O de rede, só consulta
TabelaCustas.query) que resolve uma regra cadastrada contra um valor base
e devolve o resultado JUNTO com a memória de cálculo linha a linha — nunca
"só o número", sempre o caminho até ele, pra quem for recolher a guia de
verdade poder conferir.

`TABELA_PADRAO_TJSP` é um catálogo pronto pra popular a tabela na primeira
vez (botão "Carregar tabela padrão TJSP" em Governança de carteira >
Tabela de custas — ver app/routes/governanca.py), pesquisado em fontes
públicas em 18/09/2026: Lei estadual nº 11.608/2003 (custas judiciais do
Estado de São Paulo), na redação dada pela Lei nº 17.785/2023 (novos
valores vigentes desde 03/01/2024), Provimento CSM nº 2.684/2023 (porte de
remessa e retorno), e UFESP vigente desde 01/01/2026 = R$ 38,42. Fonte:
tabela consolidada da AASP (https://www.aasp.org.br/produtos-servicos/custas/sao-paulo/),
cruzada com a página oficial de notícias do TJSP.

⚠️ Assim como toda tabela de regra deste sistema (RegraProximaAcao,
MapaEstadoTPU, Feriado): o catálogo abaixo é só um PONTO DE PARTIDA pronto
pra conferência, nunca aplicado às cegas — a UFESP é reajustada
anualmente e a lei pode mudar; o admin do escritório deve confirmar os
valores vigentes antes de usar em cobrança real, e pode editar/desativar
qualquer linha a qualquer momento pela tela. O sistema NUNCA recalcula ou
atualiza estes valores sozinho.
"""
from decimal import Decimal


def _fmt_reais(valor):
    if valor is None:
        return "—"
    return f"R$ {Decimal(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class CustaNaoCadastradaError(Exception):
    """Nenhuma regra ativa casa o tribunal+tipo_custa pedido — nunca inventa um valor."""


def calcular_custa(tribunal, tipo_custa, valor_base=None, quantidade=1):
    """
    Resolve a(s) linha(s) de TabelaCustas que casam `tribunal`+`tipo_custa`
    (ativas) e calcula o valor final para `valor_base` (valor da
    causa/partilha — obrigatório para regras percentuais ou em faixas,
    ignorado para regra de valor fixo simples) e `quantidade` (ex.: número
    de volumes no porte de remessa e retorno).

    Devolve um dict {tabela_custas, valor, memoria} — `memoria` é uma
    LISTA de strings, cada uma uma linha da memória de cálculo, pronta pra
    salvar em CalculoCustas.memoria_calculo (ver app/routes/processos.py)
    ou mostrar direto na tela antes de confirmar.

    Levanta CustaNaoCadastradaError quando não há regra ativa pra esse
    tribunal+tipo_custa (nunca inventa um percentual ou valor) — quem
    chama decide como avisar o usuário (ver rota, que sugere cadastrar em
    Governança > Tabela de custas).
    """
    from app.models import TabelaCustas

    linhas = (TabelaCustas.query.filter_by(tribunal=tribunal, tipo_custa=tipo_custa, ativo=True)
              .order_by(TabelaCustas.faixa_ate.asc().nullslast()).all())
    if not linhas:
        raise CustaNaoCadastradaError(
            f"Nenhuma regra de custas ativa cadastrada para \"{tribunal}\" / \"{tipo_custa}\". "
            "Cadastre em Governança de carteira > Tabela de custas antes de calcular."
        )

    # Faixas: mais de uma linha pro mesmo tribunal+tipo_custa, OU uma única
    # linha que já veio com faixa_ate preenchido (faixa "sem teto" sozinha,
    # caso raro mas válido) — resolve pela primeira faixa cujo teto ainda
    # comporta o valor base, na ordem crescente (None = sem teto, sempre
    # por último, ver order_by acima).
    eh_faixas = len(linhas) > 1 or linhas[0].faixa_ate is not None
    if eh_faixas:
        if valor_base is None:
            raise ValueError("Informe o valor base (valor da causa/partilha) para calcular esta custa em faixas.")
        valor_base = Decimal(str(valor_base))
        regra = next((r for r in linhas if r.faixa_ate is None or valor_base <= r.faixa_ate), linhas[-1])
        valor = Decimal(str(regra.valor_fixo or 0)) * quantidade
        memoria = [
            f"Valor base (valor da causa/partilha): {_fmt_reais(valor_base)}",
            f"Faixa aplicável: {regra.descricao}",
        ]
        if quantidade != 1:
            memoria.append(f"Quantidade: {quantidade}")
        memoria.append(f"Valor calculado: {_fmt_reais(valor)}")
        memoria.append(f"Base legal/observação: {regra.observacao or '—'}")
        return {"tabela_custas": regra, "valor": valor, "memoria": memoria}

    regra = linhas[0]

    if regra.percentual is not None:
        if valor_base is None:
            raise ValueError("Informe o valor base (valor da causa) para calcular esta custa percentual.")
        valor_base = Decimal(str(valor_base))
        percentual = Decimal(str(regra.percentual))
        bruto = valor_base * percentual / Decimal(100)

        memoria = [
            f"Valor base (valor da causa): {_fmt_reais(valor_base)}",
            f"Percentual aplicado: {percentual}% ({regra.descricao})",
            f"Valor bruto: {_fmt_reais(bruto)}",
        ]
        valor = bruto
        if regra.valor_minimo is not None and bruto < Decimal(str(regra.valor_minimo)):
            valor = Decimal(str(regra.valor_minimo))
            memoria.append(f"Abaixo do valor mínimo ({_fmt_reais(regra.valor_minimo)}) — aplicado o mínimo.")
        elif regra.valor_maximo is not None and bruto > Decimal(str(regra.valor_maximo)):
            valor = Decimal(str(regra.valor_maximo))
            memoria.append(f"Acima do valor máximo ({_fmt_reais(regra.valor_maximo)}) — aplicado o máximo.")

        if quantidade != 1:
            valor = valor * quantidade
            memoria.append(f"Quantidade: {quantidade} — valor final: {_fmt_reais(valor)}")

        memoria.append(f"Base legal/observação: {regra.observacao or '—'}")
        return {"tabela_custas": regra, "valor": valor, "memoria": memoria}

    # Valor fixo simples (sem percentual, sem faixa) — ex.: agravo de
    # instrumento, porte de remessa e retorno por volume.
    valor = Decimal(str(regra.valor_fixo or 0)) * quantidade
    memoria = [f"Valor fixo: {_fmt_reais(regra.valor_fixo)} ({regra.descricao})"]
    if quantidade != 1:
        memoria.append(f"Quantidade: {quantidade} — valor final: {_fmt_reais(valor)}")
    memoria.append(f"Base legal/observação: {regra.observacao or '—'}")
    return {"tabela_custas": regra, "valor": valor, "memoria": memoria}


# Fonte e data de referência: ver docstring do módulo acima. UFESP
# considerada: R$ 38,42 (vigente desde 01/01/2026). Cada linha já vem com
# o valor em R$ convertido (nunca guardamos "15 UFESP" cru no banco, pra
# não precisar de um sistema de atualização automática de índice que este
# sistema não tem — ver aviso no topo do módulo).
TABELA_PADRAO_TJSP = [
    dict(tribunal="TJSP", tipo_custa="distribuicao_civel",
         descricao="Distribuição — ações cíveis em geral (1ª instância)",
         percentual=Decimal("1.5"), valor_minimo=Decimal("192.10"), valor_maximo=Decimal("115260.00"),
         observacao="Lei 11.608/2003, art. 4º, I e §1º (redação da Lei 17.785/2023), vigente desde 03/01/2024 "
                     "— 5 a 3.000 UFESP (UFESP = R$ 38,42 desde 01/01/2026). Confira o valor vigente antes de usar."),
    dict(tribunal="TJSP", tipo_custa="execucao_titulo_extrajudicial",
         descricao="Distribuição — execução de título extrajudicial",
         percentual=Decimal("2.0"), valor_minimo=Decimal("192.10"), valor_maximo=Decimal("115260.00"),
         observacao="Lei 11.608/2003, art. 4º, III. Mesmo teto/piso da distribuição cível comum."),
    dict(tribunal="TJSP", tipo_custa="preparo_recurso",
         descricao="Preparo de recurso (apelação, embargos infringentes, recurso adesivo)",
         percentual=Decimal("4.0"), valor_minimo=Decimal("192.10"), valor_maximo=Decimal("115260.00"),
         observacao="Art. 1.007 do CPC, aplicado com a tabela de custas do TJSP (Lei 11.608/2003)."),
    dict(tribunal="TJSP", tipo_custa="agravo_instrumento",
         descricao="Preparo de agravo de instrumento (valor fixo)",
         valor_fixo=Decimal("576.30"),
         observacao="15 UFESP, vigente desde 01/03/2024 (UFESP = R$ 38,42 desde 01/01/2026). Some com o porte de "
                     "remessa e retorno, calculado à parte."),
    dict(tribunal="TJSP", tipo_custa="porte_remessa_retorno",
         descricao="Porte de remessa e retorno (por volume) — grátis em processo 100% eletrônico",
         valor_fixo=Decimal("64.24"),
         observacao="1,672 UFESP por volume — Provimento CSM 2.684/2023. Não se aplica quando a tramitação é "
                     "inteiramente eletrônica (a regra normal na maioria dos processos hoje)."),
    dict(tribunal="TJSP", tipo_custa="inventario_partilha",
         descricao="Inventário/separação/divórcio com partilha de bens — até R$ 50.000,00",
         valor_fixo=Decimal("384.20"), faixa_ate=Decimal("50000.00"),
         observacao="10 UFESP. Lei 11.608/2003 com alterações — confira o valor vigente."),
    dict(tribunal="TJSP", tipo_custa="inventario_partilha",
         descricao="Inventário/separação/divórcio com partilha de bens — de R$ 50.000,01 a R$ 500.000,00",
         valor_fixo=Decimal("3842.00"), faixa_ate=Decimal("500000.00"),
         observacao="100 UFESP."),
    dict(tribunal="TJSP", tipo_custa="inventario_partilha",
         descricao="Inventário/separação/divórcio com partilha de bens — de R$ 500.000,01 a R$ 2.000.000,00",
         valor_fixo=Decimal("11526.00"), faixa_ate=Decimal("2000000.00"),
         observacao="300 UFESP."),
    dict(tribunal="TJSP", tipo_custa="inventario_partilha",
         descricao="Inventário/separação/divórcio com partilha de bens — de R$ 2.000.000,01 a R$ 5.000.000,00",
         valor_fixo=Decimal("38420.00"), faixa_ate=Decimal("5000000.00"),
         observacao="1.000 UFESP."),
    dict(tribunal="TJSP", tipo_custa="inventario_partilha",
         descricao="Inventário/separação/divórcio com partilha de bens — acima de R$ 5.000.000,00",
         valor_fixo=Decimal("115260.00"), faixa_ate=None,
         observacao="3.000 UFESP (teto)."),
]
