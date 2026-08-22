import io
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, send_file, current_app
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Cliente, Unidade
from app.utils.acesso import aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403, unidades_do_escopo, usuarios_do_escopo, apenas_admin
from app.utils.notificacoes import registrar_log
from app.utils.conflito_interesse import conflitos_para_cliente
from app.utils.lgpd import montar_export_dados_cliente, montar_pacote_completo_cliente, anonimizar_cliente
from app.utils.paginacao import paginar
from app.utils.captura_conectores import obter_conector, ConectorNaoConfiguradoError

clientes_bp = Blueprint("clientes", __name__)


def _parse_valor_hora(valor):
    # Campo opcional (ver PENDENCIAS.md, seção -39) — só pré-preenche uma
    # SUGESTÃO de valor em "Gerar cobrança a partir de horas"; nunca é
    # obrigatório e um valor inválido/vazio simplesmente vira None em vez
    # de quebrar o cadastro do cliente.
    if not valor:
        return None
    try:
        return Decimal(str(valor).replace(",", "."))
    except InvalidOperation:
        return None


def _parse_data_consentimento(valor):
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%Y-%m-%d").date()
    except ValueError:
        return None


@clientes_bp.route("/")
@login_required
def listar():
    termo = request.args.get("q", "").strip()
    query = aplicar_escopo_unidade(Cliente.query, Cliente)
    if termo:
        like = f"%{termo}%"
        query = query.filter(db.or_(Cliente.nome.ilike(like), Cliente.cpf_cnpj.ilike(like)))
    # Paginação (PENDENCIAS.md, seção -47) — mesmo motivo de Processos.
    paginacao = paginar(query.order_by(Cliente.nome))
    return render_template("clientes/listar.html", clientes=paginacao.items,
                            paginacao=paginacao, termo=termo)


@clientes_bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    unidades = unidades_do_escopo() if current_user.is_admin else None

    if request.method == "POST":
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        cliente = Cliente(
            tipo_pessoa=request.form.get("tipo_pessoa", "PF"),
            nome=request.form["nome"],
            cpf_cnpj=request.form.get("cpf_cnpj"),
            rg_ie=request.form.get("rg_ie"),
            email=request.form.get("email"),
            telefone=request.form.get("telefone"),
            whatsapp=request.form.get("whatsapp"),
            endereco=request.form.get("endereco"),
            cidade=request.form.get("cidade"),
            estado=request.form.get("estado"),
            cep=request.form.get("cep"),
            observacoes=request.form.get("observacoes"),
            valor_hora_padrao=_parse_valor_hora(request.form.get("valor_hora_padrao")),
            base_legal_tratamento=request.form.get("base_legal_tratamento") or None,
            consentimento_obtido_em=_parse_data_consentimento(request.form.get("consentimento_obtido_em")),
            consentimento_observacoes=request.form.get("consentimento_observacoes") or None,
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
        )
        db.session.add(cliente)
        db.session.flush()
        registrar_log(current_user, "criou", "Cliente", cliente.id, cliente.nome)
        db.session.commit()
        flash("Cliente cadastrado com sucesso.", "success")

        # Verificação de conflito de interesses (PENDENCIAS.md, seção -42):
        # avisa já no cadastro se este cliente já aparece como parte
        # contrária em outro caso do escritório. Nunca bloqueia — só avisa
        # (fica também visível permanentemente no detalhe do cliente).
        empresa = db.session.get(Unidade, unidade_id).empresa
        conflitos = conflitos_para_cliente(empresa.id if empresa else None, cliente.nome, cliente_id=cliente.id)
        if conflitos:
            numeros = ", ".join(p.numero_processo or p.numero_interno or f"#{p.id}" for p in conflitos)
            flash(f"⚠️ Possível conflito de interesses: {cliente.nome} já aparece como parte contrária "
                  f"em outro processo do escritório ({numeros}). Revise antes de prosseguir.", "danger")

        return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))

    return render_template("clientes/form.html", cliente=None, unidades=unidades)


@clientes_bp.route("/<int:cliente_id>")
@login_required
def detalhe(cliente_id):
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)

    # Verificação de conflito de interesses (PENDENCIAS.md, seção -42):
    # este cliente aparece como parte contrária em algum outro processo do
    # escritório (qualquer unidade da mesma empresa)? Checagem ao vivo, não
    # fica salva em lugar nenhum — sempre reflete o cadastro atual.
    conflitos_interesse = conflitos_para_cliente(
        cliente.unidade.empresa_id if cliente.unidade else None,
        cliente.nome, cliente_id=cliente.id,
    )

    return render_template("clientes/detalhe.html", cliente=cliente, conflitos_interesse=conflitos_interesse)


