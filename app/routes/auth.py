from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from app.extensions import db
from app.models import Usuario, Empresa, Unidade, Licenca, ConfiguracaoPlataforma
from app.utils.notificacoes import registrar_log
from app.utils import totp as totp_utils
from app.utils import senha_redefinicao
from app.utils.senha_politica import validar_forca_senha, REQUISITOS_TEXTO
from app.utils.email import smtp_configurado

auth_bp = Blueprint("auth", __name__)

# Chaves de sessão usadas pelas duas etapas de login (segundo fator) — ver
# `auth.verificar_totp` logo abaixo. Deliberadamente NÃO chamamos
# `login_user()` até o código bater: até lá, a pessoa provou só a senha,
# não a posse do segundo fator, então a sessão do Flask-Login continua
# anônima (ver PENDENCIAS.md, seção -104).
_SESSAO_TOTP_USUARIO_ID = "totp_pendente_usuario_id"
_SESSAO_TOTP_LEMBRAR = "totp_pendente_lembrar"
_SESSAO_TOTP_NEXT = "totp_pendente_next"
_SESSAO_TOTP_TENTATIVAS = "totp_pendente_tentativas"
_MAX_TENTATIVAS_TOTP_LOGIN = 5

# Chaves de sessão do fluxo "esqueci minha senha" — ver as rotas
# `esqueci_senha*` mais abaixo.
_SESSAO_RESET_EMAIL = "reset_senha_email"
_SESSAO_RESET_VERIFICADO = "reset_senha_verificado"


def _limpar_sessao_totp_pendente():
    for chave in (_SESSAO_TOTP_USUARIO_ID, _SESSAO_TOTP_LEMBRAR, _SESSAO_TOTP_NEXT, _SESSAO_TOTP_TENTATIVAS):
        session.pop(chave, None)


def _limpar_sessao_reset_senha():
    for chave in (_SESSAO_RESET_EMAIL, _SESSAO_RESET_VERIFICADO):
        session.pop(chave, None)


