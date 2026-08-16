"""
Catálogo de tribunais suportados pela API pública do DataJud (CNJ) — ver
app/utils/conector_datajud.py.

Importante sobre como esta lista foi montada: em vez de tentar decifrar o
código numérico do tribunal (TR) que vem embutido no número CNJ para
processos da Justiça Estadual/Federal/etc — o que exigiria uma tabela de
~90 códigos que não consegui confirmar com segurança em todas as posições —,
a lista abaixo usa só a CONVENÇÃO DE NOMES dos endpoints do DataJud, que é
simples e sem ambiguidade:

    - Trabalhista:  "trt" + número da região (1 a 24)
    - Estadual:      "tj"  + sigla do estado (as 27 siglas oficiais do Brasil)
    - Federal:       "trf" + número da região (1 a 6, após a criação do TRF6)
    - Superiores:    "stj", "tst", "stf", "tse", "stm"

Isso é 100% confiável para o slug em si. O que NÃO dá pra fazer com
segurança sem essa tabela de códigos é ADIVINHAR automaticamente qual
desses tribunais corresponde a um processo Estadual/Federal só pelo número
— por isso, para a Justiça do Trabalho (onde o número da região do TRT
está diretamente no número do processo, sem ambiguidade — ver
app/utils/cnj.py) a identificação é automática; para os demais segmentos,
o usuário escolhe o tribunal manualmente uma vez (campo `tribunal_datajud`
em Processo), nunca é um chute do sistema.
"""

TRT = [(f"trt{n}", f"TRT da {n}ª Região") for n in range(1, 25)]

_ESTADOS = {
    "ac": "Acre", "al": "Alagoas", "ap": "Amapá", "am": "Amazonas", "ba": "Bahia",
    "ce": "Ceará", "df": "Distrito Federal", "es": "Espírito Santo", "go": "Goiás",
    "ma": "Maranhão", "mt": "Mato Grosso", "ms": "Mato Grosso do Sul", "mg": "Minas Gerais",
    "pa": "Pará", "pb": "Paraíba", "pr": "Paraná", "pe": "Pernambuco", "pi": "Piauí",
    "rj": "Rio de Janeiro", "rn": "Rio Grande do Norte", "rs": "Rio Grande do Sul",
    "ro": "Rondônia", "rr": "Roraima", "sc": "Santa Catarina", "sp": "São Paulo",
    "se": "Sergipe", "to": "Tocantins",
}
# DF usa "tjdft" (Tribunal de Justiça do Distrito Federal e dos Territórios),
# não "tjdf" — único caso fora do padrão "tj" + sigla.
TJ = [("tjdft", "TJ do Distrito Federal e dos Territórios")] + [
    (f"tj{uf}", f"TJ de {nome}") for uf, nome in _ESTADOS.items() if uf != "df"
]

TRF = [(f"trf{n}", f"TRF da {n}ª Região") for n in range(1, 7)]

SUPERIORES = [
    ("stj", "STJ — Superior Tribunal de Justiça"),
    ("tst", "TST — Tribunal Superior do Trabalho"),
    ("stf", "STF — Supremo Tribunal Federal"),
    ("tse", "TSE — Tribunal Superior Eleitoral"),
    ("stm", "STM — Superior Tribunal Militar"),
]

TODOS = TRT + TJ + TRF + SUPERIORES  # [(slug, rótulo), ...] — usado nos <select> dos formulários

SLUGS_VALIDOS = {slug for slug, _ in TODOS}


def slug_valido(slug):
    return bool(slug) and slug in SLUGS_VALIDOS
