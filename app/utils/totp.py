"""
Autenticação em duas etapas obrigatória (TOTP — RFC 6238, o mesmo padrão
usado por Google Authenticator/Authy/1Password/etc.) — pedido explícito de
segurança: "toda vez que o cliente for logar deve pedir o autenticador".

Fluxo, em duas pontas:
  - Conta NOVA (`auth.cadastrar_empresa`): o admin recém-criado já sai
    logado (`login_user`), mas direcionado direto pra tela de configurar o
    autenticador (app/routes/conta.py::configurar_totp) — só sai de lá
    quando confirma um código válido.
  - Conta EXISTENTE sem autenticador confirmado ainda (era o caso de toda
    conta cadastrada antes desta funcionalidade existir): loga normal
    (e-mail + senha, sem pedir código nenhum — ainda não tem o que pedir),
    mas cai no mesmo redirecionamento obrigatório pra tela de configuração
    (ver `app/__init__.py::exigir_autenticador_configurado`, o gate que
    bloqueia qualquer outra tela até isso ser feito).
  - Conta com autenticador JÁ confirmado: todo login passa a exigir o
    código de 6 dígitos ANTES de autenticar de verdade (ver
    `auth.verificar_totp` — a sessão fica "pendente" até o código bater,
    nunca chama `login_user` antes disso).

⚠️ Interruptor de segurança, não um bug: esta funcionalidade INTEIRA só
liga quando `TOTP_CIFRA_KEY` está configurada (ver `config.py`) — é a
chave Fernet que cifra o segredo TOTP em repouso. Sem essa variável
configurada, `login()` continua funcionando exatamente como antes (só
e-mail+senha, sem pedir nem oferecer autenticador nenhum) — NUNCA trava o
acesso de ninguém por causa de uma variável de ambiente que porventura não
foi configurada no deploy. Isso é deliberado: travar TODO login do sistema
(inclusive do admin) por uma env var esquecida seria um jeito de
"adicionar segurança" que na prática pode derrubar o sistema inteiro sem
ninguém conseguir entrar pra corrigir. Ver `totp_disponivel()` abaixo — é
o único ponto de verdade sobre se essa funcionalidade está ativa;
`app/__init__.py` e `app/routes/auth.py`/`conta.py` sempre checam por
aqui, nunca duplicam a lógica de "TOTP_CIFRA_KEY configurada?" direto.

**Por que uma chave PRÓPRIA (`TOTP_CIFRA_KEY`), em vez de reaproveitar o
cofre já existente (`COFRE_SENHA_PROCESSO_KEY`, ver app/utils/cofre.py) —
que já cifra as chaves de API BYOK e a senha de segredo de justiça?**
Porque, diferente daquelas, a presença de um segredo TOTP configurado
decide um GATE DE ACESSO GLOBAL (bloqueia toda tela até configurar — ver
`app/__init__.py`). Se essa disponibilidade estivesse amarrada ao MESMO
interruptor do cofre de BYOK, configurar uma chave de API do Gemini (um
recurso totalmente sem relação) ligaria SOZINHO a exigência obrigatória de
2FA pra empresa inteira, como efeito colateral surpresa de uma ação sem
nenhuma intenção de segurança por trás — comportamento ruim o bastante
para justificar uma variável de ambiente a mais, exclusiva desta
funcionalidade, mesmo repetindo o mesmo mecanismo de cifra (Fernet).
"""
import base64
import io

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

EMISSOR = "JusControl"


def _obter_fernet():
    chave = current_app.config.get("TOTP_CIFRA_KEY")
    if not chave:
        return None
    return Fernet(chave.encode() if isinstance(chave, str) else chave)


def totp_disponivel():
    """Único ponto de verdade: a funcionalidade de autenticador está
    ativa neste deploy? (ver docstring do módulo acima)."""
    try:
        return _obter_fernet() is not None
    except RuntimeError:  # fora de app context
        return False


def gerar_secret():
    import pyotp
    return pyotp.random_base32()


def obter_secret_pendente_ou_confirmado(usuario):
    """Decifra e devolve o segredo TOTP do usuário (pendente ou já
    confirmado, ver `Usuario.totp_configurado`), ou None se ele nunca
    gerou um (ou se `TOTP_CIFRA_KEY` não está configurada/a chave mudou —
    nunca propaga `InvalidToken`/erro de configuração pra quem chama,
    trata como "sem segredo utilizável", já que sem decifrar não tem como
    validar código nenhum mesmo)."""
    if not usuario or not usuario.totp_secret_cifrado:
        return None
    fernet = _obter_fernet()
    if fernet is None:
        return None
    try:
        return fernet.decrypt(usuario.totp_secret_cifrado).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def salvar_secret_pendente(usuario, secret_texto):
    """Cifra e grava o segredo (NÃO confirma sozinho — quem chama ainda
    precisa validar um código antes de marcar `totp_confirmado_em`, ver
    `confirmar` abaixo). Quem chama ainda precisa dar commit."""
    fernet = _obter_fernet()
    usuario.totp_secret_cifrado = fernet.encrypt(secret_texto.encode("utf-8"))


def verificar_codigo(secret_texto, codigo_informado):
    """Confere um código de 6 dígitos contra o segredo, tolerando até 1
    "passo" (30s) de diferença de relógio pra frente ou pra trás — telefone
    do usuário raramente está perfeitamente sincronizado."""
    import pyotp
    codigo_informado = (codigo_informado or "").strip().replace(" ", "")
    if not secret_texto or not codigo_informado:
        return False
    try:
        return pyotp.TOTP(secret_texto).verify(codigo_informado, valid_window=1)
    except Exception:
        return False


def confirmar(usuario, secret_texto):
    """Marca o segredo PENDENTE (já gravado por `salvar_secret_pendente`)
    como confirmado — só deve ser chamado depois de `verificar_codigo`
    devolver True. Quem chama ainda precisa dar commit."""
    from datetime import datetime
    usuario.totp_confirmado_em = datetime.utcnow()


def resetar(usuario):
    """Apaga o autenticador deste usuário (segredo pendente OU confirmado)
    — usado tanto por "reconfigurar autenticador" (o próprio usuário, após
    confirmar a senha atual) quanto por um admin resetando o de outra
    pessoa (ex.: perdeu o celular — ver app/routes/admin.py). Da próxima
    vez que fizer login, cai de novo no fluxo de "ainda não configurado"."""
    usuario.totp_secret_cifrado = None
    usuario.totp_confirmado_em = None


def construir_provisioning_uri(usuario, secret_texto):
    import pyotp
    return pyotp.totp.TOTP(secret_texto).provisioning_uri(name=usuario.email, issuer_name=EMISSOR)


def gerar_qrcode_data_uri(provisioning_uri):
    """PNG do QR code como data: URI, pra embutir direto no <img> do
    template sem precisar de uma rota separada (evita expor o segredo na
    URL de uma imagem, mesmo que só pra quem já está com a sessão aberta)."""
    import qrcode
    imagem = qrcode.make(provisioning_uri)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    base64_png = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{base64_png}"


def secret_formatado_para_digitar(secret_texto):
    """'ABCD1234EFGH5678' -> 'ABCD 1234 EFGH 5678' — só cosmético, pra
    quando o usuário não consegue escanear o QR e precisa digitar o
    segredo manualmente no app autenticador."""
    if not secret_texto:
        return ""
    return " ".join(secret_texto[i:i + 4] for i in range(0, len(secret_texto), 4))
