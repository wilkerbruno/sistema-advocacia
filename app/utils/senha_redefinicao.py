"""
"Esqueci minha senha" (app/routes/auth.py): código de 6 dígitos enviado
por e-mail, com prazo de validade e limite de tentativas erradas — nunca
guardado em texto puro no banco (mesmo hash do werkzeug já usado pra
senha, ver `Usuario.set_senha`).

Cuidado deliberado contra enumeração de e-mail cadastrado: a rota que usa
este módulo (`auth.esqueci_senha`) sempre mostra a MESMA mensagem
genérica ("se este e-mail existir, enviamos um código"), tanto quando o
e-mail existe quanto quando não existe — só CHAMA `gerar_e_enviar_codigo`
quando o `Usuario` foi encontrado de verdade; quando não foi, a etapa
seguinte (digitar o código) simplesmente nunca vai ter um código correto
pra bater, dando o mesmo erro genérico de "código inválido" que uma conta
real com código errado/expirado daria — indistinguível de fora.

Cada código só é usado por CONTA (guardado nas colunas
`reset_senha_*` do próprio `Usuario`, sem tabela separada) — mais simples
do que uma tabela nova, e nunca precisa de índice extra, já que a consulta
sempre parte de um `Usuario` já carregado por e-mail.
"""
import secrets
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

from app.utils.email import enviar_email, smtp_configurado

CODIGO_VALIDADE_MINUTOS = 15
MAX_TENTATIVAS = 5


def gerar_e_enviar_codigo(usuario):
    """Gera um código de 6 dígitos, grava o HASH (nunca o código em si) e
    tenta enviar por e-mail. Devolve True/False conforme o envio (o mesmo
    retorno honesto de `enviar_email` — sem SMTP configurado, ou falha de
    rede/credencial, devolve False e não finge que enviou). Quem chama
    ainda precisa dar commit."""
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    usuario.reset_senha_codigo_hash = generate_password_hash(codigo)
    usuario.reset_senha_expira_em = datetime.utcnow() + timedelta(minutes=CODIGO_VALIDADE_MINUTOS)
    usuario.reset_senha_tentativas = 0

    corpo = (
        f"Olá, {usuario.nome}.\n\n"
        f"Recebemos um pedido para redefinir sua senha no JusControl. Use o código abaixo para continuar:\n\n"
        f"    {codigo}\n\n"
        f"Esse código é válido por {CODIGO_VALIDADE_MINUTOS} minutos e só pode ser usado uma vez.\n\n"
        "Se você não pediu essa redefinição, pode ignorar este e-mail com segurança — sua senha "
        "continua a mesma."
    )
    return enviar_email(usuario.email, "Código para redefinir sua senha — JusControl", corpo)


def validar_codigo(usuario, codigo_informado):
    """Devolve (ok: bool, erro: str|None). Em caso de código errado,
    INCREMENTA `reset_senha_tentativas` (quem chama ainda precisa dar
    commit) — depois de `MAX_TENTATIVAS`, o código pendente para de valer
    (mesmo se o próximo palpite acertasse por sorte), forçando pedir um
    código novo."""
    if not usuario or not usuario.reset_senha_codigo_hash or not usuario.reset_senha_expira_em:
        return False, "Nenhum código pendente para esta conta — solicite um novo."
    if (usuario.reset_senha_tentativas or 0) >= MAX_TENTATIVAS:
        return False, "Muitas tentativas com código incorreto — solicite um novo código."
    if datetime.utcnow() > usuario.reset_senha_expira_em:
        return False, "Este código expirou — solicite um novo."

    codigo_informado = (codigo_informado or "").strip()
    if not check_password_hash(usuario.reset_senha_codigo_hash, codigo_informado):
        usuario.reset_senha_tentativas = (usuario.reset_senha_tentativas or 0) + 1
        return False, "Código inválido."

    return True, None


def limpar_codigo(usuario):
    """Consome/invalida o código pendente — chamado tanto depois de uma
    redefinição de senha concluída com sucesso quanto se o usuário desistir
    e pedir um código novo (nunca deixa o código antigo continuar valendo
    depois que um novo foi emitido)."""
    usuario.reset_senha_codigo_hash = None
    usuario.reset_senha_expira_em = None
    usuario.reset_senha_tentativas = None
