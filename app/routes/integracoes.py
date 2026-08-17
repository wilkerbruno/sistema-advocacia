"""
"Minhas Integrações" — cada empresa cliente escolhe, para o próprio
tenant, entre os provedores gratuitos padrão da plataforma e trazer a
PRÓPRIA chave/número de API (BYOK — "Bring Your Own Key"), a pedido
explícito ("coloque a opção do cliente escolher usar o nosso agente local
ou inserir uma chave API do claude [...] quero que ocorra o mesmo com o
DataJud"; depois: "cada empresa cadastrasse um whatsapp para enviar essas
mensagens [...] as empresas não vão ter acesso a esse whatsapp pra
responder dúvidas dos clientes").

Três integrações independentes:
  - Agente de IA: modelo local gratuito (padrão) OU API do Claude com
    chave própria (a empresa paga a Anthropic diretamente — ver
    app/utils/claude_api.py para o porquê de ser BYOK e não markup).
  - Captura processual (DataJud): chave padrão da plataforma (padrão) OU
    chave própria da empresa no DataJud (também gratuita, cadastro
    individual em https://datajud-wiki.cnj.jus.br/).
  - WhatsApp dos lembretes da Agenda: cada empresa conecta o PRÓPRIO
    número, escaneando um QR code, em vez de todas as empresas
    compartilharem o número da plataforma — ver app/utils/whatsapp.py
    (seção "MULTI-SESSÃO") pra como isso funciona por baixo (WAHA
    continua sendo um servidor só, compartilhado; cada empresa ganha uma
    sessão própria nele).

Provedores pagos de captura (Judit/Escavador/Digesto/Codilo) NÃO estão
disponíveis aqui — ver o comentário em app/utils/captura_conectores.py
sobre por que isso ficou de fora desta rodada (cada um tem um contrato de
API próprio; implementar "no escuro" sem a documentação e credenciais
reais do provedor contratado arriscaria parecer funcionar e devolver dado
errado). O ponto de extensão (ConectorCaptura) já existe pra quando um
desses for contratado de verdade.

Também acessível para o admin desenvolvedor (empresa dona da plataforma) —
a pedido explícito, pra poder configurar/testar o provedor de IA (e agora
também o WhatsApp) da própria conta da plataforma por aqui em vez de só
via variável de ambiente legada (ANTHROPIC_API_KEY em config.py) ou o
dashboard do WAHA direto. Único requisito: ser admin (`apenas_admin`) de
alguma empresa — mesmo padrão de app/routes/licenciamento.py, exceto que
licenciamento continua bloqueado pra empresa dona da plataforma (ela não
tem licença) e esta tela não.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, Response, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Empresa
from app.utils.acesso import apenas_admin
from app.utils.notificacoes import registrar_log
from app.utils import cofre, claude_api, whatsapp

integracoes_bp = Blueprint("integracoes", __name__)


def _empresa_atual():
    """Empresa da própria sessão (cliente OU a própria plataforma), ou None
    (com flash já emitido) no caso raro de um admin sem nenhuma empresa
    vinculada."""
    empresa = current_user.empresa
    if empresa is None:
        flash("Seu usuário não está vinculado a uma empresa.", "warning")
        return None
    return empresa


@integracoes_bp.route("/minhas-integracoes")
@login_required
@apenas_admin
def minhas_integracoes():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    whatsapp_status, whatsapp_numero, whatsapp_erro = None, None, None
    nome_sessao = empresa.whatsapp_sessao_efetiva
    if whatsapp.whatsapp_configurado() and nome_sessao:
        try:
            whatsapp_status, dados_sessao = whatsapp.status_sessao(nome_sessao)
            whatsapp_numero = (dados_sessao.get("me") or {}).get("id") if whatsapp_status == "WORKING" else None
        except whatsapp.SessaoWhatsAppError as e:
            whatsapp_erro = str(e)

    return render_template(
        "integracoes/minhas_integracoes.html",
        empresa=empresa,
        ia_provedor=empresa.agente_ia_provedor_efetivo,
        ia_tem_chave=bool(empresa.agente_ia_claude_chave_cifrada),
        ia_modelo=empresa.agente_ia_claude_modelo or claude_api.MODELO_PADRAO,
        modelo_claude_padrao=claude_api.MODELO_PADRAO,
        datajud_provedor=empresa.datajud_provedor_efetivo,
        datajud_tem_chave=bool(empresa.datajud_chave_propria_cifrada),
        whatsapp_bridge_configurado=whatsapp.whatsapp_configurado(),
        whatsapp_status=whatsapp_status,
        whatsapp_numero=whatsapp_numero,
        whatsapp_erro=whatsapp_erro,
    )


@integracoes_bp.route("/minhas-integracoes/ia", methods=["POST"])
@login_required
@apenas_admin
def salvar_ia():
    empresa = _empresa_atual()
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
    empresa = _empresa_atual()
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
    empresa = _empresa_atual()
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
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    empresa.datajud_chave_propria_cifrada = None
    empresa.datajud_provedor = Empresa.PROVEDOR_DATAJUD_PADRAO
    registrar_log(current_user, "removeu_chave_datajud", "Empresa", empresa.id)
    db.session.commit()
    flash("Chave própria do DataJud removida — a captura voltou a usar a chave padrão da plataforma.", "info")
    return redirect(url_for("integracoes.minhas_integracoes"))


# ---------------------- WhatsApp (uma sessão do WAHA por empresa) ----------------------

@integracoes_bp.route("/minhas-integracoes/whatsapp/conectar", methods=["POST"])
@login_required
@apenas_admin
def conectar_whatsapp():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    if not whatsapp.whatsapp_configurado():
        flash("O recurso de WhatsApp não está configurado neste servidor (WHATSAPP_BRIDGE_URL ausente).", "danger")
        return redirect(url_for("integracoes.minhas_integracoes"))

    # Primeira conexão desta empresa: gera um nome de sessão próprio e
    # exclusivo dela (nunca reaproveita "default", que é da plataforma).
    # Reconexões (empresa que já tinha um nome de sessão salvo) reusam o
    # mesmo nome — o WAHA simplesmente gera um QR code novo pra ele.
    nome_sessao = empresa.whatsapp_sessao_efetiva or f"empresa-{empresa.id}"
    try:
        whatsapp.conectar_sessao(nome_sessao)
    except whatsapp.SessaoWhatsAppError as e:
        flash(f"Não foi possível iniciar a conexão com o WAHA: {e}", "danger")
        return redirect(url_for("integracoes.minhas_integracoes"))

    if empresa.whatsapp_sessao != nome_sessao:
        empresa.whatsapp_sessao = nome_sessao
        registrar_log(current_user, "conectou_whatsapp", "Empresa", empresa.id, nome_sessao)
        db.session.commit()

    flash("Escaneie o QR code abaixo com o WhatsApp que a empresa vai usar pra enviar os lembretes.", "info")
    return redirect(url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/whatsapp/qr")
@login_required
@apenas_admin
def qr_whatsapp():
    """Serve a imagem PNG do QR code atual da sessão desta empresa — nunca
    expõe a URL/token do WAHA pro navegador do cliente, o backend busca e
    repassa os bytes. Usado como `src` de um <img> que a página recarrega
    periodicamente (o QR do WAHA expira em segundos)."""
    empresa = _empresa_atual()
    if empresa is None:
        abort(403)
    nome_sessao = empresa.whatsapp_sessao_efetiva
    if not nome_sessao:
        abort(404)
    imagem = whatsapp.qr_sessao_png(nome_sessao)
    if imagem is None:
        abort(404)
    return Response(imagem, mimetype="image/png", headers={"Cache-Control": "no-store"})


@integracoes_bp.route("/minhas-integracoes/whatsapp/status")
@login_required
@apenas_admin
def status_whatsapp():
    """Endpoint JSON usado pelo polling em JS da página — pra saber quando
    a sessão passou de "esperando QR" pra "conectada" sem precisar recarregar
    a página inteira a cada segundo."""
    empresa = _empresa_atual()
    if empresa is None:
        abort(403)
    nome_sessao = empresa.whatsapp_sessao_efetiva
    if not nome_sessao or not whatsapp.whatsapp_configurado():
        return jsonify(status="NAO_CONFIGURADA")
    try:
        status, dados = whatsapp.status_sessao(nome_sessao)
    except whatsapp.SessaoWhatsAppError as e:
        return jsonify(status="ERRO", erro=str(e))
    numero = (dados.get("me") or {}).get("id") if status == "WORKING" else None
    return jsonify(status=status, numero=numero)


@integracoes_bp.route("/minhas-integracoes/whatsapp/desconectar", methods=["POST"])
@login_required
@apenas_admin
def desconectar_whatsapp():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    nome_sessao = empresa.whatsapp_sessao_efetiva
    if nome_sessao:
        whatsapp.desconectar_sessao(nome_sessao)

    empresa.whatsapp_sessao = None
    registrar_log(current_user, "desconectou_whatsapp", "Empresa", empresa.id)
    db.session.commit()
    flash("Número de WhatsApp desconectado. Os lembretes por WhatsApp desta empresa ficam pausados até "
          "conectar outro número.", "info")
    return redirect(url_for("integracoes.minhas_integracoes"))
