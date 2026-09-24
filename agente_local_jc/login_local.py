"""
Login local (OAB + senha) do Agente Local — pedido do usuário: "quero que
tenha a opção do usuario logar com o oab do cliente, porem localmente".

Isto é uma coisa DIFERENTE das outras duas credenciais que já existiam:
  - O certificado A1 + senha (certificado.py) autentica no TRIBUNAL.
  - O token de pareamento (config_store.py) autentica este agente no
    SERVIDOR do JusControl.
  - Este login (OAB + senha) é só uma tela de CADEADO pro programa em si,
    nesta máquina — nunca é conferido contra o servidor, nunca sai
    daqui. Serve só pra impedir que outra pessoa com acesso físico ao
    computador abra o agente (e veja o status/log de processos) enquanto
    o dono de verdade não está por perto.

Opcional por desenho: se `oab_local`/`oab_senha_hash`/`oab_senha_salt`
nunca forem preenchidos (tela "Configurar...", seção "Login local"), o
agente abre direto, sem pedir nada — comportamento de sempre, não quebra
quem já usa o agente hoje.

Só a SENHA é protegida por hash (PBKDF2-HMAC-SHA256, sal aleatório de 16
bytes, 200 mil iterações — a mesma família de algoritmo que
`werkzeug.security.generate_password_hash` usa por padrão no sistema web,
reimplementada aqui só com a biblioteca padrão do Python pra não
adicionar dependência nova só por causa disto) — a senha em texto puro
NUNCA é gravada em `config.json`, só o par (hash, sal).
"""
import hashlib
import hmac
import os

ITERACOES_PBKDF2 = 200_000
TAMANHO_SALT_BYTES = 16
TAMANHO_MINIMO_SENHA = 6


def login_local_configurado(dados):
    """True quando o advogado configurou OAB + senha local (os três
    campos precisam estar presentes — um hash sem OAB, ou um OAB sem
    hash, é tratado como "não configurado", nunca bloqueia sem ter como
    validar nada)."""
    return bool(dados.get("oab_local") and dados.get("oab_senha_hash") and dados.get("oab_senha_salt"))


def gerar_hash(senha):
    """Devolve (hash_hex, salt_hex) para uma senha nova em texto puro —
    usado só na hora de SALVAR (tela de Configuração), nunca guardado além
    disso."""
    salt = os.urandom(TAMANHO_SALT_BYTES)
    hash_bytes = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, ITERACOES_PBKDF2)
    return hash_bytes.hex(), salt.hex()


def verificar_senha(senha, hash_hex, salt_hex):
    """Confere uma senha digitada contra o (hash, sal) salvos — comparação
    em tempo constante (`hmac.compare_digest`) pra não vazar quantos
    caracteres bateram através do tempo de resposta."""
    if not senha or not hash_hex or not salt_hex:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        esperado = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    calculado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, ITERACOES_PBKDF2)
    return hmac.compare_digest(calculado, esperado)


def validar_senha_nova(senha):
    """Regra mínima pra aceitar uma senha nova na tela de Configuração —
    só tamanho, sem exigir maiúscula/número/símbolo (é uma senha de
    cadeado local, não a senha de uma conta online): devolve None se
    estiver OK, ou uma mensagem de erro pra mostrar na tela."""
    if len(senha or "") < TAMANHO_MINIMO_SENHA:
        return f"A senha do login local precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres."
    return None
