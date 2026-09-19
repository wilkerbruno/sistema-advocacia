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
    app/utils/claude_api.py para o porquê de ser BYOK e não markup) OU API
    do Gemini com chave própria (a empresa paga o Google diretamente — ver
    app/utils/gemini_api.py; exige faturamento ativo no projeto do Google,
    o nível gratuito da API do Gemini não é aceito aqui).
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
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, Response, abort, current_app
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Empresa, CredencialTribunal
from app.utils.acesso import apenas_admin
from app.utils.notificacoes import registrar_log
from app.utils import cofre, claude_api, gemini_api, whatsapp, timbrado

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


def _contexto_minhas_integracoes():
    """
    Reúne o contexto de "Minhas Integrações" num dict — extraído de
    `minhas_integracoes()` pra ser reaproveitado também pelo hub `admin.
    minha_empresa()` (menu simplificado). Usa `current_user.empresa`
    diretamente em vez de `_empresa_atual()` de propósito: aquela função
    dispara um `flash()` quando não há empresa, e isso duplicaria o aviso
    se o hub também chamasse — quem monta o hub já decide sozinho se
    mostra esta aba ou não a partir do retorno `None` daqui, sem precisar
    de aviso nenhum (mesmo padrão de `conta._contexto_totp()` e
    `licenciamento._contexto_minha_licenca()`).
    """
    empresa = current_user.empresa
    if empresa is None:
        return None

    whatsapp_status, whatsapp_numero, whatsapp_erro = None, None, None
    nome_sessao = empresa.whatsapp_sessao_efetiva
    if whatsapp.whatsapp_configurado() and nome_sessao:
        try:
            whatsapp_status, dados_sessao = whatsapp.status_sessao(nome_sessao)
            whatsapp_numero = (dados_sessao.get("me") or {}).get("id") if whatsapp_status == "WORKING" else None
        except whatsapp.SessaoWhatsAppError as e:
            whatsapp_erro = str(e)

    return dict(
        empresa=empresa,
        ia_provedor=empresa.agente_ia_provedor_efetivo,
        ia_tem_chave=bool(empresa.agente_ia_claude_chave_cifrada),
        ia_modelo=empresa.agente_ia_claude_modelo or claude_api.MODELO_PADRAO,
        modelo_claude_padrao=claude_api.MODELO_PADRAO,
        ia_gemini_tem_chave=bool(empresa.agente_ia_gemini_chave_cifrada),
        ia_gemini_modelo=empresa.agente_ia_gemini_modelo or gemini_api.MODELO_PADRAO,
        modelo_gemini_padrao=gemini_api.MODELO_PADRAO,
        datajud_provedor=empresa.datajud_provedor_efetivo,
        datajud_tem_chave=bool(empresa.datajud_chave_propria_cifrada),
        whatsapp_bridge_configurado=whatsapp.whatsapp_configurado(),
        whatsapp_status=whatsapp_status,
        whatsapp_numero=whatsapp_numero,
        whatsapp_erro=whatsapp_erro,
        logo_configurada=bool(timbrado.caminho_logo(current_app.config["UPLOAD_FOLDER"], empresa)),
        credenciais_tribunal_count=CredencialTribunal.query.filter_by(empresa_id=empresa.id).count(),
    )


@integracoes_bp.route("/minhas-integracoes")
@login_required
@apenas_admin
def minhas_integracoes():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    return render_template("integracoes/minhas_integracoes.html", **_contexto_minhas_integracoes())