@clientes_bp.route("/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
def editar(cliente_id):
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)
    unidades = unidades_do_escopo() if current_user.is_admin else None

    if request.method == "POST":
        cliente.tipo_pessoa = request.form.get("tipo_pessoa", "PF")
        cliente.nome = request.form["nome"]
        cliente.cpf_cnpj = request.form.get("cpf_cnpj")
        cliente.rg_ie = request.form.get("rg_ie")
        cliente.email = request.form.get("email")
        cliente.telefone = request.form.get("telefone")
        cliente.whatsapp = request.form.get("whatsapp")
        cliente.endereco = request.form.get("endereco")
        cliente.cidade = request.form.get("cidade")
        cliente.estado = request.form.get("estado")
        cliente.cep = request.form.get("cep")
        cliente.observacoes = request.form.get("observacoes")
        cliente.valor_hora_padrao = _parse_valor_hora(request.form.get("valor_hora_padrao"))
        cliente.base_legal_tratamento = request.form.get("base_legal_tratamento") or None
        cliente.consentimento_obtido_em = _parse_data_consentimento(request.form.get("consentimento_obtido_em"))
        cliente.consentimento_observacoes = request.form.get("consentimento_observacoes") or None
        if current_user.is_admin and request.form.get("unidade_id"):
            cliente.unidade_id = int(request.form["unidade_id"])

        registrar_log(current_user, "editou", "Cliente", cliente.id, cliente.nome)
        db.session.commit()
        flash("Cliente atualizado com sucesso.", "success")
        return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))

    return render_template("clientes/form.html", cliente=cliente, unidades=unidades)


@clientes_bp.route("/<int:cliente_id>/inativar", methods=["POST"])
@login_required
def inativar(cliente_id):
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)
    cliente.ativo = not cliente.ativo
    registrar_log(current_user, "alterou_status", "Cliente", cliente.id,
                  f"ativo={cliente.ativo}")
    db.session.commit()
    flash("Status do cliente atualizado.", "info")
    return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))


@clientes_bp.route("/<int:cliente_id>/exportar-dados-lgpd")
@login_required
def exportar_dados_lgpd(cliente_id):
    """
    Portabilidade de dados (LGPD art. 18 V, PENDENCIAS.md seção -43): baixa
    tudo que o sistema guarda sobre este cliente (cadastro, processos,
    lançamentos financeiros, apontamentos de hora, compromissos de agenda)
    num JSON estruturado — formato padrão pra atender solicitação de
    titular de dados. Ver limitação de escopo documentada em
    app/utils/lgpd.py (não varre texto livre do sistema inteiro).
    """
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)

    dados = montar_export_dados_cliente(cliente)
    conteudo = json.dumps(dados, ensure_ascii=False, indent=2).encode("utf-8")

    registrar_log(current_user, "exportou_dados_lgpd", "Cliente", cliente.id, cliente.nome)
    db.session.commit()

    buffer = io.BytesIO(conteudo)
    nome_arquivo = f"dados_lgpd_cliente_{cliente.id}.json"
    return send_file(buffer, mimetype="application/json", as_attachment=True, download_name=nome_arquivo)


@clientes_bp.route("/<int:cliente_id>/exportar-pacote-completo")
@login_required
def exportar_pacote_completo(cliente_id):
    """
    Exportação em massa de dados de um cliente (PENDENCIAS.md, seção
    -52) — complementa a exportação LGPD acima, que só devolve os
    metadados estruturados: este pacote é um .zip com esse mesmo JSON
    MAIS os arquivos de documento de verdade (contrato, procuração,
    petição, decisão...) anexados a cada processo do cliente, tudo num
    download só.
    """
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)

    buffer, documentos_incluidos, avisos = montar_pacote_completo_cliente(
        cliente, current_app.config["UPLOAD_FOLDER"]
    )

    detalhe_log = f"{cliente.nome} — {len(documentos_incluidos)} documento(s) incluído(s)"
    if avisos:
        detalhe_log += f", {len(avisos)} arquivo(s) ausente(s) no armazenamento"
    registrar_log(current_user, "exportou_pacote_completo_cliente", "Cliente", cliente.id, detalhe_log)
    # Auditoria de acesso a documentos (PENDENCIAS.md, seção -51): um
    # documento incluído no pacote foi, na prática, baixado — registra
    # cada um também como "baixou_documento", pro histórico de acesso de
    # CADA documento (Processo → Documentos → "Histórico") não ficar cego
    # pra esta forma de download em lote.
    for doc in documentos_incluidos:
        registrar_log(current_user, "baixou_documento", "Documento", doc.id, doc.nome_original)
    db.session.commit()

    nome_arquivo = f"pacote_completo_cliente_{cliente.id}.zip"
    return send_file(buffer, mimetype="application/zip", as_attachment=True, download_name=nome_arquivo)


