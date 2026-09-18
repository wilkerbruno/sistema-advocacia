"""
Tela "Meu agente local" — cada advogado gera e revoga, para si mesmo,
os pareamentos do Agente Local (agente_local_jc/, roda na própria
máquina do advogado — ver app/models/agente_local.py e PENDENCIAS.md
seção -56) usados para buscar autos completos de processos com o
próprio certificado digital, sem que o certificado nunca chegue ao
servidor da JusControl.

Pessoal, não administrativo: qualquer usuário logado (não só admin/
gestor) pode gerenciar o(s) próprio(s) pareamento(s) — o certificado
digital é do advogado, não do escritório, então só ele decide instalar
o agente e em qual máquina.
"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, Response, session
from flask_login import login_required, current_user

from app.extensions import db
from app.models import AgenteLocalPareado
from app.utils.notificacoes import registrar_log
from app.utils import instalador_agente_local

agente_local_bp = Blueprint("agente_local", __name__)


def _pareamentos_do_usuario():
    return (
        AgenteLocalPareado.query.filter_by(usuario_id=current_user.id)
        .order_by(AgenteLocalPareado.criado_em.desc()).all()
    )


def _instalador_disponivel():
    """
    True se existe ALGUMA forma configurada de servir o instalador — via
    repositório privado (proxy autenticado, ver
    instalador_agente_local.py) ou via link direto (repositório
    público). O template só precisa saber "mostrar o botão ou não";
    quem decide COMO baixar é a rota `baixar_instalador` abaixo.
    """
    cfg = current_app.config
    repo_privado = bool(cfg.get("AGENTE_LOCAL_GITHUB_REPO") and cfg.get("AGENTE_LOCAL_GITHUB_TOKEN"))
    link_publico = bool(cfg.get("AGENTE_LOCAL_INSTALADOR_URL"))
    return repo_privado or link_publico


@agente_local_bp.route("/agente-local")
@login_required
def meu_agente():
    return render_template(
        "agente_local/meu_agente.html", pareamentos=_pareamentos_do_usuario(),
        token_novo=session.pop("agente_local_token_novo", None),
        instalador_disponivel=_instalador_disponivel(),
    )


@agente_local_bp.route("/agente-local/baixar")
@login_required
def baixar_instalador():
    """
    Entrega o instalador de duas formas possíveis (ver config.py):

    1) Repositório PRIVADO (AGENTE_LOCAL_GITHUB_REPO + _TOKEN definidos):
       busca o instalador na API do GitHub usando o token (só o servidor
       vê esse token — nunca o navegador do advogado) e entrega os bytes
       direto por aqui, sem o advogado nunca acessar o GitHub.
    2) Repositório PÚBLICO (só AGENTE_LOCAL_INSTALADOR_URL definida):
       redireciona pro link direto nas Releases do GitHub.

    Fica numa rota própria (em vez de um link direto no template) pra
    poder trocar a forma de entrega sem reeditar HTML, e pra logar quem
    baixou — útil pra saber se um advogado começou a instalar mas não
    chegou a parear (ver PENDENCIAS.md seções -57/-58).
    """
    repo = current_app.config.get("AGENTE_LOCAL_GITHUB_REPO")
    token = current_app.config.get("AGENTE_LOCAL_GITHUB_TOKEN")

    if repo and token:
        try:
            asset_id, nome_arquivo = instalador_agente_local.localizar_asset_da_ultima_release(repo, token)
            conteudo, content_type = instalador_agente_local.baixar_bytes_do_asset(repo, token, asset_id)
        except instalador_agente_local.InstaladorIndisponivelError as e:
            flash(str(e), "danger")
            return redirect(url_for("agente_local.meu_agente"))

        registrar_log(current_user, "baixou_instalador_agente_local", "AgenteLocalPareado", None,
                       f"{repo} (proxy privado)")
        db.session.commit()
        return Response(
            conteudo, mimetype=content_type or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{nome_arquivo}"'},
        )

    url_direta = current_app.config.get("AGENTE_LOCAL_INSTALADOR_URL")
    if url_direta:
        registrar_log(current_user, "baixou_instalador_agente_local", "AgenteLocalPareado", None, url_direta)
        db.session.commit()
        return redirect(url_direta)

    flash("O instalador ainda não está publicado — veja agente_local_jc/README.md "
          "para rodar manualmente enquanto isso.", "warning")
    return redirect(url_for("agente_local.meu_agente"))


@agente_local_bp.route("/agente-local/parear", methods=["POST"])
@login_required
def parear():
    apelido = request.form.get("apelido", "").strip() or "Agente local"
    registro, valor_puro = AgenteLocalPareado.emitir_para(current_user, apelido)
    registrar_log(current_user, "pareou_agente_local", "AgenteLocalPareado", None, apelido)
    db.session.commit()

    flash("Agente pareado — copie o token abaixo agora, ele não vai aparecer de novo.", "success")
    # Guarda o token na SESSION (não na URL nem num redirect com query
    # string, pra nunca sobrar num histórico de navegador/log de acesso) e
    # redireciona de volta pra onde o clique veio — a tela solta OU a aba
    # "Meu agente local" do hub "Minha conta" (ver app/routes/conta.py::hub)
    # — em vez de sempre renderizar a tela solta direto, que tirava quem
    # pareou de dentro do hub. `meu_agente()`/`conta.hub()` leem e REMOVEM
    # (session.pop) esse valor na próxima renderização, então só aparece
    # nesta primeira vez, igual sempre funcionou.
    session["agente_local_token_novo"] = valor_puro
    return redirect(request.referrer or url_for("agente_local.meu_agente"))


@agente_local_bp.route("/agente-local/<int:pareamento_id>/revogar", methods=["POST"])
@login_required
def revogar(pareamento_id):
    registro = db.get_or_404(AgenteLocalPareado, pareamento_id)
    if registro.usuario_id != current_user.id:
        flash("Você só pode revogar os próprios pareamentos.", "danger")
        return redirect(url_for("agente_local.meu_agente"))

    registro.revogar()
    registrar_log(current_user, "revogou_agente_local", "AgenteLocalPareado", registro.id, registro.apelido)
    db.session.commit()
    flash(f"Pareamento “{registro.apelido}” revogado — esse agente não vai mais conseguir buscar autos.", "info")
    return redirect(url_for("agente_local.meu_agente"))