@integracoes_bp.route("/minhas-integracoes/ia", methods=["POST"])
@login_required
@apenas_admin
def salvar_ia():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    provedor = request.form.get("provedor")
    if provedor not in (Empresa.PROVEDOR_IA_LOCAL, Empresa.PROVEDOR_IA_CLAUDE_BYOK, Empresa.PROVEDOR_IA_GEMINI_BYOK):
        flash("Selecione um provedor de IA válido.", "danger")
        return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    nova_chave = request.form.get("api_key", "").strip()
    modelo = request.form.get("modelo", "").strip()

    if provedor == Empresa.PROVEDOR_IA_CLAUDE_BYOK:
        if nova_chave:
            try:
                claude_api.validar_chave(nova_chave, modelo or None)
            except claude_api.ClaudeIndisponivelError as e:
                flash(f"Não foi possível validar a chave informada — nada foi salvo: {e}", "danger")
                return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
            try:
                empresa.agente_ia_claude_chave_cifrada = cofre.cifrar_segredo(nova_chave)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
        elif not empresa.agente_ia_claude_chave_cifrada:
            flash("Cadastre uma chave de API do Claude antes de ativar este provedor — gere uma em "
                  "https://console.anthropic.com/settings/keys.", "danger")
            return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
        empresa.agente_ia_claude_modelo = modelo or None

    if provedor == Empresa.PROVEDOR_IA_GEMINI_BYOK:
        if nova_chave:
            try:
                gemini_api.validar_chave(nova_chave, modelo or None)
            except gemini_api.GeminiIndisponivelError as e:
                flash(f"Não foi possível validar a chave informada — nada foi salvo: {e}", "danger")
                return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
            try:
                empresa.agente_ia_gemini_chave_cifrada = cofre.cifrar_segredo(nova_chave)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
        elif not empresa.agente_ia_gemini_chave_cifrada:
            flash("Cadastre uma chave de API do Gemini (de um projeto com faturamento ativo) antes de "
                  "ativar este provedor — gere uma em https://aistudio.google.com/apikey.", "danger")
            return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
        empresa.agente_ia_gemini_modelo = modelo or None

    empresa.agente_ia_provedor = provedor
    registrar_log(current_user, "configurou_agente_ia", "Empresa", empresa.id, provedor)
    db.session.commit()
    flash("Configuração do Agente de IA atualizada.", "success")
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


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
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/ia/remover-chave-gemini", methods=["POST"])
@login_required
@apenas_admin
def remover_chave_gemini():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    empresa.agente_ia_gemini_chave_cifrada = None
    empresa.agente_ia_provedor = Empresa.PROVEDOR_IA_LOCAL
    registrar_log(current_user, "removeu_chave_gemini", "Empresa", empresa.id)
    db.session.commit()
    flash("Chave da API do Gemini removida — o Agente de IA voltou a usar o modelo local gratuito.", "info")
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


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
        return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    nova_chave = request.form.get("api_key", "").strip()
    if provedor == Empresa.PROVEDOR_DATAJUD_CHAVE_PROPRIA:
        if nova_chave:
            try:
                empresa.datajud_chave_propria_cifrada = cofre.cifrar_segredo(nova_chave)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))
        elif not empresa.datajud_chave_propria_cifrada:
            flash("Cadastre sua chave própria do DataJud antes de ativar esta opção — cadastro "
                  "gratuito em https://datajud-wiki.cnj.jus.br/.", "danger")
            return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    empresa.datajud_provedor = provedor
    registrar_log(current_user, "configurou_datajud", "Empresa", empresa.id, provedor)
    db.session.commit()
    flash("Configuração de captura processual (DataJud) atualizada.", "success")
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


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
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


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
        return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    # Primeira conexão desta empresa: gera um nome de sessão próprio e
    # exclusivo dela (nunca reaproveita "default", que é da plataforma).
    # Reconexões (empresa que já tinha um nome de sessão salvo) reusam o
    # mesmo nome — o WAHA simplesmente gera um QR code novo pra ele.
    nome_sessao = empresa.whatsapp_sessao_efetiva or f"empresa-{empresa.id}"
    try:
        whatsapp.conectar_sessao(nome_sessao)
    except whatsapp.SessaoWhatsAppError as e:
        flash(f"Não foi possível iniciar a conexão com o WAHA: {e}", "danger")
        return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    if empresa.whatsapp_sessao != nome_sessao:
        empresa.whatsapp_sessao = nome_sessao
        registrar_log(current_user, "conectou_whatsapp", "Empresa", empresa.id, nome_sessao)
        db.session.commit()

    flash("Escaneie o QR code abaixo com o WhatsApp que a empresa vai usar pra enviar os lembretes.", "info")
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


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
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


# ---------------------- Timbrado do escritório (logo nos PDFs gerados) ----------------------
# A pedido explícito ("quero ter uma opção do advogado colocar o timbrado
# do escritório dele no sistema", PENDENCIAS.md seção -69). Vale pra
# empresa INTEIRA (todas as unidades) — ver decisão registrada em
# app/models/empresa.py::Empresa.logo_arquivo e app/utils/timbrado.py.