@clientes_bp.route("/<int:cliente_id>/due-diligence")
@login_required
def due_diligence(cliente_id):
    """
    Due diligence de cliente novo (PENDENCIAS.md, seção -53): busca TODO
    processo no Brasil em que este cliente apareça como parte, não só nos
    processos já cadastrados neste escritório — diferente da verificação
    de conflito de interesses (app/utils/conflito_interesse.py), que só
    cruza contra os clientes já cadastrados no próprio sistema.

    Exige um provedor pago (Judit/Escavador/Digesto/Codilo/Jusbrasil
    Soluções — nenhum contratado ainda, ver app/utils/captura_conectores.py)
    — a tela mostra isso com clareza em vez de fingir uma busca que não
    acontece. Restrita a admin/gestor (mesmo critério de outras ferramentas
    de governança/custo desta rodada, ex. alçada de aprovação): é uma busca
    que, quando um provedor estiver configurado, tem custo por consulta.
    """
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)
    if not (current_user.is_admin or current_user.is_gestor):
        abort(403)

    empresa = cliente.unidade.empresa if cliente.unidade else None
    resultados = None
    erro = None
    try:
        conector = obter_conector("due_diligence", empresa=empresa)
        resultados = conector.buscar_processos_por_parte(cpf_cnpj=cliente.cpf_cnpj, nome=cliente.nome)
        registrar_log(current_user, "consultou_due_diligence", "Cliente", cliente.id,
                      f"{cliente.nome} — {len(resultados)} processo(s) encontrado(s) via {conector.nome_fonte}")
        db.session.commit()
    except ConectorNaoConfiguradoError as e:
        erro = str(e)

    return render_template("clientes/due_diligence.html", cliente=cliente, resultados=resultados, erro=erro)


@clientes_bp.route("/<int:cliente_id>/anonimizar", methods=["GET", "POST"])
@login_required
@apenas_admin
def anonimizar(cliente_id):
    """
    Direito ao esquecimento (LGPD art. 18 VI, PENDENCIAS.md seção -43) —
    só admin (ação irreversível de compliance, mesmo padrão de acesso de
    outras ações sensíveis do sistema) e sempre com confirmação explícita
    (GET mostra o que será apagado + processos ativos vinculados como
    aviso; só o POST, com o checkbox de confirmação marcado, executa).
    Nunca decide sozinho que a anonimização é apropriada — isso depende de
    não haver mais base legal pra reter o dado, uma avaliação jurídica que
    cabe a quem está usando o sistema, não ao software.
    """
    cliente = db.get_or_404(Cliente, cliente_id)
    checar_acesso_unidade_ou_403(cliente.unidade_id)

    if cliente.anonimizado_em:
        flash("Este cliente já foi anonimizado.", "info")
        return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))

    if request.method == "POST":
        if not request.form.get("confirmar"):
            flash("Confirme marcando a caixa de ciência antes de anonimizar.", "danger")
            return redirect(url_for("clientes.anonimizar", cliente_id=cliente.id))

        nome_original = cliente.nome
        anonimizar_cliente(cliente, current_user)
        registrar_log(current_user, "anonimizou", "Cliente", cliente.id,
                      f"nome original: {nome_original}")
        db.session.commit()
        flash("Dados pessoais do cliente foram anonimizados. Processos e lançamentos vinculados foram mantidos.", "success")
        return redirect(url_for("clientes.detalhe", cliente_id=cliente.id))

    processos_ativos = cliente.processos.filter_by(status="ativo").all()
    return render_template("clientes/anonimizar_confirmar.html", cliente=cliente, processos_ativos=processos_ativos)
