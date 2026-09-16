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
