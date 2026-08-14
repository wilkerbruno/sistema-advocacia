"""
Cofre de senha de processo (seção 5.1 do briefing).

Cifra/decifra o valor com Fernet (AES-128 em modo CBC + HMAC, autenticado)
a partir da chave em `COFRE_SENHA_PROCESSO_KEY` (variável de ambiente —
em produção real, mover para um Vault/KMS dedicado, como o próprio README
já observa). Nunca grava a senha em texto puro no banco.
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


def cifrar_senha_processo(valor_texto_puro: str) -> bytes:
    f = _obter_fernet()
    return f.encrypt(valor_texto_puro.encode("utf-8"))


def decifrar_senha_processo(valor_cifrado: bytes) -> str:
    f = _obter_fernet()
    try:
        return f.decrypt(valor_cifrado).decode("utf-8")
    except InvalidToken:
        raise ValueError("Não foi possível decifrar a senha — chave do cofre pode ter mudado.")
