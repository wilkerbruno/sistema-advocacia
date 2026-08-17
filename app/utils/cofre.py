"""
Cofre de segredos (seção 5.1 do briefing, e desde a rodada BYOK também
usado para as chaves de API que cada empresa cadastra por conta própria —
ver app/utils/agente_ia_router.py e app/routes/integracoes.py).

Cifra/decifra o valor com Fernet (AES-128 em modo CBC + HMAC, autenticado)
a partir da chave em `COFRE_SENHA_PROCESSO_KEY` (variável de ambiente —
em produção real, mover para um Vault/KMS dedicado, como o próprio README
já observa). Nunca grava segredo em texto puro no banco — nem senha de
processo, nem chave de API de terceiro.
"""
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


class CofreNaoConfiguradoError(Exception):
    """Levantado quando COFRE_SENHA_PROCESSO_KEY não está definida no ambiente."""


def _obter_fernet() -> Fernet:
    chave = current_app.config.get("COFRE_SENHA_PROCESSO_KEY")
    if not chave:
        raise CofreNaoConfiguradoError(
            "COFRE_SENHA_PROCESSO_KEY não está configurada no ambiente. "
            "Gere uma chave com: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\" e defina no .env."
        )
    return Fernet(chave.encode() if isinstance(chave, str) else chave)


def cifrar_segredo(valor_texto_puro: str) -> bytes:
    """Cifra qualquer segredo de texto (senha de processo, chave de API de
    terceiro cadastrada por uma empresa etc) com o mesmo cofre. Nome
    genérico — use este para qualquer segredo novo."""
    f = _obter_fernet()
    return f.encrypt(valor_texto_puro.encode("utf-8"))


def decifrar_segredo(valor_cifrado: bytes) -> str:
    f = _obter_fernet()
    try:
        return f.decrypt(valor_cifrado).decode("utf-8")
    except InvalidToken:
        raise ValueError("Não foi possível decifrar o segredo — chave do cofre pode ter mudado.")


# Aliases mantidos por compatibilidade com o código já existente (seção 5.1
# — senha de processo em segredo de justiça). Novo código deve preferir
# cifrar_segredo/decifrar_segredo diretamente.
def cifrar_senha_processo(valor_texto_puro: str) -> bytes:
    return cifrar_segredo(valor_texto_puro)


def decifrar_senha_processo(valor_cifrado: bytes) -> str:
    return decifrar_segredo(valor_cifrado)