def _concluir_login(usuario, lembrar, proximo):
    """Único ponto que chama `login_user` de verdade — usado tanto pelo
    login direto (sem segundo fator, ou segundo fator ainda não
    configurado) quanto por `verificar_totp` (segundo fator confirmado).
    Nunca duplicado, pra garantir que `ultimo_login`/log de auditoria
    sempre acontecem do mesmo jeito nos dois caminhos."""
    login_user(usuario, remember=lembrar)
    usuario.ultimo_login = datetime.utcnow()
    registrar_log(usuario, "login", "Usuario", usuario.id)
    db.session.commit()
    return redirect(proximo or url_for("dashboard.index"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        lembrar = bool(request.form.get("lembrar"))

        usuario = Usuario.query.filter_by(email=email).first()

        if usuario and usuario.ativo and usuario.checar_senha(senha):
            proximo = request.args.get("next")

            # Segundo fator obrigatório (PENDENCIAS.md, seção -104): só
            # exige o código de quem JÁ confirmou o autenticador alguma
            # vez — quem ainda não configurou loga normal (só senha) e cai
            # direto na tela de configuração obrigatória (ver o gate em
            # app/__init__.py::exigir_autenticador_configurado). Se a
            # funcionalidade inteira estiver desligada neste deploy
            # (COFRE_SENHA_PROCESSO_KEY não configurada), nunca pede nada
            # — ver docstring de app/utils/totp.py.
            if totp_utils.totp_disponivel() and usuario.totp_configurado:
                session[_SESSAO_TOTP_USUARIO_ID] = usuario.id
                session[_SESSAO_TOTP_LEMBRAR] = lembrar
                session[_SESSAO_TOTP_NEXT] = proximo or ""
                session.pop(_SESSAO_TOTP_TENTATIVAS, None)
                return redirect(url_for("auth.verificar_totp"))

            return _concluir_login(usuario, lembrar, proximo)

        flash("E-mail ou senha inválidos, ou usuário inativo.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/verificar-autenticador", methods=["GET", "POST"])
def verificar_totp():
    """Segunda etapa do login (código do app autenticador) — só chega
    aqui quem já acertou a senha (ver `login` acima); a sessão ainda não
    está autenticada de verdade (Flask-Login não viu `login_user` ainda),
    só "pendente" via `session[_SESSAO_TOTP_USUARIO_ID]`."""
    usuario_id = session.get(_SESSAO_TOTP_USUARIO_ID)
    if not usuario_id:
        return redirect(url_for("auth.login"))

    usuario = db.session.get(Usuario, usuario_id)
    if not usuario or not usuario.ativo or not usuario.totp_configurado:
        _limpar_sessao_totp_pendente()
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        codigo = request.form.get("codigo", "")
        secret = totp_utils.obter_secret_pendente_ou_confirmado(usuario)

        if secret and totp_utils.verificar_codigo(secret, codigo):
            lembrar = bool(session.get(_SESSAO_TOTP_LEMBRAR))
            proximo = session.get(_SESSAO_TOTP_NEXT) or None
            _limpar_sessao_totp_pendente()
            return _concluir_login(usuario, lembrar, proximo)

        tentativas = session.get(_SESSAO_TOTP_TENTATIVAS, 0) + 1
        if tentativas >= _MAX_TENTATIVAS_TOTP_LOGIN:
            _limpar_sessao_totp_pendente()
            flash("Muitas tentativas com código incorreto — faça login novamente.", "danger")
            return redirect(url_for("auth.login"))

        session[_SESSAO_TOTP_TENTATIVAS] = tentativas
        flash("Código inválido — confira o app autenticador (o código muda a cada 30 segundos) e "
              "tente de novo.", "danger")

    return render_template("auth/verificar_totp.html", usuario_email=usuario.email)


@auth_bp.route("/verificar-autenticador/cancelar")
def cancelar_verificacao_totp():
    _limpar_sessao_totp_pendente()
    flash("Login cancelado.", "info")
    return redirect(url_for("auth.login"))


# ---------------------- "Esqueci minha senha" ----------------------
# Fluxo em 3 passos, cada um sua própria tela: (1) informar o e-mail —
# nunca revela se o e-mail existe ou não, sempre a mesma mensagem
# genérica (ver docstring de app/utils/senha_redefinicao.py); (2) digitar
# o código de 6 dígitos recebido por e-mail; (3) só depois do código
# confirmado, escolher a nova senha (+ confirmação), com a política de
# força exigida (mín. 8 caracteres, maiúscula, minúscula, número e
# caractere especial).

@auth_bp.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        # Diferente da checagem de e-mail existente ou não (que é sempre
        # escondida do usuário, ver acima): SMTP não configurado é um
        # problema do SISTEMA, não desta conta — não vaza nada sobre
        # nenhum e-mail específico dizer isso abertamente, e é bem mais
        # honesto do que prometer um código que nunca vai chegar pra
        # ninguém (mesmo padrão de "degrada honestamente" usado em toda
        # outra integração externa deste projeto).
        if not smtp_configurado():
            flash("O envio de e-mail não está configurado neste sistema no momento — peça para um "
                  "administrador do seu escritório redefinir sua senha por você.", "danger")
            return render_template("auth/esqueci_senha.html")

        if email:
            usuario = Usuario.query.filter_by(email=email, ativo=True).first()
            if usuario:
                senha_redefinicao.gerar_e_enviar_codigo(usuario)
                db.session.commit()

        session[_SESSAO_RESET_EMAIL] = email
        session.pop(_SESSAO_RESET_VERIFICADO, None)
        flash("Se este e-mail estiver cadastrado e ativo, enviamos um código de verificação — "
              "confira sua caixa de entrada (e o spam).", "info")
        return redirect(url_for("auth.esqueci_senha_verificar"))

    return render_template("auth/esqueci_senha.html")


@auth_bp.route("/esqueci-senha/verificar", methods=["GET", "POST"])
def esqueci_senha_verificar():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    email = session.get(_SESSAO_RESET_EMAIL)
    if not email:
        return redirect(url_for("auth.esqueci_senha"))

    if request.method == "POST":
        codigo = request.form.get("codigo", "")
        usuario = Usuario.query.filter_by(email=email, ativo=True).first()
        ok, erro = senha_redefinicao.validar_codigo(usuario, codigo)
        if usuario:
            db.session.commit()  # persiste o incremento de tentativa mesmo quando erra

        if ok:
            session[_SESSAO_RESET_VERIFICADO] = True
            return redirect(url_for("auth.esqueci_senha_nova_senha"))

        # Mesma mensagem genérica tanto para "e-mail nunca existiu" quanto
        # para "código errado/expirado" — nunca diferencia os dois casos.
        flash(erro or "Código inválido.", "danger")

    return render_template("auth/esqueci_senha_verificar.html", email=email)


@auth_bp.route("/esqueci-senha/reenviar", methods=["POST"])
def esqueci_senha_reenviar():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    email = session.get(_SESSAO_RESET_EMAIL)
    if not email:
        return redirect(url_for("auth.esqueci_senha"))

    if smtp_configurado():
        usuario = Usuario.query.filter_by(email=email, ativo=True).first()
        if usuario:
            senha_redefinicao.gerar_e_enviar_codigo(usuario)
            db.session.commit()
        flash("Se este e-mail estiver cadastrado e ativo, reenviamos um novo código.", "info")
    else:
        flash("O envio de e-mail não está configurado neste sistema no momento — peça para um "
              "administrador do seu escritório redefinir sua senha por você.", "danger")

    return redirect(url_for("auth.esqueci_senha_verificar"))


@auth_bp.route("/esqueci-senha/nova-senha", methods=["GET", "POST"])
def esqueci_senha_nova_senha():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    email = session.get(_SESSAO_RESET_EMAIL)
    if not email or not session.get(_SESSAO_RESET_VERIFICADO):
        flash("Sua sessão de redefinição de senha expirou ou ainda não foi verificada — comece de novo.",
              "warning")
        return redirect(url_for("auth.esqueci_senha"))

    if request.method == "POST":
        nova_senha = request.form.get("nova_senha", "")
        confirmar_senha = request.form.get("confirmar_senha", "")

        erros = validar_forca_senha(nova_senha)
        if nova_senha != confirmar_senha:
            erros.append("A confirmação não é igual à nova senha.")

        if erros:
            for e in erros:
                flash(e, "danger")
            return render_template("auth/esqueci_senha_nova_senha.html", requisitos=REQUISITOS_TEXTO)

        usuario = Usuario.query.filter_by(email=email, ativo=True).first()
        if not usuario:
            # Não deveria acontecer (só chega aqui com _SESSAO_RESET_VERIFICADO
            # já confirmado, o que exige um Usuario válido) — mas nunca
            # confia cegamente no estado da sessão pra escrever no banco.
            _limpar_sessao_reset_senha()
            flash("Não foi possível concluir a redefinição — comece de novo.", "danger")
            return redirect(url_for("auth.esqueci_senha"))

        usuario.set_senha(nova_senha)
        senha_redefinicao.limpar_codigo(usuario)
        registrar_log(usuario, "redefiniu_senha", "Usuario", usuario.id)
        db.session.commit()

        _limpar_sessao_reset_senha()
        flash("Senha redefinida com sucesso — faça login com a sua nova senha.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/esqueci_senha_nova_senha.html", requisitos=REQUISITOS_TEXTO)


@auth_bp.route("/cadastrar-empresa", methods=["GET", "POST"])
def cadastrar_empresa():
    """
    Cadastro público (self-service): qualquer visitante pode criar sua
    própria empresa cliente, com a primeira unidade e o primeiro admin
    dessa empresa, e já sai com uma licença pendente de pagamento pronta
    para pagar via Mercado Pago.
    """
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    # Preço de tabela gerenciável pelo admin desenvolvedor em
    # /plataforma/planos (ver app/models/configuracao.py) — cai pro valor
    # de config.py/.env só enquanto ninguém salvou nada por essa tela ainda.
    precos = ConfiguracaoPlataforma.obter().como_dict_precos()

    if request.method == "POST":
        nome_empresa = request.form.get("empresa_nome", "").strip()
        nome_admin = request.form.get("admin_nome", "").strip()
        email_admin = request.form.get("admin_email", "").strip().lower()
        senha = request.form.get("admin_senha", "")
        plano = request.form.get("plano", "mensal")

        erros = []
        if not nome_empresa:
            erros.append("Informe o nome da empresa.")
        if not nome_admin or not email_admin:
            erros.append("Informe seu nome e e-mail.")
        if len(senha) < 6:
            erros.append("A senha precisa ter pelo menos 6 caracteres.")
        if plano not in Licenca.PLANOS:
            erros.append("Plano inválido.")
        if Usuario.query.filter_by(email=email_admin).first():
            erros.append("Já existe uma conta com este e-mail.")

        if erros:
            for e in erros:
                flash(e, "danger")
            return render_template("auth/cadastro_empresa.html", precos=precos, form=request.form)

        empresa = Empresa(nome=nome_empresa, cnpj=request.form.get("empresa_cnpj") or None,
                           email_contato=email_admin, dono_da_plataforma=False)
        db.session.add(empresa)
        db.session.flush()

        unidade = Unidade(empresa_id=empresa.id, nome="Matriz", codigo=f"EMP{empresa.id}-01")
        db.session.add(unidade)
        db.session.flush()

        admin = Usuario(nome=nome_admin, email=email_admin, papel="admin", unidade_id=unidade.id)
        admin.set_senha(senha)
        db.session.add(admin)
        db.session.flush()

        licenca = Licenca(
            empresa_id=empresa.id, plano=plano, valor_negociado=precos[plano],
            status="pendente_pagamento",
        )
        db.session.add(licenca)

        registrar_log(admin, "cadastro_self_service", "Empresa", empresa.id, empresa.nome)
        db.session.commit()

        login_user(admin)
        flash(f"Empresa \"{nome_empresa}\" cadastrada! Falta só ativar sua licença para começar a usar.", "success")

        # Autenticador obrigatório (PENDENCIAS.md, seção -104): a conta
        # nova já sai direto pra tela de configurar o QR code, antes de
        # qualquer outra coisa — "o usuário deve escanear o qrcode do
        # autenticador assim que estiver criando sua conta". Quando a
        # funcionalidade está desligada neste deploy (cofre não
        # configurado — ver app/utils/totp.py), segue pro fluxo de sempre.
        if totp_utils.totp_disponivel():
            return redirect(url_for("conta.configurar_totp"))
        return redirect(url_for("licenciamento.minha_licenca"))

    return render_template("auth/cadastro_empresa.html", precos=precos, form={})


@auth_bp.route("/logout")
@login_required
def logout():
    registrar_log(current_user, "logout", "Usuario", current_user.id)
    db.session.commit()
    logout_user()
    _limpar_sessao_totp_pendente()
    _limpar_sessao_reset_senha()
    flash("Sessão encerrada com sucesso.", "info")
    return redirect(url_for("auth.login"))
