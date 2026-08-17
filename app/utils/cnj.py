"""
Validação e leitura do número único de processo (padrão CNJ,
Resolução CNJ 65/2008): NNNNNNN-DD.AAAA.J.TR.OOOO

- NNNNNNN: número sequencial (7 dígitos)
- DD:      dígito verificador (módulo 97 base 10)
- AAAA:    ano de ajuizamento
- J:       segmento do Judiciário (1 dígito)
- TR:      tribunal (2 dígitos)
- OOOO:    unidade de origem (4 dígitos)

Usado pela seção 5.0 do briefing: "o usuário digita ou cola o número CNJ
e nada mais" — a partir daqui o sistema valida e identifica o tribunal
antes de qualquer outra coisa.
"""
import re

SEGMENTOS = {
    "1": "STF",
    "2": "CNJ",
    "3": "STJ",
    "4": "Justiça Federal",
    "5": "Justiça do Trabalho",
    "6": "Justiça Eleitoral",
    "7": "Justiça Militar da União",
    "8": "Justiça Estadual",
    "9": "Justiça Militar Estadual",
}


def somente_digitos(numero: str) -> str:
    return re.sub(r"\D", "", numero or "")


def formatar_numero_cnj(numero: str) -> str:
    """Formata 20 dígitos no padrão NNNNNNN-DD.AAAA.J.TR.OOOO. Levanta
    ValueError se não tiver exatamente 20 dígitos."""
    d = somente_digitos(numero)
    if len(d) != 20:
        raise ValueError("Número CNJ precisa ter 20 dígitos.")
    return f"{d[0:7]}-{d[7:9]}.{d[9:13]}.{d[13:14]}.{d[14:16]}.{d[16:20]}"


def calcular_digito_verificador(sequencial: str, ano: str, segmento: str, tribunal: str, origem: str) -> str:
    """
    Calcula os 2 dígitos verificadores pelo algoritmo Módulo 97 Base 10
    (ISO 7064:2003), conforme Anexo VIII da Resolução CNJ 65/2008.

    Implementado a partir da fórmula de VERIFICAÇÃO oficial (item VI do
    anexo): o número completo de 20 dígitos, na ordem original
    N(7) D(2) A(4) J(1) TR(2) O(4), módulo 97, deve dar resto 1. Resolve-se
    para D por aritmética modular (inverso de 10^11 mod 97) em vez de
    reproduzir a fórmula de geração fatorada do anexo (item III/V), que tem
    uma notação ambígua na publicação oficial — este caminho é
    matematicamente equivalente e foi validado por força bruta contra a
    própria fórmula de verificação (item VI) para milhares de combinações.
    """
    n = int(sequencial)
    r = int(f"{ano}{segmento}{tribunal}{origem}")  # 11 dígitos: A(4) J(1) TR(2) O(4)
    inv_10e11 = pow(10**11, -1, 97)
    alvo = (1 - n * (10**13) - r) % 97
    dv = (alvo * inv_10e11) % 97
    return f"{dv:02d}"


def validar_numero_cnj(numero: str, exigir_dv: bool = True) -> dict:
    """
    Confere o formato (20 dígitos, segmento conhecido) e, por padrão, o
    dígito verificador (módulo 97) — devolve as partes do número.

    `exigir_dv=False`: não bloqueia mais por dígito verificador que não
    bate com o cálculo oficial — só avisa (campo `aviso_dv` na resposta).
    Existe porque, na prática, processos reais (principalmente antigos,
    anteriores à unificação de numeração pela Resolução CNJ 65/2008) às
    vezes têm um número com dígito verificador que não fecha pela fórmula
    atual, mas que é exatamente como o próprio tribunal registrou o
    processo e como o DataJud indexou — quem decide se o processo existe
    de verdade é a consulta real ao DataJud, não o cálculo do dígito por
    aqui. Use `exigir_dv=False` nos fluxos que efetivamente BUSCAM no
    DataJud (ver app/utils/conector_datajud.py); mantenha o padrão
    (`exigir_dv=True`) onde faz sentido barrar entrada claramente digitada
    errada sem nem tentar (ex.: importação em lote via CSV).

    Retorna:
        {"valido": bool, "motivo": str|None, "aviso_dv": str|None, "partes": {...}|None}
    """
    d = somente_digitos(numero)
    if len(d) != 20:
        return {"valido": False, "motivo": "Número precisa ter 20 dígitos (formato CNJ).", "aviso_dv": None, "partes": None}

    sequencial = d[0:7]
    dv_informado = d[7:9]
    ano = d[9:13]
    segmento = d[13:14]
    tribunal = d[14:16]
    origem = d[16:20]

    # Verificação direta pela fórmula oficial (item VI do Anexo VIII):
    # o número completo, na ordem original, módulo 97 deve dar resto 1.
    dv_bate = (int(d) % 97 == 1)
    aviso_dv = None
    if not dv_bate:
        dv_calculado = calcular_digito_verificador(sequencial, ano, segmento, tribunal, origem)
        if exigir_dv:
            return {
                "valido": False,
                "motivo": f"Dígito verificador inválido (informado {dv_informado}, esperado {dv_calculado}).",
                "aviso_dv": None,
                "partes": None,
            }
        aviso_dv = (
            f"Dígito verificador não confere pelo cálculo oficial (informado {dv_informado}, "
            f"esperado {dv_calculado}) — pode ser numeração legada. Buscando mesmo assim com o "
            "número exatamente como digitado."
        )

    if segmento not in SEGMENTOS:
        return {"valido": False, "motivo": f"Segmento de Justiça desconhecido: {segmento}.", "aviso_dv": None, "partes": None}

    return {
        "valido": True,
        "motivo": None,
        "aviso_dv": aviso_dv,
        "partes": {
            "sequencial": sequencial,
            "digito_verificador": dv_informado,
            "ano": ano,
            "segmento_codigo": segmento,
            "segmento_nome": SEGMENTOS[segmento],
            "tribunal_codigo": tribunal,
            "origem_codigo": origem,
            "formatado": formatar_numero_cnj(d),
        },
    }
