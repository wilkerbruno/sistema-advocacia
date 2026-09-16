"""
"Minha conta" — preferências PESSOAIS de cada usuário, diferente das telas
de app/routes/integracoes.py (que são da EMPRESA inteira, só pra admin).
Hoje só existe uma preferência: qual grupo do menu lateral (Operação,
Governança de carteira ou Configurações) este usuário quer que já apareça
ABERTO ao entrar no sistema — pedido explícito:

  "quero tambem que tenha uma opção de favoritos em configurações para o
  cliente selecionar uma categoria desse menu como favorito e ele ja
  aparecer aberto igual o 'operação'" (PENDENCIAS.md)

Isso veio junto com o pedido de "Operação" virar um grupo recolhível igual
aos outros dois (antes era o único sempre fixo/aberto) — ver
app/templates/base.html. Sem preferência nenhuma escolhida, o menu volta a
funcionar do jeito que já funcionava pra Governança/Configurações: cada
grupo abre sozinho só quando a página atual está dentro dele.

Por ser preferência de USUÁRIO (não de empresa), este blueprint não usa
`apenas_admin` — qualquer papel autenticado pode escolher a própria
categoria favorita, inclusive quem não gerencia nada.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db
from app.utils import totp as totp_utils
from app.utils.notificacoes import registrar_log

conta_bp = Blueprint("conta", __name__, url_prefix="/minha-conta")

# Mesmos data-grupo usados em app/templates/base.html — mantidos juntos
# aqui como a lista de valores válidos para não depender de string solta
# espalhada pelas duas pontas (rota e template).
GRUPOS_MENU_VALIDOS = ("operacao", "governanca", "config")

NOMES_GRUPOS_MENU = {
    "operacao": "Operação",
    "governanca": "Governança de carteira",
    "config": "Configurações",
}


@conta_bp.route("/preferencias")
@login_required
def preferencias():
    return render_template(
        "conta/preferencias.html",
        grupo_favorito=current_user.menu_grupo_favorito,
        grupos=GRUPOS_MENU_VALIDOS,
        nomes_grupos=NOMES_GRUPOS_MENU,
    )


@conta_bp.route("/preferencias/favorito", methods=["POST"])
@login_required
def salvar_favorito():
    grupo = (request.form.get("grupo_favorito") or "").strip() or None
    if grupo is not None and grupo not in GRUPOS_MENU_VALIDOS:
        flash("Selecione uma categoria do menu válida.", "danger")
        return redirect(url_for("conta.preferencias"))

    current_user.menu_grupo_favorito = grupo
    db.session.commit()

    if grupo:
        flash(f'"{NOMES_GRUPOS_MENU[grupo]}" marcada como favorita — ela já aparece aberta no menu.', "success")
    else:
        flash("Nenhuma categoria favorita — o menu volta a abrir sozinho só a categoria da página atual.", "info")
    return redirect(url_for("conta.preferencias"))


# ---------------------- Autenticador (2FA obrigatório) ----------------------
# PENDENCIAS.md, seção -104 — "toda vez que o cliente for logar deve pedir
# o autenticador". Esta é a tela de configuração (escanear o QR code e
# confirmar um código) — quem exige que TODO usuário passe por aqui antes
# de usar qualquer outra tela é o gate em
# app/__init__.py::exigir_autenticador_configurado, não esta rota; aqui só
# existe o formulário em si.

@conta_bp.route("/autenticador", methods=["GET"])
@login_required
def configurar_totp():
    if not totp_utils.totp_disponivel():
        flash("A autenticação em duas etapas não está disponível neste sistema no momento.", "warning")
        return redirect(url_for("dashboard.index"))

    if current_user.totp_configurado:
        return render_template("conta/configurar_totp.html", ja_configurado=True)

    # Reaproveita um segredo PENDENTE já gerado antes (ex.: o usuário saiu
    # da tela sem confirmar e voltou depois) em vez de trocar o QR a cada
    # visita — trocar sempre criaria um QR novo cada vez que a página
    # recarrega, e o usuário nunca conseguiria escanear a tempo.
    secret = totp_utils.obter_secret_pendente_ou_confirmado(current_user)
    if not secret:
        secret = totp_utils.gerar_secret()
        totp_utils.salvar_secret_pendente(current_user, secret)
        db.session.commit()

    uri = totp_utils.construir_provisioning_uri(current_user, secret)
    return render_template(
        "conta/configurar_totp.html", ja_configurado=False,
        qrcode_data_uri=totp_utils.gerar_qrcode_data_uri(uri),
        secret_formatado=totp_utils.secret_formatado_para_digitar(secret),
    )


@conta_bp.route("/autenticador/confirmar", methods=["POST"])
@login_required
def confirmar_totp():
    if not totp_utils.totp_disponivel():
        return redirect(url_for("dashboard.index"))

    secret = totp_utils.obter_secret_pendente_ou_confirmado(current_user)
    codigo = request.form.get("codigo", "")

    if secret and totp_utils.verificar_codigo(secret, codigo):
        totp_utils.confirmar(current_user, secret)
        registrar_log(current_user, "configurou_autenticador", "Usuario", current_user.id)
        db.session.commit()
        flash("Autenticador configurado com sucesso! A partir de agora, todo login vai pedir o código "
              "gerado pelo seu app.", "success")
        return redirect(request.form.get("proximo") or url_for("dashboard.index"))

    flash("Código inválido — confira o app autenticador (o código muda a cada 30 segundos) e tente "
          "de novo. Se preferir, escaneie o QR code novamente antes de digitar.", "danger")
    return redirect(url_for("conta.configurar_totp"))


@conta_bp.route("/autenticador/reconfigurar", methods=["POST"])
@login_required
def reconfigurar_totp():
    """Só existe para quem JÁ tem o autenticador confirmado (ex.: trocou
    de celular) — exige a senha atual antes de invalidar o autenticador
    confirmado, pra uma sessão de navegador esquecida aberta não conseguir
    sozinha rebaixar a segurança da conta sem confirmar de novo que é
    mesmo o dono dela."""
    if not totp_utils.totp_disponivel() or not current_user.totp_configurado:
        return redirect(url_for("conta.configurar_totp"))

    senha_atual = request.form.get("senha_atual", "")
    if not current_user.checar_senha(senha_atual):
        flash("Senha atual incorreta — o autenticador não foi alterado.", "danger")
        return redirect(url_for("conta.configurar_totp"))

    totp_utils.resetar(current_user)
    registrar_log(current_user, "reconfigurou_autenticador", "Usuario", current_user.id)
    db.session.commit()
    flash("Autenticador removido — escaneie o novo QR code abaixo para configurar de novo.", "info")
    return redirect(url_for("conta.configurar_totp"))