@integracoes_bp.route("/minhas-integracoes/timbrado", methods=["POST"])
@login_required
@apenas_admin
def salvar_timbrado():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    arquivo = request.files.get("logo")
    if not arquivo or arquivo.filename == "":
        flash("Selecione uma imagem (PNG ou JPG) para o timbrado.", "warning")
        return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    try:
        nome_salvo = timbrado.salvar_logo(current_app.config["UPLOAD_FOLDER"], empresa, arquivo)
    except timbrado.ArquivoLogoInvalido as e:
        flash(str(e), "danger")
        return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))

    empresa.logo_arquivo = nome_salvo
    registrar_log(current_user, "atualizou_timbrado", "Empresa", empresa.id)
    db.session.commit()
    flash("Timbrado atualizado — a logo já aparece nos próximos PDFs gerados (ex.: recibos).", "success")
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/timbrado/remover", methods=["POST"])
@login_required
@apenas_admin
def remover_timbrado():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    timbrado.remover_logo(current_app.config["UPLOAD_FOLDER"], empresa)
    empresa.logo_arquivo = None
    registrar_log(current_user, "removeu_timbrado", "Empresa", empresa.id)
    db.session.commit()
    flash("Timbrado removido — os PDFs gerados voltam a usar só o cabeçalho de texto.", "info")
    return redirect(request.referrer or url_for("integracoes.minhas_integracoes"))


