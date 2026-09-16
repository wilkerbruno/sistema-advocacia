"""
Política de força de senha — pedido explícito para a NOVA senha do fluxo
"esqueci minha senha" (app/routes/auth.py): mínimo 8 caracteres, pelo menos
1 letra maiúscula, 1 minúscula, 1 número e 1 caractere especial.

Módulo isolado (não misturado dentro de auth.py) de propósito: além do
reuso óbvio (usado tanto na validação de "nova senha" quanto na de
"confirmar nova senha", que precisa bater com a primeira), fica pronto
para o dia em que a mesma regra precisar valer em algum outro lugar que
define senha (ex.: cadastro de empresa, cadastro de usuário pelo admin —
hoje esses dois ainda pedem só "6 caracteres", um padrão mais antigo e
mais fraco que não foi tocado nesta rodada para não mudar comportamento
que ninguém pediu para mudar).
"""
import re

REQUISITOS_TEXTO = (
    "mínimo de 8 caracteres, incluindo pelo menos 1 letra maiúscula, 1 letra minúscula, "
    "1 número e 1 caractere especial (ex.: ! @ # $ % & * - _ .)"
)

_PADRAO_MAIUSCULA = re.compile(r"[A-ZÀ-Ý]")
_PADRAO_MINUSCULA = re.compile(r"[a-zà-ÿ]")
_PADRAO_NUMERO = re.compile(r"[0-9]")
# "Caractere especial" = nem letra (com acento), nem número, nem espaço.
_PADRAO_ESPECIAL = re.compile(r"[^A-Za-zÀ-ÿ0-9\s]")


def validar_forca_senha(senha):
    """Devolve uma lista de erros (strings) — vazia quando a senha atende
    a todos os requisitos. Nunca levanta exceção; `senha` vazia/None só
    gera o erro de tamanho mínimo, como qualquer outra senha curta."""
    senha = senha or ""
    erros = []
    if len(senha) < 8:
        erros.append("A senha precisa ter pelo menos 8 caracteres.")
    if not _PADRAO_MAIUSCULA.search(senha):
        erros.append("A senha precisa ter pelo menos 1 letra MAIÚSCULA.")
    if not _PADRAO_MINUSCULA.search(senha):
        erros.append("A senha precisa ter pelo menos 1 letra minúscula.")
    if not _PADRAO_NUMERO.search(senha):
        erros.append("A senha precisa ter pelo menos 1 número.")
    if not _PADRAO_ESPECIAL.search(senha):
        erros.append("A senha precisa ter pelo menos 1 caractere especial (ex.: ! @ # $ % & * - _ .).")
    return erros
