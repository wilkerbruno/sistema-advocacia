"""
"Minhas Integrações" — cada empresa cliente escolhe, para o próprio
tenant, entre os provedores gratuitos padrão da plataforma e trazer a
PRÓPRIA chave de API (BYOK — "Bring Your Own Key"), a pedido explícito
("coloque a opção do cliente escolher usar o nosso agente local ou inserir
uma chave API do claude [...] quero que ocorra o mesmo com o DataJud").

Duas integrações independentes:
  - Agente de IA: modelo local gratuito (padrão) OU API do Claude com
    chave própria (a empresa paga a Anthropic diretamente — ver
    app/utils/claude_api.py para o porquê de ser BYOK e não markup).
  - Captura processual (DataJud): chave padrão da plataforma (padrão) OU
    chave própria da empresa no DataJud (também gratuita, cadastro
    individual em https://datajud-wiki.cnj.jus.br/).

Provedores pagos de captura (Judit/Escavador/Digesto/Codilo) NÃO estão
disponíveis aqui — ver o comentário em app/utils/captura_conectores.py
sobre por que isso ficou de fora desta rodada (cada um tem um contrato de
API próprio; implementar "no escuro" sem a documentação e credenciais
reais do provedor contratado arriscaria parecer funcionar e devolver dado
errado). O ponto de extensão (ConectorCaptura) já existe pra quando um
desses for contratado de verdade.

Nunca acessível para a empresa dona da plataforma (ela usa a configuração
global do .env normalmente) nem para quem não é admin da própria empresa
— mesmo padrão de app/routes/licenciamento.py.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Empresa
from app.utils.acesso import apenas_admin
from app.utils.notificacoes import registrar_log
from app.utils import cofre, claude_api

integracoes_bp = Blueprint("integracoes", __name__)


def _empresa_cliente():
    """Empresa da própria sessão, ou None (com flash já emitido) se não fizer
    sentido pra este usuário (admin desenvolvedor / dono da plataforma)."""
    empresa = current_user.empresa
    if empresa is None or empresa.dono_da_plataforma:
        flash("Esta área é só para empresas clientes — a própria plataforma usa a configuração do "
              "servidor (.env) diretamente.", "warning")
        return None
    return empresa


@integracoes_bp.route("/minhas-integracoes")
@login_required
@apenas_admin
def minhas_integracoes():
    empresa = _empresa_cliente()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    return render_template(
        "integracoes/minhas_integracoes.html",
        empresa=empresa,
        ia_provedor=empresa.agente_ia_provedor_efetivo,
        ia_tem_chave=bool(empresa.agente_ia_claude_chave_cifrada),
        ia_modelo=empresa.agente_ia_claude_modelo or claude_api.MODELO_PADRAO,
        modelo_claude_padrao=claude_api.MODELO_PADRAO,
        datajud_provedor=empresa.datajud_provedor_efetivo,
        datajud_tem_chave=bool(empresa.datajud_chave_propria_cifrada),
    )


@integracoes_bp.route("/minhas-integracoes/ia", methods=["POST"])
@login_required
@apenas_admin
def salvar_ia():
    empresa = _empresa_cliente()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    provedor = request.form.get("provedor")
    if provedor not in (Empresa.PROVEDOR_IA_LOCAL, Empresa.PROVEDOR_IA_CLAUDE_BYOK):
        flash("Selecione um provedor de IA válido.", "danger")
        return redirect(url_for("integracoes.minhas_integracoes"))

    nova_chave = request.form.get("api_key", "").strip()
    modelo = request.form.get("modelo", "").strip()

    if provedor == Empresa.PROVEDOR_IA_CLAUDE_BYOK:
        if nova_chave:
            try:
                claude_api.validar_chave(nova_chave, modelo or None)
            except claude_api.ClaudeIndisponivelError as e:
                flash(f"Não foi possível validar a chave informada — nada foi salvo: {e}", "danger")
                return redirect(url_for("integracoes.minhas_integracoes"))
            try:
                empresa.agente_ia_claude_chave_cifrada = cofre.cifrar_segredo(nova_chave)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(url_for("integracoes.minhas_integracoes"))
        elif not empresa.agente_ia_claude_chave_cifrada:
            flash("Cadastre uma chave de API do Claude antes de ativar este provedor — gere uma em "
                  "https://console.anthropic.com/settings/keys.", "danger")
            return redirect(url_for("integracoes.minhas_integracoes"))
        empresa.agente_ia_claude_modelo = modelo or None

    empresa.agente_ia_provedor = provedor
    registrar_log(current_user, "configurou_agente_ia", "Empresa", empresa.id, provedor)
    db.session.commit()
    flash("Configuração do Agente de IA atualizada.", "success")
    return redirect(url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/ia/remover-chave", methods=["POST"])
@login_required
@apenas_admin
def remover_chave_ia():
    empresa = _empresa_cliente()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    empresa.agente_ia_claude_chave_cifrada = None
    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_LOCAL
    registrar_log(current_user, "removeu_chave_claude", "Empresa", empresa.id)
    db.session.commit()
    flash("Chave da API do Claude removida — o Agente de IA voltou a usar o modelo local gratuito.", "info")
    return redirect(url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/datajud", methods=["POST"])
@login_required
@apenas_admin
def salvar_datajud():
    empresa = _empresa_cliente()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    provedor = request.form.get("provedor")
    if provedor not in (Empresa.PROVEDOR_DATAJUD_PADRAO, Empresa.PROVEDOR_DATAJUD_CHAVE_PROPRIA):
        flash("Selecione um provedor de captura válido.", "danger")
        return redirect(url_for("integracoes.minhas_integracoes"))

    nova_chave = request.form.get("api_key", "").strip()
    if provedor == Empresa.PROVEDOR_DATAJUD_CHAVE_PROPRIA:
        if nova_chave:
            try:
                empresa.datajud_chave_propria_cifrada = cofre.cifrar_segredo(nova_chave)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(url_for("integracoes.minhas_integracoes"))
        elif not empresa.datajud_chave_propria_cifrada:
            flash("Cadastre sua chave própria do DataJud antes de ativar esta opção — cadastro "
                  "gratuito em https://datajud-wiki.cnj.jus.br/.", "danger")
            return redirect(url_for("integracoes.minhas_integracoes"))

    empresa.datajud_provedor = provedor
    registrar_log(current_user, "configurou_datajud", "Empresa", empresa.id, provedor)
    db.session.commit()
    flash("Configuração de captura processual (DataJud) atualizada.", "success")
    return redirect(url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/datajud/remover-chave", methods=["POST"])
@login_required
@apenas_admin
def remover_chave_datajud():
    empresa = _empresa_cliente()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    empresa.datajud_chave_propria_cifrada = None
    empresa.datajud_provedor = Empresa.PROVEDOR_DATAJUD_PADRAO
    registrar_log(current_user, "removeu_chave_datajud", "Empresa", empresa.id)
    db.session.commit()
    flash("Chave própria do DataJud removida — a captura voltou a usar a chave padrão da plataforma.", "info")
    return redirect(url_for("integracoes.minhas_integracoes"))