@integracoes_bp.route("/minhas-integracoes/timbrado/imagem")
@login_required
@apenas_admin
def imagem_timbrado():
    """Serve a imagem da logo cadastrada, pra pré-visualização na própria
    tela de configuração — mesmo padrão de `qr_whatsapp` acima (backend lê
    e repassa os bytes em vez de expor um caminho de disco pro navegador)."""
    empresa = _empresa_atual()
    if empresa is None:
        abort(403)
    caminho = timbrado.caminho_logo(current_app.config["UPLOAD_FOLDER"], empresa)
    if not caminho:
        abort(404)
    ext = caminho.rsplit(".", 1)[-1].lower()
    mimetype = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
    with open(caminho, "rb") as f:
        conteudo = f.read()
    return Response(conteudo, mimetype=mimetype, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Credenciais de tribunal (item 2 — PENDENCIAS.md, seção -108): "Download dos
# autos — Sessão autenticada do escritório no PJe, eproc, Projudi e ESAJ, com
# certificado ou credencial guardada no cofre já existente." Ver aviso
# completo em app/models/credencial_tribunal.py sobre o escopo desta rodada
# (SÓ o cofre — nenhum conector de login/download automatizado ainda).
# Empresa inteira (não por unidade), lista (várias credenciais por
# empresa — uma por sistema+tribunal), senha nunca volta em texto puro pra
# tela nenhuma depois de cadastrada (só "tem credencial: sim/não").
# ---------------------------------------------------------------------------

@integracoes_bp.route("/minhas-integracoes/credenciais-tribunal")
@login_required
@apenas_admin
def credenciais_tribunal():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    credenciais = (CredencialTribunal.query.filter_by(empresa_id=empresa.id)
                   .order_by(CredencialTribunal.sistema, CredencialTribunal.tribunal).all())
    return render_template("integracoes/credenciais_tribunal.html", credenciais=credenciais,
                            sistemas=CredencialTribunal.SISTEMAS)


@integracoes_bp.route("/minhas-integracoes/credenciais-tribunal/nova", methods=["GET", "POST"])
@login_required
@apenas_admin
def nova_credencial_tribunal():
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        sistema = request.form.get("sistema", "").strip()
        tribunal = request.form.get("tribunal", "").strip().upper()
        usuario_login = request.form.get("usuario_login", "").strip()
        senha = request.form.get("senha", "")
        observacao = request.form.get("observacao", "").strip() or None

        if sistema not in CredencialTribunal.SISTEMAS:
            flash("Selecione um sistema de tribunal válido.", "danger")
            return redirect(url_for("integracoes.nova_credencial_tribunal"))
        if not tribunal or not usuario_login:
            flash("Informe o tribunal (ex.: TJSP, TRF3) e o usuário/login.", "danger")
            return redirect(url_for("integracoes.nova_credencial_tribunal"))

        senha_cifrada = None
        if senha:
            try:
                senha_cifrada = cofre.cifrar_segredo(senha)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(url_for("integracoes.nova_credencial_tribunal"))

        credencial = CredencialTribunal(
            empresa_id=empresa.id, sistema=sistema, tribunal=tribunal, usuario_login=usuario_login,
            senha_cifrada=senha_cifrada, observacao=observacao, ativo=True, criado_por_id=current_user.id,
        )
        db.session.add(credencial)
        registrar_log(current_user, "cadastrou_credencial_tribunal", "CredencialTribunal", None,
                       f"{sistema}/{tribunal}")
        db.session.commit()
        flash("Credencial cadastrada — guardada cifrada no cofre; nunca é exibida em texto puro de novo.",
              "success")
        return redirect(url_for("integracoes.credenciais_tribunal"))

    return render_template("integracoes/credencial_tribunal_form.html", credencial=None,
                            sistemas=CredencialTribunal.SISTEMAS)


@integracoes_bp.route("/minhas-integracoes/credenciais-tribunal/<int:credencial_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_credencial_tribunal(credencial_id):
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    credencial = db.get_or_404(CredencialTribunal, credencial_id)
    if credencial.empresa_id != empresa.id:
        abort(404)

    if request.method == "POST":
        sistema = request.form.get("sistema", "").strip()
        tribunal = request.form.get("tribunal", "").strip().upper()
        usuario_login = request.form.get("usuario_login", "").strip()
        senha = request.form.get("senha", "")
        observacao = request.form.get("observacao", "").strip() or None

        if sistema not in CredencialTribunal.SISTEMAS:
            flash("Selecione um sistema de tribunal válido.", "danger")
            return redirect(url_for("integracoes.editar_credencial_tribunal", credencial_id=credencial.id))
        if not tribunal or not usuario_login:
            flash("Informe o tribunal (ex.: TJSP, TRF3) e o usuário/login.", "danger")
            return redirect(url_for("integracoes.editar_credencial_tribunal", credencial_id=credencial.id))

        # Senha em branco no formulário de edição = mantém a já cadastrada
        # (mesmo padrão de salvar_ia/salvar_datajud acima) — nunca apaga a
        # senha por acidente só porque o campo veio vazio na tela.
        if senha:
            try:
                credencial.senha_cifrada = cofre.cifrar_segredo(senha)
            except cofre.CofreNaoConfiguradoError as e:
                flash(str(e), "danger")
                return redirect(url_for("integracoes.editar_credencial_tribunal", credencial_id=credencial.id))

        credencial.sistema = sistema
        credencial.tribunal = tribunal
        credencial.usuario_login = usuario_login
        credencial.observacao = observacao
        registrar_log(current_user, "editou_credencial_tribunal", "CredencialTribunal", credencial.id,
                       f"{sistema}/{tribunal}")
        db.session.commit()
        flash("Credencial atualizada.", "success")
        return redirect(url_for("integracoes.credenciais_tribunal"))

    return render_template("integracoes/credencial_tribunal_form.html", credencial=credencial,
                            sistemas=CredencialTribunal.SISTEMAS)


@integracoes_bp.route("/minhas-integracoes/credenciais-tribunal/<int:credencial_id>/alternar-ativo",
                       methods=["POST"])
@login_required
@apenas_admin
def alternar_credencial_tribunal(credencial_id):
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    credencial = db.get_or_404(CredencialTribunal, credencial_id)
    if credencial.empresa_id != empresa.id:
        abort(404)
    credencial.ativo = not credencial.ativo
    registrar_log(current_user, "ativou_credencial_tribunal" if credencial.ativo else "desativou_credencial_tribunal",
                   "CredencialTribunal", credencial.id, f"{credencial.sistema}/{credencial.tribunal}")
    db.session.commit()
    flash(f"Credencial {'ativada' if credencial.ativo else 'desativada'}.", "info")
    return redirect(url_for("integracoes.credenciais_tribunal"))


@integracoes_bp.route("/minhas-integracoes/credenciais-tribunal/<int:credencial_id>/excluir", methods=["POST"])
@login_required
@apenas_admin
def excluir_credencial_tribunal(credencial_id):
    empresa = _empresa_atual()
    if empresa is None:
        return redirect(url_for("dashboard.index"))
    credencial = db.get_or_404(CredencialTribunal, credencial_id)
    if credencial.empresa_id != empresa.id:
        abort(404)
    rotulo = f"{credencial.sistema}/{credencial.tribunal}"
    db.session.delete(credencial)
    registrar_log(current_user, "excluiu_credencial_tribunal", "CredencialTribunal", credencial_id, rotulo)
    db.session.commit()
    flash("Credencial excluída.", "info")
    return redirect(url_for("integracoes.credenciais_tribunal"))
