"""
Rotas de governança de carteira processual — implementação das seções
5 a 10 do briefing que dependiam só de código (sem provedor externo).

Ingestão automática de verdade (seção 5.0/5.2) continua bloqueada por
depender de um provedor de dados processuais contratado (ver
app/utils/captura_conectores.py) — aqui o cadastro por CNJ e a
importação em lote fazem a parte que dá para fazer sem isso: validar o
número, cadastrar o processo e marcar honestamente como "não
monitorável automaticamente" até um conector real existir (seção 5.1,
3º caminho: "o sistema nunca deixa buraco silencioso na carteira").
"""
import csv
import hashlib
import io
from datetime import datetime, timedelta, date
from decimal import Decimal

from flask import (Blueprint, render_template, request, redirect, url_for,
                    flash, Response, current_app, jsonify, abort)
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models import (Processo, Cliente, Unidade, Movimentacao, Publicacao, Decisao,
                         Prazo, HistoricoEstadoProcesso, SenhaProcesso, LogCaptura,
                         MapaEstadoTPU, RegraProximaAcao, AgenteLocalPareado, SolicitacaoBuscaAutos,
                         ModeloPeca, TabelaCustas)
from app.utils.calculo_custas import TABELA_PADRAO_TJSP
from app.utils.acesso import (aplicar_escopo_unidade, unidade_id_para_novo_registro, checar_acesso_unidade_ou_403,
                               unidades_do_escopo, usuarios_do_escopo, apenas_admin,
                               checar_acesso_processo_ou_403, filtrar_processos_visiveis)
from app.utils.notificacoes import registrar_log, notificar
from app.utils.cnj import validar_numero_cnj, somente_digitos
from app.utils.cofre import cifrar_senha_processo, decifrar_senha_processo, CofreNaoConfiguradoError
from app.utils.captura_conectores import obter_conector, ConectorNaoConfiguradoError
from app.utils.conector_datajud import TribunalNaoIdentificadoError, ConexaoDataJudError
from app.utils.conector_esaj_publico import ConectorEsajPublico, ErroEsajPublico, EsajProtegidoPorSenhaError
from app.utils.conector_pje_publico import ConectorPjePublico, ErroPjePublico
from app.utils.conector_pje_jt_publico import ConectorPjeJtPublico, ErroPjeJtPublico
from app.utils.captura_pipeline import (aplicar_carga_inicial, registrar_movimentacoes_capturadas,
                                         montar_nota_datajud, tentar_fontes_publicas_complementares,
                                         mensagem_fontes_extra)
from app.utils import tribunais_conectores
from app.utils.estado_processual_engine import traduzir_movimentacao
from app.utils.prazos_engine import aplicar_regra_proxima_acao
from app.utils.audiencias_engine import detectar_e_aplicar_audiencia
from app.utils import tribunais_datajud
from app.utils.paginacao import paginar, limitar_com_total
from app.utils.conflito_interesse import varrer_conflitos_da_empresa
from app.utils.indexacao_documentos import info_ultima_busca_autos

governanca_bp = Blueprint("governanca", __name__)


# ---------- Cadastro por número CNJ (seção 5.0) ----------

@governanca_bp.route("/processos/novo-por-cnj", methods=["GET", "POST"])
@login_required
def novo_por_cnj():
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()
    unidades = unidades_do_escopo() if current_user.is_admin else None

    if request.method == "POST":
        numero = request.form.get("numero_cnj", "")
        # exigir_dv=False: número com dígito verificador que não bate pela
        # fórmula oficial não trava mais o cadastro (pode ser numeração
        # legada real) — quem decide se o processo existe é a busca no
        # DataJud logo abaixo, não esse cálculo. Ver app/utils/cnj.py.
        resultado = validar_numero_cnj(numero, exigir_dv=False)
        if not resultado["valido"]:
            flash(f"Número CNJ inválido: {resultado['motivo']}", "danger")
            return redirect(url_for("governanca.novo_por_cnj"))

        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)
        partes = resultado["partes"]
        tribunal_hint = request.form.get("tribunal_datajud") or None

        # Cadastra já (com forma_acompanhamento provisória — o DataJud
        # decide o valor final mais abaixo) pra ter um `processo.id` antes
        # de chamar qualquer conector: e-SAJ/PJe/PJe-JT e o LogCaptura do
        # DataJud precisam da linha já existir no banco.
        processo = Processo(
            numero_processo=partes["formatado"],
            area_direito=request.form.get("area_direito") or "Não classificada",
            cliente_id=request.form["cliente_id"],
            unidade_id=unidade_id,
            criado_por_id=current_user.id,
            responsavel_id=current_user.id,
            forma_acompanhamento="nao_monitoravel",
            monitoravel=False,
            motivo_nao_monitoravel=None,
            tribunal_datajud=tribunal_hint,
            segredo_justica=bool(request.form.get("segredo_justica")),
        )
        db.session.add(processo)
        db.session.flush()

        # Ordem invertida (PENDENCIAS.md, seção -95): e-SAJ/PJe/PJe-JT ANTES
        # do DataJud — leem o sistema do próprio tribunal em tempo real,
        # então acham processo recém-distribuído que o DataJud (indexação
        # do CNJ, com defasagem variável, às vezes de dias) ainda pode não
        # ter, e costumam trazer mais campos. `aplicar_carga_inicial` só
        # preenche campo vazio, então rodar estas primeiro — tentativa E
        # aplicação dos dados — é o que faz elas priorizarem sobre o
        # DataJud quando as duas acham a mesma informação. Mesmo trio que o
        # botão "Buscar processo" usa pra um processo já existente; só
        # enriquece dados, nunca mexe em monitoravel/forma_acompanhamento.
        resultados_extra = tentar_fontes_publicas_complementares(processo, partes["segmento_codigo"])

        # DataJud por último: continua sendo tentado sempre (é a única
        # fonte que cobre QUALQUER segmento, e a única com recaptura
        # periódica de verdade) — cai para "não monitorável" de forma
        # honesta se a chave não estiver configurada, se o tribunal não
        # puder ser identificado, ou se o processo ainda não estiver
        # indexado (segredo de justiça, ou defasagem do próprio DataJud).
        # Respeita a chave própria do DataJud da empresa quando cadastrada
        # (ver app/routes/integracoes.py).
        empresa_do_cadastro = db.session.get(Unidade, unidade_id).empresa
        dados_capturados, motivo = None, None
        try:
            conector = obter_conector("padrao", empresa=empresa_do_cadastro)
            dados_capturados = conector.consultar_processo(partes["formatado"], tribunal_hint=tribunal_hint)
        except ConectorNaoConfiguradoError as e:
            motivo = str(e)
        except TribunalNaoIdentificadoError as e:
            motivo = str(e)
        except ConexaoDataJudError as e:
            motivo = str(e)

        qtd_movimentacoes_novas = 0
        if dados_capturados:
            processo.tribunal_datajud = dados_capturados["tribunal_slug"]
            aplicar_carga_inicial(processo, dados_capturados)
            qtd_movimentacoes_novas = registrar_movimentacoes_capturadas(
                processo, dados_capturados["movimentacoes"], captura_inicial=True
            )
            processo.monitoravel = True
            processo.forma_acompanhamento = "automatico"
            processo.motivo_nao_monitoravel = None
            db.session.add(LogCaptura(
                fonte="datajud", processo_id=processo.id, tribunal=dados_capturados["tribunal_slug"],
                status="sucesso", mensagem=f"{qtd_movimentacoes_novas} movimentação(ões) capturada(s).",
            ))
        else:
            processo.forma_acompanhamento = "nao_monitoravel"
            processo.monitoravel = False
            processo.motivo_nao_monitoravel = motivo
            if motivo:
                db.session.add(LogCaptura(
                    fonte="datajud", processo_id=processo.id, tribunal=tribunal_hint,
                    status="falha", mensagem=motivo[:500],
                ))

        registrar_log(current_user, "cadastro_por_cnj", "Processo", processo.id, processo.numero_processo)
        db.session.commit()

        aviso_dv = resultado.get("aviso_dv")
        extra_txt = mensagem_fontes_extra(resultados_extra)
        if not processo.monitoravel:
            flash(f"Processo {processo.numero_processo} cadastrado, mas marcado como NÃO monitorável "
                  f"automaticamente: {motivo}{extra_txt}", "success" if extra_txt else "warning")
        else:
            flash(f"Processo {processo.numero_processo} cadastrado e em monitoramento automático "
                  f"({qtd_movimentacoes_novas} movimentação(ões) já capturada(s) do DataJud)."
                  + (f" Atenção: {aviso_dv}" if aviso_dv else "") + extra_txt, "success")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    return render_template("governanca/novo_por_cnj.html", clientes=clientes, unidades=unidades,
                            tribunais_datajud=tribunais_datajud.TODOS)


@governanca_bp.route("/processos/entrada")
@login_required
def entrada_processos():
    """
    Hub único de entrada de processos (simplificação de menu, a pedido
    explícito): reúne em três abas da mesma tela o que antes eram itens
    separados no menu — "Cadastro por CNJ" (ação pontual, processo já com
    número conhecido), "Captação por OAB" (monitoramento contínuo, sem
    precisar saber o número antes) e, a pedido explícito também ("inclusive
    em 'entrada de processos', poderia incluir o importar em lotes lá
    dentro também"), "Importar em lote" (CSV, vários processos de uma vez).
    As três continuam sendo funcionalidades distintas por baixo (ver
    docstring de app/routes/captacao_oab.py e a rota governanca.
    importar_lote abaixo) — só a NAVEGAÇÃO foi unificada; os formulários
    das três abas continuam enviando para as mesmas rotas de sempre
    (governanca.novo_por_cnj via POST, captacao_oab.nova e governanca.
    importar_lote via POST), sem nenhuma mudança na lógica de negócio de
    nenhuma delas. As telas antigas (governanca.novo_por_cnj GET,
    captacao_oab.index e governanca.importar_lote GET) continuam existindo
    e funcionando normalmente para quem chegar direto por um link salvo —
    só não aparecem mais sozinhas no menu.

    `?tab=oab` abre direto na aba de OAB (usado por quem clica em "Ver
    captação por OAB" a partir de outra tela). `?tab=lote` abre direto na
    aba de importação em lote. `?numero_cnj=...` (com ou sem `tab=cnj`)
    pré-preenche o número na aba de CNJ — é o que o link de "cadastrar
    processo novo a partir da intimação" da triagem por OAB usa.
    """
    from app.models import OabMonitorada, IntimacaoCapturada

    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()
    unidades = unidades_do_escopo() if current_user.is_admin else None

    oabs = aplicar_escopo_unidade(OabMonitorada.query, OabMonitorada).order_by(OabMonitorada.criado_em.desc()).all()
    total_pendente_triagem = aplicar_escopo_unidade(
        IntimacaoCapturada.query, IntimacaoCapturada
    ).filter_by(status="pendente_triagem").count()
    usuarios = usuarios_do_escopo()

    aba_pedida = request.args.get("tab")
    aba_inicial = aba_pedida if aba_pedida in ("oab", "lote") else "cnj"

    return render_template(
        "governanca/entrada_processos.html",
        clientes=clientes, unidades=unidades, tribunais_datajud=tribunais_datajud.TODOS,
        oabs=oabs, total_pendente_triagem=total_pendente_triagem, usuarios=usuarios,
        aba_inicial=aba_inicial,
    )


def _preview_json_encontrado(dados, fonte_rotulo, aviso_dv=None):
    """
    Monta o JSON de "achou" da pré-visualização (usado tanto pelo DataJud
    quanto pelo e-SAJ público — ver consultar_cnj_preview abaixo), a
    partir do dict padrão que todo ConectorCaptura.consultar_processo()
    devolve (mesmas chaves: classe, assunto, orgao_julgador, comarca,
    instancia, data_ajuizamento, valor_causa, movimentacoes, nivel_sigilo).

    `fonte_rotulo`: "DataJud" ou "e-SAJ público" — vai tanto no JSON (pro
    JS decidir a mensagem certa) quanto na nota de descrição (ver
    montar_nota_datajud).
    """
    comarca_aviso = None
    if not dados.get("comarca"):
        codigo_municipio = dados.get("comarca_codigo_ibge")
        if codigo_municipio:
            comarca_aviso = (f"O DataJud indicou o código de município IBGE {codigo_municipio} para "
                              "este processo, mas não consegui confirmar o nome agora (falha ao consultar "
                              "a API do IBGE) — tente de novo em instantes, ou preencha a comarca à mão.")
        elif fonte_rotulo == "DataJud":
            comarca_aviso = ("O DataJud não informou o código do município para este processo/tribunal "
                              "— preencha a comarca à mão.")

    return jsonify(
        valido=True, encontrado=True, fonte=fonte_rotulo,
        tribunal_slug=dados["tribunal_slug"],
        classe=dados["classe"],
        assunto=dados["assunto"],
        orgao_julgador=dados["orgao_julgador"],
        data_ajuizamento=dados["data_ajuizamento"].strftime("%d/%m/%Y") if dados["data_ajuizamento"] else None,
        # formato ISO (YYYY-MM-DD) à parte do "data_ajuizamento" (dd/mm/aaaa)
        # acima — é o formato que <input type="date"> exige pra preencher
        # sozinho (ver app/templates/processos/form.html); o dd/mm/aaaa
        # continua servindo só pra exibição na pré-visualização.
        data_ajuizamento_iso=dados["data_ajuizamento"].strftime("%Y-%m-%d") if dados["data_ajuizamento"] else None,
        valor_causa=str(dados["valor_causa"]) if dados["valor_causa"] is not None else None,
        qtd_movimentacoes=len(dados["movimentacoes"]),
        aviso_dv=aviso_dv,
        # Ver app/utils/conector_datajud.py (campo "grau" do DataJud) e
        # app/utils/ibge.py (nome do município a partir do código IBGE que
        # vem dentro de "orgaoJulgador"). Nem todo tribunal/processo/fonte
        # devolve esses dois; ficam None quando não dá.
        instancia=dados.get("instancia"),
        comarca=dados.get("comarca"),
        comarca_aviso=comarca_aviso,
        # Nota com o que não tem campo próprio no cadastro (mais de um
        # assunto, sistema/formato, sigilo) — ver
        # app/utils/captura_pipeline.py::montar_nota_datajud. None quando
        # não há nada extra que valha a pena mostrar.
        descricao_sugerida=montar_nota_datajud(dados, fonte_rotulo=fonte_rotulo),
        sigilo_sugerido=bool(dados.get("nivel_sigilo") not in (None, 0)),
    )


@governanca_bp.route("/processos/consultar-cnj")
@login_required
def consultar_cnj_preview():
    """
    Pré-visualização via AJAX dos dados de um processo a partir do número
    CNJ — chamada pelo JS de processos/form.html (tanto ao apertar Enter
    quanto, desde a seção -72, automaticamente enquanto o usuário digita o
    número completo) ANTES de salvar o processo. NUNCA grava nada no
    banco — é só uma consulta de leitura, para o usuário ver
    classe/assunto/órgão julgador e confirmar que é o processo certo antes
    de cadastrar de fato (o cadastro em si continua acontecendo só no POST
    da tela "Novo processo"/"Editar processo", que faz a mesma consulta de
    novo e persiste).

    Ordem de tentativa (PENDENCIAS.md, seções -72, -78, -90 e, a partir de
    agora, -95 — ORDEM INVERTIDA): primeiro o(s) conector(s) público(s)
    específico(s) do segmento do número — e-SAJ e depois PJe pra estadual
    (segmento "8"), PJe-JT pra trabalhista (segmento "5") — e só DEPOIS o
    DataJud, como último recurso. Antes o DataJud vinha primeiro; a ordem
    inverteu porque esses conectores públicos leem o sistema do próprio
    tribunal em tempo real, então acham processo recém-distribuído que o
    DataJud ainda não indexou (a indexação do CNJ tem defasagem variável,
    às vezes de dias) — e, quando o DataJud está indisponível ou lento
    (timeout da API pública do CNJ, por exemplo), tentar essas fontes
    primeiro evita que a pré-visualização inteira fique esperando o
    DataJud responder (ou estourar o timeout) antes de sequer tentar uma
    fonte que talvez já tivesse achado o processo na hora.
    - Qualquer segmento sem conector público (nem "8" nem "5"): só o
      DataJud mesmo — é a única fonte que cobre qualquer segmento.
    Nenhuma tentativa extra tem custo de espera perceptível a mais pro
    usuário além da primeira (ele já ia esperar alguma fonte responder de
    qualquer jeito).
    """
    numero = request.args.get("numero_cnj", "")
    # exigir_dv=False: mesmo raciocínio do cadastro em si — não barra a
    # busca por dígito verificador que não bate, só avisa (aviso_dv).
    resultado = validar_numero_cnj(numero, exigir_dv=False)
    if not resultado["valido"]:
        return jsonify(valido=False, motivo=resultado["motivo"])

    partes = resultado["partes"]
    tribunal_hint = request.args.get("tribunal_datajud") or None
    segmento = partes["segmento_codigo"]

    motivo_esaj = None
    motivo_pje = None
    motivo_pje_jt = None

    def _tentar_datajud():
        """Só chamado quando nenhuma fonte específica do segmento achou
        (ou quando o segmento não tem nenhuma) — devolve (motivo, precisa_tribunal)."""
        try:
            conector = obter_conector("padrao", empresa=current_user.empresa)
            dados = conector.consultar_processo(partes["formatado"], tribunal_hint=tribunal_hint)
            return dados, None, False
        except ConectorNaoConfiguradoError as e:
            return None, str(e), False
        except TribunalNaoIdentificadoError as e:
            return None, str(e), True
        except ConexaoDataJudError as e:
            return None, str(e), False

    if segmento == "8":
        try:
            dados_esaj = ConectorEsajPublico().consultar_processo(partes["formatado"])
            return _preview_json_encontrado(dados_esaj, "e-SAJ público")
        except EsajProtegidoPorSenhaError as e:
            # Sinal definitivo (processo existe, só não dá pra ler sem
            # senha) — tenta o DataJud só pra complementar a mensagem, mas
            # a resposta já é "não encontrado" (protegido) de qualquer jeito.
            motivo_protegido = str(e)
            _, motivo_datajud, _ = _tentar_datajud()
            motivo = f"{motivo_protegido} Também tentei o DataJud: {motivo_datajud}" if motivo_datajud else motivo_protegido
            return jsonify(valido=True, encontrado=False, motivo=motivo)
        except ErroEsajPublico as e:
            motivo_esaj = str(e)

        # Terceira chance (PENDENCIAS.md, seção -78): PJe público (TJRJ,
        # TJMG) — mesma ideia do e-SAJ público, ver aviso completo em
        # app/utils/conector_pje_publico.py sobre por que só esses dois
        # tribunais e as limitações (sigilo indistinguível de "não achou",
        # sem advogado nas partes).
        try:
            dados_pje = ConectorPjePublico().consultar_processo(partes["formatado"])
            return _preview_json_encontrado(dados_pje, "PJe público")
        except ErroPjePublico as e:
            motivo_pje = str(e)
    elif segmento == "5":
        # PENDENCIAS.md, seção -90: PJe-JT público (22 TRTs) — mesma ideia
        # do PJe estadual, ver aviso completo em
        # app/utils/conector_pje_jt_publico.py sobre os 2 TRTs não
        # cobertos (TRT-3, TRT-23) e as limitações do parsing.
        try:
            dados_pje_jt = ConectorPjeJtPublico().consultar_processo(partes["formatado"])
            return _preview_json_encontrado(dados_pje_jt, "PJe-JT público")
        except ErroPjeJtPublico as e:
            motivo_pje_jt = str(e)

    # DataJud por último — tentado sempre (cobre qualquer segmento), seja
    # pra segmento sem conector público nenhum, seja como último recurso
    # de quem já tentou acima e não achou.
    dados_datajud, motivo_datajud, precisa_tribunal = _tentar_datajud()
    if dados_datajud:
        return _preview_json_encontrado(dados_datajud, "DataJud", aviso_dv=dados_datajud.get("aviso_dv"))

    if segmento not in ("8", "5"):
        # Nenhum conector público além do DataJud atende esse segmento —
        # tentar os outros só acrescentaria ruído na mensagem de erro.
        return jsonify(valido=True, encontrado=False, motivo=motivo_datajud, precisa_tribunal=precisa_tribunal)

    # Junta os motivos de quem foi tentado e não achou, cada um já com o
    # nome da fonte (generalizado pra qualquer subconjunto de fontes
    # tentadas conforme o segmento do número).
    motivos_com_rotulo = [
        (rotulo, m) for rotulo, m in
        (("e-SAJ público", motivo_esaj), ("PJe público", motivo_pje), ("PJe-JT público", motivo_pje_jt),
         ("DataJud", motivo_datajud))
        if m
    ]
    motivo_final = "; ".join(f"{rotulo}: {m}" for rotulo, m in motivos_com_rotulo)
    return jsonify(valido=True, encontrado=False, motivo=motivo_final, precisa_tribunal=precisa_tribunal)


# ---------- Importação em lote (CSV) ----------

@governanca_bp.route("/processos/importar-lote", methods=["GET", "POST"])
@login_required
def importar_lote():
    clientes = aplicar_escopo_unidade(Cliente.query, Cliente).filter_by(ativo=True).order_by(Cliente.nome).all()

    if request.method == "POST":
        arquivo = request.files.get("arquivo_csv")
        cliente_id = request.form.get("cliente_id")
        area_direito = request.form.get("area_direito") or "Não classificada"
        unidade_id = unidade_id_para_novo_registro()
        checar_acesso_unidade_ou_403(unidade_id)

        if not arquivo or not cliente_id:
            flash("Selecione um arquivo CSV (uma coluna com números CNJ, um por linha) e o cliente.", "danger")
            return redirect(url_for("governanca.importar_lote"))

        conteudo = arquivo.read().decode("utf-8-sig", errors="ignore")
        linhas = [l.strip() for l in conteudo.splitlines() if l.strip()]

        relatorio = []
        criados = 0
        for linha in linhas:
            numero = linha.split(",")[0].strip()
            if somente_digitos(numero) == "" or len(somente_digitos(numero)) < 20 and numero.lower() in ("numero_cnj", "numero", "cnj"):
                continue  # ignora possível cabeçalho
            resultado = validar_numero_cnj(numero)
            if not resultado["valido"]:
                relatorio.append({"numero": numero, "sucesso": False, "motivo": resultado["motivo"]})
                continue

            partes = resultado["partes"]
            existente = Processo.query.filter_by(numero_processo=partes["formatado"]).first()
            if existente:
                relatorio.append({"numero": partes["formatado"], "sucesso": False, "motivo": "Já cadastrado."})
                continue

            processo = Processo(
                numero_processo=partes["formatado"],
                area_direito=area_direito,
                cliente_id=cliente_id,
                unidade_id=unidade_id,
                criado_por_id=current_user.id,
                responsavel_id=current_user.id,
                forma_acompanhamento="nao_monitoravel",
                monitoravel=False,
                motivo_nao_monitoravel="Importado em lote — sem provedor de captura configurado (seção 5.2).",
            )
            db.session.add(processo)
            criados += 1
            relatorio.append({"numero": partes["formatado"], "sucesso": True, "motivo": None})

        registrar_log(current_user, "importacao_lote", "Processo", None,
                      f"{criados} processos criados de {len(linhas)} linhas")
        db.session.commit()

        # Nota importante de honestidade: sem fila assíncrona (Celery/RQ) provisionada,
        # este processamento é síncrono — para lotes grandes (ex: os 500 do briefing),
        # a requisição HTTP pode demorar. Ver README para o que falta de infraestrutura.
        return render_template("governanca/importar_lote_resultado.html", relatorio=relatorio, criados=criados,
                                total=len(linhas))

    return render_template("governanca/importar_lote.html", clientes=clientes)


# ---------- Cofre de senha de processo (seção 5.1) ----------

@governanca_bp.route("/processos/<int:processo_id>/senha", methods=["POST"])
@login_required
def cadastrar_senha_processo(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    valor = request.form.get("valor")
    tribunal = request.form.get("tribunal")
    if not valor:
        flash("Informe a senha do processo.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        cifrado = cifrar_senha_processo(valor)
    except CofreNaoConfiguradoError as e:
        flash(str(e), "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    existente = SenhaProcesso.query.filter_by(processo_id=processo.id).first()
    if existente:
        existente.valor_criptografado = cifrado
        existente.tribunal = tribunal
        existente.cadastrado_por_id = current_user.id
        existente.cadastrado_em = datetime.utcnow()
    else:
        db.session.add(SenhaProcesso(
            processo_id=processo.id, tribunal=tribunal, valor_criptografado=cifrado,
            cadastrado_por_id=current_user.id,
        ))

    processo.forma_acompanhamento = "senha_processo"
    processo.monitoravel = True
    processo.motivo_nao_monitoravel = None

    registrar_log(current_user, "cadastrar_senha_processo", "Processo", processo.id, "senha do processo cadastrada")
    db.session.commit()
    flash("Senha do processo cadastrada no cofre (criptografada).", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/senha/ver", methods=["POST"])
@login_required
def ver_senha_processo(processo_id):
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    senha = SenhaProcesso.query.filter_by(processo_id=processo.id).first()
    if not senha:
        flash("Nenhuma senha cadastrada para este processo.", "warning")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    # Acesso restrito ao usuário que cadastrou (seção 5.1), exceto admin.
    if senha.cadastrado_por_id != current_user.id and not current_user.is_admin:
        flash("Só quem cadastrou a senha (ou um admin) pode visualizá-la.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        valor = decifrar_senha_processo(senha.valor_criptografado)
    except (CofreNaoConfiguradoError, ValueError) as e:
        flash(str(e), "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    senha.ultimo_acesso_em = datetime.utcnow()
    senha.ultimo_acesso_por_id = current_user.id
    registrar_log(current_user, "ver_senha_processo", "Processo", processo.id, "leitura da senha do processo")
    db.session.commit()

    return render_template("governanca/senha_visualizar.html", processo=processo, senha=senha, valor=valor)


# ---------- Registro manual de movimentação (stand-in para captura automática) ----------

@governanca_bp.route("/processos/<int:processo_id>/movimentacoes/nova", methods=["POST"])
@login_required
def nova_movimentacao(processo_id):
    """
    Enquanto não há conector de captura real (ver captura_conectores.py),
    esta é a porta de entrada para registrar uma movimentação e disparar
    a máquina de estados (seção 6) e o motor de próxima ação (seção 7.1) —
    o mesmo pipeline que rodaria automaticamente quando o conector existir.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    data_str = request.form.get("data")
    codigo_tpu = request.form.get("codigo_tpu") or None
    texto = request.form.get("texto_integral", "").strip()
    if not texto:
        flash("Descreva o texto da movimentação.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    data_mov = datetime.strptime(data_str, "%Y-%m-%dT%H:%M") if data_str else datetime.utcnow()
    hash_dedup = hashlib.sha256(f"{processo.id}|{data_mov.isoformat()}|{texto}".encode()).hexdigest()

    if Movimentacao.query.filter_by(hash_dedup=hash_dedup).first():
        flash("Movimentação idêntica já registrada (deduplicação).", "warning")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    mov = Movimentacao(
        processo_id=processo.id, data=data_mov, codigo_tpu=codigo_tpu,
        texto_integral=texto, origem_captura="manual", hash_dedup=hash_dedup,
    )
    db.session.add(mov)
    db.session.flush()

    historico = traduzir_movimentacao(mov)
    if historico:
        db.session.add(historico)

    # Detecção de audiência (PENDENCIAS.md, seção -77) — mesma função
    # usada na captura automática (app/utils/captura_pipeline.py), pra
    # este caminho manual continuar espelhando o que a captura
    # automática faria (ver docstring desta rota acima).
    detectar_e_aplicar_audiencia(mov)
    db.session.flush()

    prazo_gerado = aplicar_regra_proxima_acao(mov)
    if prazo_gerado:
        db.session.add(prazo_gerado)
        db.session.flush()
        if prazo_gerado.responsavel_id:
            notificar(prazo_gerado.responsavel_id, "Novo prazo gerado automaticamente",
                      f"{prazo_gerado.descricao} — vence em {prazo_gerado.data_vencimento.strftime('%d/%m/%Y')}",
                      tipo="prazo", link=url_for("processos.detalhe", processo_id=processo.id))

    registrar_log(current_user, "registrar_movimentacao", "Processo", processo.id,
                  f"triagem_pendente={mov.triagem_pendente}")
    db.session.commit()

    if mov.triagem_pendente:
        flash("Movimentação registrada, mas o código TPU não está mapeado — "
              "caiu na fila de triagem (aba Governança).", "warning")
    else:
        flash(f"Movimentação registrada. Estado atualizado para: {mov.estado_negocio_resultante or '—'}.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/nao-monitoravel", methods=["POST"])
@login_required
def marcar_nao_monitoravel(processo_id):
    """3º caminho da seção 5.1: marcação explícita, nunca buraco silencioso."""
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)
    motivo = request.form.get("motivo", "").strip() or "Motivo não informado"
    processo.forma_acompanhamento = "nao_monitoravel"
    processo.monitoravel = False
    processo.motivo_nao_monitoravel = motivo
    registrar_log(current_user, "marcar_nao_monitoravel", "Processo", processo.id, motivo)
    db.session.commit()
    flash("Processo marcado como não monitorável automaticamente — aparece sinalizado no painel.", "info")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/tentar-captura", methods=["POST"])
@login_required
def tentar_captura(processo_id):
    """
    Tenta (de novo) a captura automática via DataJud pra um processo que
    já está cadastrado — usada quando a primeira tentativa (no cadastro
    por CNJ) falhou por falta de tribunal selecionado, chave do DataJud
    configurada depois do cadastro, ou qualquer outro motivo temporário.
    Reaproveita o mesmo pipeline do cadastro inicial (aplicar_carga_inicial
    + registrar_movimentacoes_capturadas), que só preenche campo vazio e
    deduplica movimentação por hash — seguro rodar de novo quantas vezes
    precisar, nunca duplica nem sobrescreve o que já foi preenchido à mão.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    tribunal_hint = request.form.get("tribunal_datajud") or processo.tribunal_datajud
    if not processo.numero_processo:
        flash("Este processo não tem número CNJ cadastrado — não dá pra buscar no DataJud.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    empresa = processo.unidade.empresa if processo.unidade else None

    try:
        conector = obter_conector("padrao", empresa=empresa)
        dados_capturados = conector.consultar_processo(processo.numero_processo, tribunal_hint=tribunal_hint)
    except ConectorNaoConfiguradoError as e:
        flash(f"Captura não configurada: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))
    except TribunalNaoIdentificadoError as e:
        flash(str(e), "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))
    except ConexaoDataJudError as e:
        db.session.add(LogCaptura(fonte="datajud", processo_id=processo.id, tribunal=tribunal_hint,
                                   status="falha", mensagem=str(e)[:500]))
        db.session.commit()
        flash(f"Não foi possível buscar o processo no DataJud agora: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    processo.tribunal_datajud = dados_capturados["tribunal_slug"]
    aplicar_carga_inicial(processo, dados_capturados)
    novas = registrar_movimentacoes_capturadas(
        processo, dados_capturados["movimentacoes"], captura_inicial=True
    )
    processo.monitoravel = True
    processo.forma_acompanhamento = "automatico"
    processo.motivo_nao_monitoravel = None

    db.session.add(LogCaptura(
        fonte="datajud", processo_id=processo.id, tribunal=dados_capturados["tribunal_slug"],
        status="sucesso", mensagem=f"{novas} movimentação(ões) capturada(s) (nova tentativa manual).",
    ))
    registrar_log(current_user, "tentou_captura_datajud", "Processo", processo.id, processo.numero_processo)
    db.session.commit()
    flash(f"Processo encontrado no DataJud — {novas} movimentação(ões) capturada(s). "
          f"Agora está em monitoramento automático.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/tentar-captura-esaj", methods=["POST"])
@login_required
def tentar_captura_esaj(processo_id):
    """
    Busca dados públicos direto do e-SAJ (TJSP) — sem certificado, sem
    token, sem login (PENDENCIAS.md, seção -71). Complementar ao DataJud
    (`tentar_captura` acima): só cobre TJSP, 1º grau, e é scraping de
    página pública (sem contrato oficial) em vez de API do CNJ — ver
    avisos completos em app/utils/conector_esaj_publico.py.

    Reaproveita o mesmo pipeline de carga inicial + dedup por hash do
    DataJud, mas com origem_captura="esaj_publico" — seguro rodar quantas
    vezes precisar, e nunca sobrescreve campo já preenchido manualmente.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    if not processo.numero_processo:
        flash("Este processo não tem número CNJ cadastrado — não dá pra buscar no e-SAJ.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        conector = ConectorEsajPublico()
        dados_capturados = conector.consultar_processo(processo.numero_processo)
    except ErroEsajPublico as e:
        db.session.add(LogCaptura(fonte="esaj_publico", processo_id=processo.id, tribunal="tjsp",
                                   status="falha", mensagem=str(e)[:500]))
        db.session.commit()
        flash(f"Não foi possível buscar o processo no e-SAJ agora: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    aplicar_carga_inicial(processo, dados_capturados, fonte_rotulo="e-SAJ")
    novas = registrar_movimentacoes_capturadas(
        processo, dados_capturados["movimentacoes"], captura_inicial=True, origem_captura="esaj_publico",
    )
    qtd_partes = len(dados_capturados.get("partes") or [])

    db.session.add(LogCaptura(
        fonte="esaj_publico", processo_id=processo.id, tribunal="tjsp", status="sucesso",
        mensagem=f"{novas} movimentação(ões) e {qtd_partes} parte(s) capturada(s) do e-SAJ público.",
    ))
    registrar_log(current_user, "tentou_captura_esaj_publico", "Processo", processo.id, processo.numero_processo)
    db.session.commit()
    flash(f"Processo encontrado no e-SAJ (TJSP) — {novas} movimentação(ões) nova(s) e {qtd_partes} "
          f"parte(s) identificada(s). Fonte pública, sem certificado nem token.", "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/tentar-captura-pje", methods=["POST"])
@login_required
def tentar_captura_pje(processo_id):
    """
    Busca dados públicos direto do PJe (TJRJ, TJMG) — sem certificado,
    sem token, sem login (PENDENCIAS.md, seção -78 — mesma ideia do
    `tentar_captura_esaj` acima, aplicada à plataforma PJe em vez de
    e-SAJ). Ver aviso completo em app/utils/conector_pje_publico.py sobre
    por que só esses dois tribunais (TJRS e TJPR NÃO são cobertos ainda).

    Reaproveita o mesmo pipeline de carga inicial + dedup por hash do
    DataJud/e-SAJ público, com origem_captura="pje_publico" — seguro
    rodar quantas vezes precisar, e nunca sobrescreve campo já
    preenchido manualmente.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    if not processo.numero_processo:
        flash("Este processo não tem número CNJ cadastrado — não dá pra buscar no PJe.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        conector = ConectorPjePublico()
        dados_capturados = conector.consultar_processo(processo.numero_processo)
    except ErroPjePublico as e:
        db.session.add(LogCaptura(fonte="pje_publico", processo_id=processo.id, tribunal=None,
                                   status="falha", mensagem=str(e)[:500]))
        db.session.commit()
        flash(f"Não foi possível buscar o processo no PJe agora: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    tribunal_slug = dados_capturados.get("tribunal_slug")
    aplicar_carga_inicial(processo, dados_capturados, fonte_rotulo="PJe")
    novas = registrar_movimentacoes_capturadas(
        processo, dados_capturados["movimentacoes"], captura_inicial=True, origem_captura="pje_publico",
    )
    qtd_partes = len(dados_capturados.get("partes") or [])

    db.session.add(LogCaptura(
        fonte="pje_publico", processo_id=processo.id, tribunal=tribunal_slug, status="sucesso",
        mensagem=f"{novas} movimentação(ões) e {qtd_partes} parte(s) capturada(s) do PJe público.",
    ))
    registrar_log(current_user, "tentou_captura_pje_publico", "Processo", processo.id, processo.numero_processo)
    db.session.commit()
    flash(f"Processo encontrado no PJe ({(tribunal_slug or '').upper()}) — {novas} movimentação(ões) "
          f"nova(s) e {qtd_partes} parte(s) identificada(s). Fonte pública, sem certificado nem token.",
          "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/tentar-captura-pje-jt", methods=["POST"])
@login_required
def tentar_captura_pje_jt(processo_id):
    """
    Busca dados públicos direto do PJe-JT (22 TRTs) — sem certificado,
    sem token, sem login (PENDENCIAS.md, seção -90 — mesma ideia do
    `tentar_captura_pje` acima, aplicada à Justiça do Trabalho). Ver
    aviso completo em app/utils/conector_pje_jt_publico.py sobre os 2
    TRTs não cobertos (TRT-3, TRT-23) e as limitações do parsing (campos
    de capa ainda não confirmados contra um processo real).

    Reaproveita o mesmo pipeline de carga inicial + dedup por hash do
    DataJud/e-SAJ/PJe público, com origem_captura="pje_jt_publico" —
    seguro rodar quantas vezes precisar, e nunca sobrescreve campo já
    preenchido manualmente.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    if not processo.numero_processo:
        flash("Este processo não tem número CNJ cadastrado — não dá pra buscar no PJe-JT.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    try:
        conector = ConectorPjeJtPublico()
        dados_capturados = conector.consultar_processo(processo.numero_processo)
    except ErroPjeJtPublico as e:
        db.session.add(LogCaptura(fonte="pje_jt_publico", processo_id=processo.id, tribunal=None,
                                   status="falha", mensagem=str(e)[:500]))
        db.session.commit()
        flash(f"Não foi possível buscar o processo no PJe-JT agora: {e}", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    tribunal_slug = dados_capturados.get("tribunal_slug")
    aplicar_carga_inicial(processo, dados_capturados, fonte_rotulo="PJe-JT")
    novas = registrar_movimentacoes_capturadas(
        processo, dados_capturados["movimentacoes"], captura_inicial=True, origem_captura="pje_jt_publico",
    )
    qtd_partes = len(dados_capturados.get("partes") or [])

    db.session.add(LogCaptura(
        fonte="pje_jt_publico", processo_id=processo.id, tribunal=tribunal_slug, status="sucesso",
        mensagem=f"{novas} movimentação(ões) e {qtd_partes} parte(s) capturada(s) do PJe-JT público.",
    ))
    registrar_log(current_user, "tentou_captura_pje_jt_publico", "Processo", processo.id, processo.numero_processo)
    db.session.commit()
    flash(f"Processo encontrado no PJe-JT ({(tribunal_slug or '').upper()}) — {novas} movimentação(ões) "
          f"nova(s) e {qtd_partes} parte(s) identificada(s). Fonte pública, sem certificado nem token.",
          "success")
    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/buscar-processo", methods=["POST"])
@login_required
def buscar_processo(processo_id):
    """
    Rota unificada de busca (PENDENCIAS.md, seção -93) — o botão único
    "Buscar processo" da tela do processo, que substitui (na TELA; as
    rotas antigas continuam existindo por baixo, veja nota abaixo) os
    quatro botões separados de DataJud/e-SAJ/PJe/PJe-JT e o formulário
    manual de conector do Agente Local. Ordem de decisão:

    1. O advogado logado tem um Agente Local pareado (token — ver
       app/models/agente_local.py) e não pediu explicitamente pra pular
       pro caminho público (`forcar_publico` no form)? Dispara os 3
       conectores piloto (pje_mni, projudi, esaj_sp —
       app/utils/tribunais_conectores.py) em paralelo, cada um virando
       sua própria SolicitacaoBuscaAutos. IMPORTANTE: isso é
       ASSÍNCRONO — quem responde de verdade é o agente instalado na
       máquina do advogado, na próxima vez que ele verificar por
       tarefas (não é uma resposta na hora desta requisição). Sem
       duplicar pedido: se já existe uma solicitação aberta
       (pendente/em_andamento) pro mesmo conector+processo, não cria de
       novo. "Fallback gracioso" pra quem não quer esperar o piloto
       (ainda não testado contra tribunal real): a mesma tela mostra
       sempre um botão "buscar pelos sistemas públicos agora" que manda
       `forcar_publico=1` pra esta mesma rota — e destaca esse botão
       automaticamente quando as tentativas anteriores do agente já
       terminaram todas em erro (ver app/templates/processos/detalhe.html).
    2. Sem agente pareado (ou `forcar_publico`): caminho público, sempre
       começando pelo DataJud (metadados, qualquer segmento) e depois
       o(s) conector(s) público(s) que atendem o segmento do número CNJ
       — e-SAJ e PJe pra estadual (segmento 8), PJe-JT pra trabalhista
       (segmento 5) — ou só o que o usuário escolheu no campo `sistema`
       (auto/esaj/pje/pje_jt), se preencheu. Roda tudo que se aplica no
       mesmo clique: é seguro (aplicar_carga_inicial só preenche campo
       vazio; movimentação é deduplicada por hash), então em vez de um
       clique por fonte como antes, um clique só já traz o que der de
       mais completo.
    3. Segmento sem conector público automático (só haveria os links
       soltos de eproc/Projudi/Creta/Tucujuris)? Só o DataJud mesmo é
       tentado aqui — os botões de link solto, já mostrados na mesma
       tela fora desta rota, continuam sendo a forma do usuário abrir o
       portal oficial do tribunal pra uma busca mais completa manual
       (exatamente o "escolher DataJud ou algo mais completo" pedido).

    Resumo em PDF: não precisa de nenhum gatilho novo aqui — o botão
    "PDF" no topo da tela (app/utils/pdf_processo.py) já monta o resumo
    na hora a partir do banco, então qualquer dado novo capturado por
    este botão já aparece nele da próxima vez que for baixado.
    """
    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)

    if not processo.numero_processo:
        flash("Este processo não tem número CNJ cadastrado — não dá pra buscar automaticamente.", "danger")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    forcar_publico = bool(request.form.get("forcar_publico"))
    sistema_escolhido = (request.form.get("sistema") or "auto").strip()

    agente = AgenteLocalPareado.query.filter_by(usuario_id=current_user.id, ativo=True).first()
    if agente and not forcar_publico:
        criadas = []
        for slug in sorted(tribunais_conectores.CONECTORES_IMPLEMENTADOS):
            ja_aberta = SolicitacaoBuscaAutos.query.filter_by(
                processo_id=processo.id, tribunal_conector=slug,
            ).filter(SolicitacaoBuscaAutos.status.in_(SolicitacaoBuscaAutos.STATUS_ABERTOS)).first()
            if ja_aberta:
                continue
            db.session.add(SolicitacaoBuscaAutos(
                processo_id=processo.id, tribunal_conector=slug,
                numero_processo_solicitado=processo.numero_processo,
                solicitado_por_id=current_user.id,
            ))
            criadas.append(slug)

        if criadas:
            # Download incremental (item 2 — PENDENCIAS.md, seção -103): NÃO
            # bloqueia o pedido (o piloto do Agente Local ainda pode valer a
            # pena tentar de novo, ex.: uma tentativa anterior falhou por
            # rede) — só avisa quando o sistema já sabe que os autos
            # completos já foram baixados e indexados antes, sem nada novo
            # capturado desde então, pra quem pediu decidir com essa
            # informação em vez de pedir às cegas toda vez.
            info_anterior = info_ultima_busca_autos(processo)
            registrar_log(current_user, "solicitou_busca_autos_agente_local", "Processo", processo.id,
                          f"busca unificada (piloto, {len(criadas)} conector(es)): {', '.join(criadas)}")
            db.session.commit()
            if info_anterior and info_anterior["indexado"] and info_anterior["qtd_movimentacoes_novas"] == 0:
                flash("Busca enviada mesmo assim, mas os autos completos já tinham sido baixados e "
                      f"indexados em {info_anterior['documento'].enviado_em.strftime('%d/%m/%Y')} e "
                      "nenhuma movimentação nova foi capturada por nenhuma fonte desde então — talvez "
                      "não precise esperar por este pedido novo.", "info")
            else:
                flash("Busca enviada para o seu Agente Local (ainda em piloto, não testado contra nenhum "
                      f"tribunal real) nos {len(criadas)} conector(es) disponíveis — assim que o agente no "
                      "seu computador verificar por tarefas novas, o processo completo aparece aqui. Isso "
                      "pode levar alguns instantes, não é na hora. Se preferir não esperar, dá pra buscar "
                      "pelos sistemas públicos agora mesmo, na tabela mais abaixo.", "success")
        else:
            flash("Já existe uma busca do Agente Local em aberto para este processo — aguarde a "
                  "resposta ou cancele na tabela abaixo antes de pedir de novo.", "info")
        return redirect(url_for("processos.detalhe", processo_id=processo.id))

    # ---- Caminho público (sem agente pareado, ou o usuário optou por pular) ----
    numero = processo.numero_processo
    validado = validar_numero_cnj(numero, exigir_dv=False)
    segmento = validado["partes"]["segmento_codigo"] if validado["valido"] else None
    empresa = processo.unidade.empresa if processo.unidade else None

    # Ordem invertida (PENDENCIAS.md, seção -95): e-SAJ/PJe/PJe-JT ANTES do
    # DataJud — são conectores que leem o sistema do próprio tribunal em
    # tempo real, então acham processo recém-distribuído que o DataJud
    # (que depende da própria indexação do CNJ, com defasagem variável às
    # vezes de dias) ainda pode não ter; e também costumam trazer mais
    # campos (partes, por exemplo). `aplicar_carga_inicial` só preenche
    # campo VAZIO, então quem roda primeiro "ganha" o campo quando as duas
    # fontes têm o mesmo dado — rodar estas primeiro é o que faz elas
    # priorizarem sobre o DataJud, não só serem tentadas.
    #
    # O DataJud continua sendo tentado sempre, só que depois, como
    # confirmação/complemento: é a única fonte que cobre QUALQUER segmento,
    # e a única com recaptura periódica de verdade — só ele decide
    # `monitoravel`/`forma_acompanhamento` (ver app/utils/captura_pipeline.py).
    resultados_extra = tentar_fontes_publicas_complementares(processo, segmento, sistema_escolhido)

    resultado_datajud = None
    try:
        conector = obter_conector("padrao", empresa=empresa)
        dados_datajud = conector.consultar_processo(numero, tribunal_hint=processo.tribunal_datajud)
    except ConectorNaoConfiguradoError as e:
        dados_datajud = None
        resultado_datajud = ("DataJud", False, str(e))
    except TribunalNaoIdentificadoError as e:
        dados_datajud = None
        resultado_datajud = ("DataJud", False, str(e))
    except ConexaoDataJudError as e:
        dados_datajud = None
        db.session.add(LogCaptura(fonte="datajud", processo_id=processo.id, tribunal=processo.tribunal_datajud,
                                   status="falha", mensagem=str(e)[:500]))
        resultado_datajud = ("DataJud", False, str(e))

    if dados_datajud:
        processo.tribunal_datajud = dados_datajud["tribunal_slug"]
        aplicar_carga_inicial(processo, dados_datajud)
        novas = registrar_movimentacoes_capturadas(processo, dados_datajud["movimentacoes"], captura_inicial=True)
        processo.monitoravel = True
        processo.forma_acompanhamento = "automatico"
        processo.motivo_nao_monitoravel = None
        db.session.add(LogCaptura(
            fonte="datajud", processo_id=processo.id, tribunal=dados_datajud["tribunal_slug"], status="sucesso",
            mensagem=f"{novas} movimentação(ões) capturada(s) (busca unificada).",
        ))
        resultado_datajud = ("DataJud", True, f"{novas} movimentação(ões) nova(s).")

    resultados = resultados_extra + [resultado_datajud]

    registrar_log(current_user, "buscou_processo_unificado", "Processo", processo.id,
                  "; ".join(f"{f}:{'ok' if ok else 'falhou'}" for f, ok, _ in resultados) or "nenhuma fonte tentada")
    db.session.commit()

    sucessos = [r for r in resultados if r[1]]
    if sucessos:
        resumo = " ".join(f"{f}: {m}" for f, ok, m in resultados if ok)
        flash(f"Processo encontrado — {resumo} O resumo em PDF (botão \"PDF\" no topo) já reflete os "
              "dados novos.", "success")
    elif resultados:
        motivos = " ".join(f"{f}: {m}" for f, _, m in resultados)
        extra = ""
        if segmento not in ("8", "5"):
            extra = (" Não há conector público automático pra este segmento — se houver um link de "
                      "consulta pública mais abaixo, ele abre o portal oficial do tribunal pra uma busca "
                      "manual mais completa.")
        flash(f"Não encontrei o processo em nenhuma fonte automática agora. {motivos}{extra}", "warning")
    else:
        flash("Nenhuma fonte automática pública se aplica a este processo (número CNJ sem segmento "
              "coberto) — se houver um link de consulta pública mais abaixo, ele abre o portal oficial "
              "do tribunal pra uma busca manual.", "warning")

    return redirect(url_for("processos.detalhe", processo_id=processo.id))


@governanca_bp.route("/processos/<int:processo_id>/abrir-eproc/<slug>")
@login_required
def abrir_eproc_auto_envio(processo_id, slug):
    """
    Ponte de auto-envio pra consulta pública do eproc "clássico" (TJRJ,
    TJSC — PENDENCIAS.md, seção -81). NÃO é captura automática: renderiza
    uma página com um <form> oculto que se auto-envia pro site OFICIAL do
    tribunal, com os mesmos nomes de campo confirmados ao vivo no HTML
    real de cada um (ver app/utils/eproc_links.py) — exatamente o que
    aconteceria se um humano preenchesse e clicasse "Consultar" à mão.
    Quem resolve o "confirme que é humano" do Cloudflare do outro lado é
    a pessoa logada, numa aba nova; nada volta capturado pro JusControl.
    """
    from app.utils.eproc_links import formulario_post

    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)
    formulario = formulario_post(slug)
    if not formulario or not processo.numero_processo:
        abort(404)

    validado = validar_numero_cnj(processo.numero_processo, exigir_dv=False)
    numero_formatado = validado["partes"]["formatado"] if validado["valido"] else somente_digitos(processo.numero_processo)

    registrar_log(current_user, "abriu_eproc_publico_externo", "Processo", processo.id,
                  f"{slug}: {processo.numero_processo}")

    return render_template("processos/_eproc_auto_envio.html", formulario=formulario, numero=numero_formatado)


@governanca_bp.route("/processos/<int:processo_id>/abrir-projudi/<slug>")
@login_required
def abrir_projudi_auto_envio(processo_id, slug):
    """
    Mesma ideia da rota acima (abrir_eproc_auto_envio), agora pro Projudi
    (TJPR — PENDENCIAS.md, seção -85). NÃO é captura automática: renderiza
    uma página com um <form> oculto que se auto-envia pro site OFICIAL do
    tribunal, com os mesmos nomes de campo confirmados ao vivo (ver
    app/utils/projudi_links.py). Quem resolve o captcha do outro lado é a
    pessoa logada, numa aba nova; nada volta capturado pro JusControl.
    """
    from app.utils.projudi_links import formulario_projudi

    processo = db.get_or_404(Processo, processo_id)
    checar_acesso_processo_ou_403(processo)
    formulario = formulario_projudi(slug)
    if not formulario or not processo.numero_processo:
        abort(404)

    validado = validar_numero_cnj(processo.numero_processo, exigir_dv=False)
    numero_formatado = validado["partes"]["formatado"] if validado["valido"] else somente_digitos(processo.numero_processo)

    registrar_log(current_user, "abriu_projudi_publico_externo", "Processo", processo.id,
                  f"{slug}: {processo.numero_processo}")

    return render_template("processos/_eproc_auto_envio.html", formulario=formulario, numero=numero_formatado)


# ---------- Fila de intimações (seção 7.2) ----------

def _contexto_fila_intimacoes():
    """
    Reúne o contexto da fila de intimações num dict só — extraído da view
    `fila_intimacoes()` para ser reaproveitado pelo hub `painel_e_filas()`
    (simplificação de menu). `paginar()` já lê "pagina"/"por_pagina" direto
    da querystring da requisição atual, então a paginação funciona igual
    tanto na tela solteira quanto na aba do hub.
    """
    # "historico_anterior" (ver PENDENCIAS.md, seção -33) fica de fora da
    # fila de propósito — já foi revisado e regularizado em lote, não
    # precisa de ninguém tratando de novo aqui.
    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): `if not
    # current_user.is_admin: filter(...)` deixava QUALQUER admin (não só
    # o admin desenvolvedor) ver a fila de intimações de TODAS as
    # empresas do sistema — troca por `aplicar_escopo_unidade`, que já
    # implementa a regra certa das 3 camadas.
    query = aplicar_escopo_unidade(
        Prazo.query.join(Processo).filter(
            Prazo.deletado_em.is_(None),
            Prazo.status.notin_(["cumprido", "historico_anterior"]),
        ),
        Processo,
    )
    query = filtrar_processos_visiveis(query)

    # Paginação (PENDENCIAS.md, seção -47) — esta era a listagem mais
    # arriscada do sistema depois de Processos: TODO prazo em aberto do
    # escopo inteiro, sem limite, numa tela pensada pra ser a "fila de
    # trabalho do dia".
    paginacao = paginar(query.order_by(Prazo.data_vencimento))
    hoje = date.today()
    return dict(prazos=paginacao.items, paginacao=paginacao, hoje=hoje)


@governanca_bp.route("/fila-intimacoes")
@login_required
def fila_intimacoes():
    return render_template("governanca/fila_intimacoes.html", **_contexto_fila_intimacoes())


# ---------- Prazos em atenção / Prazos perdidos (cards do painel — PENDENCIAS.md, seção -100) ----------
# Antes, os cards "Prazos em atenção" e "Prazos perdidos" do painel
# (app/routes/dashboard.py) só mostravam um NÚMERO, sem nenhum jeito de
# clicar e ver a lista de verdade — pedido explícito do usuário pra
# corrigir isso. As duas rotas abaixo usam a MESMA definição de janela que
# o painel usa (`hoje` / `hoje + 5 dias` pra "em atenção"; `< hoje` pra
# "perdidos"), então o número do card e o que aparece aqui sempre batem.

@governanca_bp.route("/prazos-em-atencao")
@login_required
def prazos_em_atencao():
    hoje = date.today()
    limite_alerta = hoje + timedelta(days=5)
    query = filtrar_processos_visiveis(
        aplicar_escopo_unidade(Prazo.query.join(Processo), Processo)
    ).filter(
        Prazo.deletado_em.is_(None),
        Prazo.status == "pendente",
        Prazo.data_vencimento >= hoje,
        Prazo.data_vencimento <= limite_alerta,
    )
    paginacao = paginar(query.order_by(Prazo.data_vencimento))
    return render_template("governanca/prazos_em_atencao.html", prazos=paginacao.items,
                            paginacao=paginacao, hoje=hoje, limite_alerta=limite_alerta)


@governanca_bp.route("/prazos-perdidos")
@login_required
def prazos_perdidos():
    """Pedido explícito: "prazos perdidos deve direcionar para uma pagina
    de prazos perdidos aonde vao ter todos os prazos perdidos separados
    por processos e unidades" — em vez de uma lista só (como a fila de
    intimações), agrupa em duas camadas: Unidade -> Processo -> prazos
    perdidos daquele processo. Pra usuário comum (uma unidade só) o
    primeiro nível vira só um grupo; pra admin com várias unidades, dá
    pra ver de cara onde a perda está concentrada."""
    hoje = date.today()
    query = filtrar_processos_visiveis(
        aplicar_escopo_unidade(Prazo.query.join(Processo), Processo)
    ).filter(
        Prazo.deletado_em.is_(None),
        Prazo.status == "pendente",
        Prazo.data_vencimento < hoje,
    )
    prazos = query.order_by(Prazo.data_vencimento).all()

    grupos_por_unidade = {}
    ordem_unidades = []
    for p in prazos:
        unidade = p.processo.unidade
        if unidade.id not in grupos_por_unidade:
            grupos_por_unidade[unidade.id] = {"unidade": unidade, "processos": {}, "ordem_processos": []}
            ordem_unidades.append(unidade.id)
        grupo_unidade = grupos_por_unidade[unidade.id]
        processo = p.processo
        if processo.id not in grupo_unidade["processos"]:
            grupo_unidade["processos"][processo.id] = {"processo": processo, "prazos": []}
            grupo_unidade["ordem_processos"].append(processo.id)
        grupo_unidade["processos"][processo.id]["prazos"].append(p)

    grupos = []
    for unidade_id in ordem_unidades:
        grupo_unidade = grupos_por_unidade[unidade_id]
        processos = [grupo_unidade["processos"][pid] for pid in grupo_unidade["ordem_processos"]]
        grupos.append({
            "unidade": grupo_unidade["unidade"],
            "processos": processos,
            "total": sum(len(pr["prazos"]) for pr in processos),
        })

    return render_template("governanca/prazos_perdidos.html", grupos=grupos, total=len(prazos), hoje=hoje)


# ---------- Painel de governança (seção 8) ----------

def _contexto_painel_governanca():
    """
    Reúne todo o contexto do painel de governança num dict só — extraído
    da view `painel()` para poder ser reaproveitado pelo hub
    `painel_e_filas()` (simplificação de menu, mesmo pedido de "Minha
    empresa"/"Plataforma": juntar em abas o que antes eram itens soltos
    do menu).
    """
    hoje = date.today()
    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): mesmo problema do
    # `fila_intimacoes` acima — QUALQUER admin via os cartões "Prazos
    # fatais"/"Vencidos sem evidência" deste painel somando TODAS as
    # empresas do sistema, não só a própria.
    prazos_base = aplicar_escopo_unidade(Prazo.query.join(Processo), Processo)
    prazos_base = filtrar_processos_visiveis(prazos_base).filter(Prazo.deletado_em.is_(None))

    # "historico_anterior" (ver PENDENCIAS.md, seção -33) fica de fora das
    # três contagens abaixo de propósito — já foi revisado e regularizado
    # em lote, não é um prazo em aberto precisando de atenção.
    #
    # Paginação/escala (PENDENCIAS.md, seção -47): estes 3 números só
    # alimentam os cartões de estatística do topo do painel
    # (`|length` no template antigo) — nunca uma tabela linha a linha.
    # Antes disto, cada um carregava a lista INTEIRA de prazos só pra
    # contar o tamanho; `.count()` faz a mesma conta no próprio banco,
    # sem trazer nenhuma linha pra memória do servidor.
    prazos_7d_count = prazos_base.filter(
        Prazo.status.notin_(["cumprido", "historico_anterior"]),
        Prazo.data_vencimento.between(hoje, hoje + timedelta(days=7)),
    ).count()
    prazos_15d_count = prazos_base.filter(
        Prazo.status.notin_(["cumprido", "historico_anterior"]),
        Prazo.data_vencimento.between(hoje + timedelta(days=8), hoje + timedelta(days=15)),
    ).count()
    prazos_vencidos_sem_evidencia_count = prazos_base.filter(
        Prazo.status.notin_(["cumprido", "historico_anterior"]), Prazo.data_vencimento < hoje
    ).count()

    limite_30 = hoje - timedelta(days=30)
    limite_60 = hoje - timedelta(days=60)
    limite_90 = hoje - timedelta(days=90)
    processos_ativos = processos_q.filter(Processo.status == "ativo")
    parados_30 = processos_ativos.filter(Processo.ultima_movimentacao_em.is_(None) | (Processo.ultima_movimentacao_em <= limite_30)).count()
    parados_60 = processos_ativos.filter(Processo.ultima_movimentacao_em.is_(None) | (Processo.ultima_movimentacao_em <= limite_60)).count()
    parados_90 = processos_ativos.filter(Processo.ultima_movimentacao_em.is_(None) | (Processo.ultima_movimentacao_em <= limite_90)).count()

    distribuicao_fase = dict(processos_q.with_entities(Processo.fase, func.count(Processo.id)).group_by(Processo.fase).all())
    distribuicao_area = dict(processos_q.with_entities(Processo.area_direito, func.count(Processo.id)).group_by(Processo.area_direito).all())
    distribuicao_unidade = None
    if current_user.is_admin:
        distribuicao_unidade = dict(
            db.session.query(Unidade.nome, func.count(Processo.id))
            .join(Processo, Processo.unidade_id == Unidade.id).group_by(Unidade.nome).all()
        )

    exposicao_por_fase = dict(
        processos_q.with_entities(Processo.fase, func.coalesce(func.sum(Processo.valor_causa), 0))
        .group_by(Processo.fase).all()
    )
    exposicao_por_risco = dict(
        processos_q.with_entities(Processo.classificacao_risco, func.coalesce(func.sum(Processo.valor_causa), 0))
        .group_by(Processo.classificacao_risco).all()
    )

    limite_24h = datetime.utcnow() - timedelta(hours=24)
    tipos_criticos = ["sentenca", "decisao", "penhora", "bloqueio", "audiencia", "intimacao_pessoal", "auto_de_infracao"]
    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): mesmo problema —
    # QUALQUER admin via movimentação crítica das últimas 24h de TODAS as
    # empresas do sistema.
    movimentacoes_criticas = aplicar_escopo_unidade(
        Movimentacao.query.join(Processo).filter(
            Movimentacao.criado_em >= limite_24h, Movimentacao.deletado_em.is_(None),
        ),
        Processo,
    ).order_by(Movimentacao.data.desc()).limit(20).all()

    # Esta lista, diferente das 3 contagens acima, é mesmo renderizada
    # linha a linha no template — aqui o risco real é carregar milhares
    # de processo numa tabela só. `limitar_com_total` mostra os N mais
    # recentes (o teto padrão, 50) e o total de verdade, pra tela poder
    # avisar "mostrando 50 de 312" em vez de fingir que cabia tudo.
    processos_nao_monitoraveis, total_nao_monitoraveis = limitar_com_total(
        processos_q.filter(Processo.monitoravel.is_(False)).order_by(Processo.criado_em.desc())
    )

    return dict(
        hoje=hoje,
        prazos_7d_count=prazos_7d_count, prazos_15d_count=prazos_15d_count,
        prazos_vencidos_sem_evidencia_count=prazos_vencidos_sem_evidencia_count,
        parados_30=parados_30, parados_60=parados_60, parados_90=parados_90,
        distribuicao_fase=distribuicao_fase, distribuicao_area=distribuicao_area, distribuicao_unidade=distribuicao_unidade,
        exposicao_por_fase=exposicao_por_fase, exposicao_por_risco=exposicao_por_risco,
        total_nao_monitoraveis=total_nao_monitoraveis,
        movimentacoes_criticas=movimentacoes_criticas, processos_nao_monitoraveis=processos_nao_monitoraveis,
    )


@governanca_bp.route("/painel")
@login_required
def painel():
    return render_template("governanca/painel.html", **_contexto_painel_governanca())


# ---------- Métricas de governança (seção 9) ----------

def _contexto_metricas():
    """
    Reúne todo o contexto da tela de métricas de governança num dict só —
    extraído da view `metricas()` para poder ser reaproveitado também pelo
    hub `painel_metricas()` (fusão de "Métricas" + "Relatório semanal" em
    abas, simplificação de menu), sem duplicar nenhuma consulta/regra de
    negócio entre as duas rotas.
    """
    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): mesmo problema —
    # QUALQUER admin via a taxa de cumprido/perdido somando TODAS as
    # empresas do sistema.
    prazos_q = aplicar_escopo_unidade(Prazo.query.join(Processo), Processo)
    prazos_q = prazos_q.filter(Prazo.deletado_em.is_(None))

    total_prazos_finalizados = prazos_q.filter(Prazo.status.in_(["cumprido", "perdido"])).count()
    cumpridos = prazos_q.filter(Prazo.status == "cumprido").count()
    perdidos = prazos_q.filter(Prazo.status == "perdido").count()
    taxa_cumprimento = (cumpridos / total_prazos_finalizados * 100) if total_prazos_finalizados else None

    prazos_perdidos_por_processo = (
        prazos_q.filter(Prazo.status == "perdido")
        .with_entities(Processo.numero_processo, Processo.id, func.count(Prazo.id))
        .group_by(Processo.id, Processo.numero_processo)
        .order_by(func.count(Prazo.id).desc()).limit(15).all()
    )

    # Tempo médio entre publicação e protocolo (só calculável quando há
    # publicacao vinculada + prazo cumprido com evidência)
    cumpridos_com_publicacao = prazos_q.filter(
        Prazo.status == "cumprido", Prazo.publicacao_id.isnot(None), Prazo.cumprido_em.isnot(None)
    ).join(Publicacao, Prazo.publicacao_id == Publicacao.id).all()
    if cumpridos_com_publicacao:
        deltas = [
            (p.cumprido_em.date() - p.publicacao.data_publicacao).days
            for p in cumpridos_com_publicacao if p.publicacao.data_publicacao
        ]
        tempo_medio_publicacao_protocolo = sum(deltas) / len(deltas) if deltas else None
    else:
        tempo_medio_publicacao_protocolo = None

    hoje = date.today()
    processos_ativos = processos_q.filter(Processo.status == "ativo").all()
    idades = [(hoje - p.data_distribuicao).days for p in processos_ativos if p.data_distribuicao]
    idade_media_dias = sum(idades) / len(idades) if idades else None

    total_carteira = processos_q.count()
    monitoraveis_automatico = processos_q.filter(
        Processo.forma_acompanhamento == "automatico", Processo.monitoravel.is_(True)
    ).count()
    cobertura_pct = (monitoraveis_automatico / total_carteira * 100) if total_carteira else None

    limite_7d = datetime.utcnow() - timedelta(days=7)
    logs_recentes = LogCaptura.query.filter(LogCaptura.executado_em >= limite_7d).all()
    if logs_recentes:
        sucesso_7d = len([l for l in logs_recentes if l.status == "sucesso"])
        pct_captura_saudavel = sucesso_7d / len(logs_recentes) * 100
    else:
        pct_captura_saudavel = None  # sem execuções de captura ainda — ver captura_conectores.py

    # ---- BI: taxa de sucesso, ganhos/perdas, tempo médio de duração (paridade item 4) ----
    processos_encerrados = processos_q.filter(Processo.status == "encerrado").all()
    distribuicao_desfecho = {}
    for p in processos_encerrados:
        chave = p.desfecho or "sem_desfecho_registrado"
        distribuicao_desfecho[chave] = distribuicao_desfecho.get(chave, 0) + 1

    ganhos = distribuicao_desfecho.get("ganho", 0)
    perdas = distribuicao_desfecho.get("perda", 0)
    acordos = distribuicao_desfecho.get("acordo", 0)
    total_com_desfecho_definido = sum(v for k, v in distribuicao_desfecho.items() if k != "sem_desfecho_registrado")
    # Taxa de sucesso considera ganho + acordo como resultado favorável, sobre
    # o total de processos com desfecho já registrado (nunca sobre a carteira
    # inteira, que incluiria processos ainda em curso e distorceria o número).
    taxa_sucesso = ((ganhos + acordos) / total_com_desfecho_definido * 100) if total_com_desfecho_definido else None

    duracoes = [
        (p.data_encerramento - p.data_distribuicao).days
        for p in processos_encerrados
        if p.data_encerramento and p.data_distribuicao
    ]
    tempo_medio_duracao_dias = sum(duracoes) / len(duracoes) if duracoes else None

    return dict(
        taxa_cumprimento=taxa_cumprimento, cumpridos=cumpridos, perdidos=perdidos,
        total_prazos_finalizados=total_prazos_finalizados,
        prazos_perdidos_por_processo=prazos_perdidos_por_processo,
        tempo_medio_publicacao_protocolo=tempo_medio_publicacao_protocolo,
        idade_media_dias=idade_media_dias, total_carteira=total_carteira,
        cobertura_pct=cobertura_pct, monitoraveis_automatico=monitoraveis_automatico,
        pct_captura_saudavel=pct_captura_saudavel,
        taxa_sucesso=taxa_sucesso, ganhos=ganhos, perdas=perdas, acordos=acordos,
        total_processos_encerrados=len(processos_encerrados),
        total_com_desfecho_definido=total_com_desfecho_definido,
        tempo_medio_duracao_dias=tempo_medio_duracao_dias,
    )


@governanca_bp.route("/metricas")
@login_required
def metricas():
    return render_template("governanca/metricas.html", **_contexto_metricas())


# ---------- Produtividade por advogado (item 2 do briefing de paridade) ----------

def _contexto_produtividade():
    """
    Ranking de produtividade individual — item 2 ("controle de
    produtividade") do briefing de paridade. Cada linha soma o que está
    sob responsabilidade daquele usuário: prazos, tarefas e horas
    apontadas (quando o timesheet estiver em uso). Extraído da view
    `produtividade()` para ser reaproveitado pelo hub `painel_e_filas()`
    (simplificação de menu).
    """
    from app.models import Usuario, Tarefa, Apontamento

    if current_user.is_admin_desenvolvedor:
        usuarios_q = Usuario.query.filter_by(ativo=True)
    elif current_user.is_admin:
        usuarios_q = Usuario.query.join(Unidade).filter(
            Unidade.empresa_id == current_user.empresa_id_atual, Usuario.ativo.is_(True)
        )
    else:
        usuarios_q = Usuario.query.filter_by(unidade_id=current_user.unidade_id, ativo=True)

    hoje = date.today()
    linhas = []
    for u in usuarios_q.order_by(Usuario.nome).all():
        prazos_usuario = Prazo.query.filter(Prazo.responsavel_id == u.id, Prazo.deletado_em.is_(None))
        cumpridos = prazos_usuario.filter(Prazo.status == "cumprido").count()
        perdidos = prazos_usuario.filter(Prazo.status == "perdido").count()
        finalizados = cumpridos + perdidos
        taxa = (cumpridos / finalizados * 100) if finalizados else None

        tarefas_concluidas = Tarefa.query.filter(
            Tarefa.responsavel_id == u.id, Tarefa.status == "concluida"
        ).count()
        tarefas_atrasadas = Tarefa.query.filter(
            Tarefa.responsavel_id == u.id, Tarefa.status.in_(["pendente", "em_andamento"]),
            Tarefa.data_vencimento.isnot(None), Tarefa.data_vencimento < hoje,
        ).count()

        horas_total = db.session.query(func.coalesce(func.sum(Apontamento.horas), 0)).filter(
            Apontamento.usuario_id == u.id
        ).scalar()

        if finalizados == 0 and tarefas_concluidas == 0 and tarefas_atrasadas == 0 and not horas_total:
            continue  # não polui o ranking com usuário sem nenhuma atividade registrada

        linhas.append(dict(
            usuario=u, cumpridos=cumpridos, perdidos=perdidos, taxa=taxa,
            tarefas_concluidas=tarefas_concluidas, tarefas_atrasadas=tarefas_atrasadas,
            horas_total=horas_total,
        ))

    linhas.sort(key=lambda l: (l["taxa"] if l["taxa"] is not None else -1), reverse=True)

    return dict(linhas=linhas)


@governanca_bp.route("/produtividade")
@login_required
def produtividade():
    return render_template("governanca/produtividade.html", **_contexto_produtividade())


# ---------- Contingenciamento jurídico formal (item 7 do briefing de paridade) ----------

def _contexto_contingenciamento():
    """
    Provisão de contingência: valor da causa × percentual da classificação
    (provável=100%, possível=50%, remoto=0%, ou percentual manual por
    processo) — não apenas soma bruta por categoria de risco operacional,
    que é o que `classificacao_risco` já fazia no painel de governança.
    Extraído da view `contingenciamento()` para ser reaproveitado pelo hub
    `painel_e_filas()` (simplificação de menu).
    """
    processos_q = aplicar_escopo_unidade(Processo.query, Processo).filter(Processo.status == "ativo")

    totais_por_classificacao = {"provavel": Decimal("0"), "possivel": Decimal("0"), "remoto": Decimal("0"), "sem_classificacao": Decimal("0")}
    contagem_por_classificacao = {"provavel": 0, "possivel": 0, "remoto": 0, "sem_classificacao": 0}
    exposicao_total = Decimal("0")
    provisao_total = Decimal("0")
    processos_classificados = []

    for p in processos_q.all():
        if p.valor_causa is None:
            continue
        exposicao_total += p.valor_causa
        chave = p.classificacao_contingencia or "sem_classificacao"
        contagem_por_classificacao[chave] = contagem_por_classificacao.get(chave, 0) + 1
        provisionado = p.valor_provisionado or Decimal("0")
        totais_por_classificacao[chave] = totais_por_classificacao.get(chave, Decimal("0")) + provisionado
        provisao_total += provisionado
        if p.classificacao_contingencia:
            processos_classificados.append(p)

    processos_classificados.sort(key=lambda p: p.valor_provisionado or Decimal("0"), reverse=True)

    return dict(
        linhas=processos_classificados[:30],
        totais_por_classificacao=totais_por_classificacao,
        contagem_por_classificacao=contagem_por_classificacao,
        exposicao_total=exposicao_total, provisao_total=provisao_total,
    )


@governanca_bp.route("/contingenciamento")
@login_required
def contingenciamento():
    return render_template("governanca/contingenciamento.html", **_contexto_contingenciamento())


# ---------- Verificação de conflito de interesses (PENDENCIAS.md, seção -42) ----------

def _contexto_verificacao_conflitos():
    """
    Varredura completa da empresa inteira (todas as unidades) atrás de
    todo par (cliente, processo de OUTRO cliente cuja parte_contraria bate
    com o nome desse cliente) — o conflito de interesses clássico da
    advocacia: representar alguém e, ao mesmo tempo, ser adverso a essa
    mesma pessoa/empresa em outro caso do escritório. Só admin (decisão de
    aceitar/recusar caso por conflito é tipicamente do sócio/gestor, não
    de qualquer usuário — mesmo padrão de acesso de admin/relatorios).

    Restrito a admin, mas nunca cruza fronteira de empresa: admin de
    empresa vê só a própria empresa; admin desenvolvedor (multi-empresa)
    não tem uma "empresa atual" fixa, então esta tela pede pra escolher
    uma antes de rodar a varredura, em vez de arriscar misturar dado de
    escritórios clientes diferentes.

    Extraído da view `verificacao_conflitos()` para ser reaproveitado pelo
    hub `regras_e_parametros()` (simplificação de menu).
    """
    from app.models import Empresa

    empresa_id = request.args.get("empresa_id", type=int)
    empresas_para_escolher = None
    conflitos = []

    if current_user.is_admin_desenvolvedor:
        empresas_para_escolher = Empresa.query.filter_by(dono_da_plataforma=False).order_by(Empresa.nome).all()
        if empresa_id:
            conflitos = varrer_conflitos_da_empresa(empresa_id)
    else:
        empresa_id = current_user.empresa_id_atual
        conflitos = varrer_conflitos_da_empresa(empresa_id)

    return dict(conflitos=conflitos, empresas_para_escolher=empresas_para_escolher, empresa_id=empresa_id)


@governanca_bp.route("/conflitos")
@login_required
@apenas_admin
def verificacao_conflitos():
    return render_template("governanca/conflitos.html", **_contexto_verificacao_conflitos())


# ---------- Export para Data Lake (seção 12) ----------

@governanca_bp.route("/export/<entidade>.csv")
@login_required
def exportar_csv(entidade):
    """
    Export tabular (CSV) autenticado de processos/movimentações/decisões/
    prazos, para o escritório consumir no próprio Data Lake (seção 12:
    "o sistema deve ser fonte, não ilha"). Exige login (equivalente a
    "API de leitura autenticada" em uma versão simples, síncrona —
    uma API real com token de serviço fica fácil de adicionar depois,
    mas não foi criada agora para não inventar um mecanismo de auth
    novo sem alinhar com você primeiro).
    """
    mapeamento = {
        "processos": (Processo, ["id", "numero_processo", "area_direito", "fase", "estado_negocio_atual",
                                  "status", "status_comercial", "unidade_id", "cliente_id", "valor_causa",
                                  "data_distribuicao", "monitoravel", "forma_acompanhamento"]),
        "movimentacoes": (Movimentacao, ["id", "processo_id", "data", "codigo_tpu", "estado_negocio_resultante",
                                          "origem_captura", "triagem_pendente"]),
        "decisoes": (Decisao, ["id", "processo_id", "tipo", "orgao_julgador", "magistrado_relator", "data", "resultado"]),
        "prazos": (Prazo, ["id", "processo_id", "descricao", "data_vencimento", "status", "calculo_automatico",
                            "responsavel_id"]),
    }
    if entidade not in mapeamento:
        flash("Entidade de export desconhecida.", "danger")
        return redirect(url_for("governanca.painel"))

    modelo, campos = mapeamento[entidade]
    query = modelo.query
    if hasattr(modelo, "processo") and not current_user.is_admin:
        query = query.join(Processo).filter(Processo.unidade_id == current_user.unidade_id)
    elif modelo is Processo and not current_user.is_admin:
        query = query.filter(Processo.unidade_id == current_user.unidade_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(campos)
    for registro in query.limit(20000).all():
        writer.writerow([getattr(registro, campo) for campo in campos])

    registrar_log(current_user, "export_datalake", entidade, None, f"{query.count()} registros")
    db.session.commit()

    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={entidade}.csv"},
    )


# ---------- Relatório semanal (seção 10) ----------
# Geração de conteúdo pronta. O ENVIO automático por e-mail (ou WhatsApp via
# Evolution API) está bloqueado — depende de credenciais SMTP/instância que
# não temos aqui (ver README, seção "Bloqueado"). Este preview permite ver
# exatamente o que seria enviado, e serve de base para plugar o envio assim
# que houver credencial e um agendador (cron/Celery beat) no servidor.

def _contexto_relatorio_semanal():
    """
    Mesma extração de `_contexto_metricas()` acima, para a view do preview
    do relatório semanal — reaproveitada pelo hub `painel_metricas()`.
    """
    hoje = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)
    semana_passada_inicio = inicio_semana - timedelta(days=7)

    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): isto usava
    # `if not current_user.is_admin: filter(unidade_id == ...)`, o que só
    # restringia usuário comum — QUALQUER admin (inclusive admin de uma
    # empresa cliente comum, não só o admin desenvolvedor) via o preview do
    # relatório semanal prazos de TODAS as empresas do sistema, não só da
    # própria. `aplicar_escopo_unidade` já implementa a regra certa das 3
    # camadas.
    prazos_q = aplicar_escopo_unidade(
        Prazo.query.join(Processo).filter(Prazo.deletado_em.is_(None)), Processo
    )

    prazos_da_semana = prazos_q.filter(
        Prazo.data_vencimento.between(inicio_semana, fim_semana), Prazo.status != "cumprido"
    ).order_by(Prazo.data_vencimento).all()

    prazos_perdidos_semana_passada = prazos_q.filter(
        Prazo.status == "perdido",
        Prazo.data_vencimento.between(semana_passada_inicio, inicio_semana - timedelta(days=1)),
    ).all()

    processos_q = aplicar_escopo_unidade(Processo.query, Processo)
    limite_30 = datetime.utcnow() - timedelta(days=30)
    processos_parados = processos_q.filter(
        Processo.status == "ativo",
        or_(Processo.ultima_movimentacao_em.is_(None), Processo.ultima_movimentacao_em <= limite_30),
    ).limit(20).all()

    inicio_semana_dt = datetime.combine(inicio_semana, datetime.min.time())
    # CORREÇÃO DE SEGURANÇA (PENDENCIAS.md, seção -54): mesmo padrão —
    # QUALQUER admin (não só o admin desenvolvedor) recebia movimentações
    # de TODAS as empresas do sistema no preview do relatório semanal.
    movimentacoes_semana = aplicar_escopo_unidade(
        Movimentacao.query.join(Processo).filter(
            Movimentacao.criado_em >= inicio_semana_dt, Movimentacao.deletado_em.is_(None),
        ),
        Processo,
    )
    movimentacoes_semana = movimentacoes_semana.order_by(Movimentacao.data.desc()).limit(30).all()

    return dict(
        inicio_semana=inicio_semana, fim_semana=fim_semana,
        prazos_da_semana=prazos_da_semana, prazos_perdidos_semana_passada=prazos_perdidos_semana_passada,
        processos_parados=processos_parados, movimentacoes_semana=movimentacoes_semana,
    )


@governanca_bp.route("/relatorio-semanal/preview")
@login_required
def relatorio_semanal_preview():
    return render_template("governanca/relatorio_semanal_preview.html", **_contexto_relatorio_semanal())


@governanca_bp.route("/metricas-e-relatorio")
@login_required
def painel_metricas():
    """
    Hub único de "Métricas" + "Relatório semanal (preview)" em duas abas —
    simplificação de menu, a pedido explícito. Reaproveita o mesmo contexto
    das duas views originais (_contexto_metricas / _contexto_relatorio_semanal),
    que continuam existindo e funcionando normalmente sozinhas (nenhuma
    lógica de negócio mudou, só a navegação).
    """
    aba_inicial = "relatorio" if request.args.get("tab") == "relatorio" else "metricas"
    contexto = {}
    contexto.update(_contexto_metricas())
    contexto.update(_contexto_relatorio_semanal())
    contexto["aba_inicial"] = aba_inicial
    return render_template("governanca/painel_metricas.html", **contexto)


# ---------- Regras de próxima ação (seção 7.1) e mapa código TPU → estado (seção 6) ----------
# Interface de configuração do motor de prazos automático e da tradução de
# movimentação para estado de negócio (app/utils/prazos_engine.py e
# app/utils/estado_processual_engine.py). Sem nenhuma regra/mapeamento
# cadastrado aqui, os dois motores continuam funcionando, mas todo ato
# capturado cai no caminho genérico ("análise necessária", prazo provisório
# de 5 dias, ou "triagem pendente") por falta de regra específica — esta
# tela é o que efetivamente liga o "lançamento automático de prazo" na
# prática, para os tipos de ato que o escritório realmente enfrenta.
#
# Acesso restrito a admin: são regras de negócio jurídicas que valem para
# toda a carteira, não configuração de uso pessoal. E, de propósito, o
# sistema NÃO vem com nenhum prazo legal pré-cadastrado nem sugerido aqui —
# cadastrar um prazo errado é grave (risco real de perda de prazo). Cabe ao
# advogado responsável validar e digitar o prazo de cada tipo de ato
# conforme a legislação e o rito aplicável ao caso (CPC, Lei de Execução
# Fiscal etc.).

def _contexto_regras_proxima_acao():
    """
    Extraído da view `regras_proxima_acao_lista()` para ser reaproveitado
    pelo hub `regras_e_parametros()` (simplificação de menu).
    """
    regras = RegraProximaAcao.query.order_by(RegraProximaAcao.ativo.desc(), RegraProximaAcao.ato_capturado).all()
    return dict(regras=regras)


@governanca_bp.route("/regras-proxima-acao")
@login_required
@apenas_admin
def regras_proxima_acao_lista():
    return render_template("governanca/regras_proxima_acao_lista.html", **_contexto_regras_proxima_acao())


@governanca_bp.route("/regras-proxima-acao/nova", methods=["GET", "POST"])
@login_required
@apenas_admin
def nova_regra_proxima_acao():
    if request.method == "POST":
        prazo_base_dias = request.form.get("prazo_base_dias") or None
        regra = RegraProximaAcao(
            ato_capturado=request.form["ato_capturado"].strip(),
            codigo_tpu=request.form.get("codigo_tpu") or None,
            acao_exigida=request.form["acao_exigida"].strip(),
            prazo_base_dias=int(prazo_base_dias) if prazo_base_dias else None,
            unidade_prazo=request.form.get("unidade_prazo") or "dias_uteis",
            observacao_prazo=request.form.get("observacao_prazo") or None,
            responsavel_sugerido_papel=request.form.get("responsavel_sugerido_papel") or None,
            ativo=True,
        )
        db.session.add(regra)
        registrar_log(current_user, "criou", "RegraProximaAcao", None, regra.ato_capturado)
        db.session.commit()
        flash("Regra de próxima ação cadastrada.", "success")
        return redirect(url_for("governanca.regras_proxima_acao_lista"))

    # Atalho "cadastrar regra" a partir da tela do processo (aba Prazos, ver
    # detalhe.html) — chega com o texto do ato já preenchido via querystring.
    prefill = {
        "ato_capturado": request.args.get("ato_capturado", ""),
        "codigo_tpu": request.args.get("codigo_tpu", ""),
    }
    return render_template("governanca/regra_proxima_acao_form.html", regra=None, prefill=prefill)


@governanca_bp.route("/regras-proxima-acao/<int:regra_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_regra_proxima_acao(regra_id):
    regra = db.get_or_404(RegraProximaAcao, regra_id)
    if request.method == "POST":
        prazo_base_dias = request.form.get("prazo_base_dias") or None
        regra.ato_capturado = request.form["ato_capturado"].strip()
        regra.codigo_tpu = request.form.get("codigo_tpu") or None
        regra.acao_exigida = request.form["acao_exigida"].strip()
        regra.prazo_base_dias = int(prazo_base_dias) if prazo_base_dias else None
        regra.unidade_prazo = request.form.get("unidade_prazo") or "dias_uteis"
        regra.observacao_prazo = request.form.get("observacao_prazo") or None
        regra.responsavel_sugerido_papel = request.form.get("responsavel_sugerido_papel") or None
        registrar_log(current_user, "editou", "RegraProximaAcao", regra.id, regra.ato_capturado)
        db.session.commit()
        flash("Regra atualizada.", "success")
        return redirect(url_for("governanca.regras_proxima_acao_lista"))

    return render_template("governanca/regra_proxima_acao_form.html", regra=regra)


@governanca_bp.route("/regras-proxima-acao/<int:regra_id>/alternar-ativo", methods=["POST"])
@login_required
@apenas_admin
def alternar_regra_proxima_acao(regra_id):
    regra = db.get_or_404(RegraProximaAcao, regra_id)
    regra.ativo = not regra.ativo
    registrar_log(current_user, "ativou" if regra.ativo else "desativou", "RegraProximaAcao", regra.id, regra.ato_capturado)
    db.session.commit()
    flash(f"Regra {'ativada' if regra.ativo else 'desativada'}.", "info")
    return redirect(request.referrer or url_for("governanca.regras_proxima_acao_lista"))


# Biblioteca de modelos de peças (item 10 da lista de pipeline de IA jurídica
# trazida pelo usuário — PENDENCIAS.md, seção -107): "biblioteca de modelos
# por tipo de peça e área, com estilo e cláusulas do escritório aprendidos
# das peças anteriores". Mesmo padrão de CRUD admin-only das regras de
# próxima ação acima, mas escopado por EMPRESA (não é regra global de
# tribunal, é o jeito de escrever de UM escritório específico — mesma
# decisão de design do timbrado, ver app/utils/timbrado.py) — cada empresa
# só vê e edita os próprios modelos, nunca os de outro escritório cliente.

def _contexto_modelos_peca():
    """
    Extraído da view `modelos_peca_lista()` para ser reaproveitado pelo
    hub `regras_e_parametros()` (simplificação de menu).
    """
    modelos = (ModeloPeca.query.filter_by(empresa_id=current_user.empresa_id_atual)
               .order_by(ModeloPeca.ativo.desc(), ModeloPeca.tipo_peca, ModeloPeca.nome).all())
    return dict(modelos=modelos)


@governanca_bp.route("/modelos-peca")
@login_required
@apenas_admin
def modelos_peca_lista():
    return render_template("governanca/modelos_peca_lista.html", **_contexto_modelos_peca())


@governanca_bp.route("/modelos-peca/novo", methods=["GET", "POST"])
@login_required
@apenas_admin
def novo_modelo_peca():
    if request.method == "POST":
        modelo = ModeloPeca(
            empresa_id=current_user.empresa_id_atual,
            nome=request.form["nome"].strip(),
            tipo_peca=request.form["tipo_peca"].strip().lower().replace(" ", "_"),
            area_direito=request.form.get("area_direito", "").strip() or None,
            conteudo=request.form["conteudo"].strip(),
            criado_por_id=current_user.id,
            ativo=True,
        )
        db.session.add(modelo)
        registrar_log(current_user, "criou", "ModeloPeca", None, modelo.nome)
        db.session.commit()
        flash("Modelo de peça cadastrado — passa a ser aplicado automaticamente nas próximas gerações "
              "que casarem este tipo de peça e área.", "success")
        return redirect(url_for("governanca.modelos_peca_lista"))

    return render_template("governanca/modelo_peca_form.html", modelo=None)


@governanca_bp.route("/modelos-peca/<int:modelo_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_modelo_peca(modelo_id):
    modelo = db.get_or_404(ModeloPeca, modelo_id)
    if modelo.empresa_id != current_user.empresa_id_atual:
        abort(404)
    if request.method == "POST":
        modelo.nome = request.form["nome"].strip()
        modelo.tipo_peca = request.form["tipo_peca"].strip().lower().replace(" ", "_")
        modelo.area_direito = request.form.get("area_direito", "").strip() or None
        modelo.conteudo = request.form["conteudo"].strip()
        registrar_log(current_user, "editou", "ModeloPeca", modelo.id, modelo.nome)
        db.session.commit()
        flash("Modelo atualizado.", "success")
        return redirect(url_for("governanca.modelos_peca_lista"))

    return render_template("governanca/modelo_peca_form.html", modelo=modelo)


@governanca_bp.route("/modelos-peca/<int:modelo_id>/alternar-ativo", methods=["POST"])
@login_required
@apenas_admin
def alternar_modelo_peca(modelo_id):
    modelo = db.get_or_404(ModeloPeca, modelo_id)
    if modelo.empresa_id != current_user.empresa_id_atual:
        abort(404)
    modelo.ativo = not modelo.ativo
    registrar_log(current_user, "ativou" if modelo.ativo else "desativou", "ModeloPeca", modelo.id, modelo.nome)
    db.session.commit()
    flash(f"Modelo {'ativado' if modelo.ativo else 'desativado'}.", "info")
    return redirect(request.referrer or url_for("governanca.modelos_peca_lista"))


@governanca_bp.route("/modelos-peca/<int:modelo_id>/excluir", methods=["POST"])
@login_required
@apenas_admin
def excluir_modelo_peca(modelo_id):
    modelo = db.get_or_404(ModeloPeca, modelo_id)
    if modelo.empresa_id != current_user.empresa_id_atual:
        abort(404)
    nome = modelo.nome
    db.session.delete(modelo)
    registrar_log(current_user, "excluiu", "ModeloPeca", modelo_id, nome)
    db.session.commit()
    flash("Modelo excluído.", "info")
    return redirect(request.referrer or url_for("governanca.modelos_peca_lista"))


# Tabela de custas (item 9 da lista de pipeline de IA jurídica trazida pelo
# usuário — PENDENCIAS.md, seção -108): "Base de cálculo, valor da causa,
# custas, preparo e guias quando aplicável, com memória de cálculo aberta
# para conferência." Mesmo padrão de CRUD admin-only GLOBAL (não por
# empresa) de RegraProximaAcao/MapaEstadoTPU acima — é regra de tribunal
# (fato objetivo da lei de custas), não estilo do escritório.

def _contexto_tabela_custas():
    """
    Extraído da view `tabela_custas_lista()` para ser reaproveitado pelo
    hub `regras_e_parametros()` (simplificação de menu).
    """
    linhas = (TabelaCustas.query
              .order_by(TabelaCustas.tribunal, TabelaCustas.tipo_custa, TabelaCustas.faixa_ate.asc().nullslast())
              .all())
    return dict(linhas=linhas)


@governanca_bp.route("/tabela-custas")
@login_required
@apenas_admin
def tabela_custas_lista():
    return render_template("governanca/tabela_custas_lista.html", **_contexto_tabela_custas())


@governanca_bp.route("/tabela-custas/carregar-padrao-tjsp", methods=["POST"])
@login_required
@apenas_admin
def carregar_tabela_padrao_tjsp():
    """
    Insere o catálogo pronto de TABELA_PADRAO_TJSP (ver
    app/utils/calculo_custas.py — pesquisado em fonte pública, com data de
    referência e base legal em cada linha) — idempotente: pula qualquer
    combinação tribunal+tipo_custa+descricao que já exista, nunca duplica
    nem sobrescreve uma linha que o admin já tenha editado à mão.
    """
    existentes = {(t, tc, d) for (t, tc, d) in
                  db.session.query(TabelaCustas.tribunal, TabelaCustas.tipo_custa, TabelaCustas.descricao).all()}
    inseridas = 0
    for linha in TABELA_PADRAO_TJSP:
        chave = (linha["tribunal"], linha["tipo_custa"], linha["descricao"])
        if chave in existentes:
            continue
        db.session.add(TabelaCustas(**linha, ativo=True))
        inseridas += 1
    if inseridas:
        registrar_log(current_user, "carregou_tabela_padrao", "TabelaCustas", None, f"TJSP ({inseridas} linhas)")
        db.session.commit()
        flash(f"{inseridas} linha(s) da tabela padrão do TJSP carregada(s) — confira os valores e a base legal "
              "de cada uma antes de usar em cobrança real (UFESP e lei podem ter mudado desde a pesquisa).",
              "success")
    else:
        flash("A tabela padrão do TJSP já estava carregada (nenhuma linha nova).", "info")
    return redirect(request.referrer or url_for("governanca.tabela_custas_lista"))


@governanca_bp.route("/tabela-custas/nova", methods=["GET", "POST"])
@login_required
@apenas_admin
def nova_linha_custas():
    if request.method == "POST":
        linha = TabelaCustas(
            tribunal=request.form["tribunal"].strip().upper(),
            tipo_custa=request.form["tipo_custa"].strip().lower().replace(" ", "_"),
            descricao=request.form["descricao"].strip(),
            percentual=Decimal(request.form["percentual"].replace(",", ".")) if request.form.get("percentual") else None,
            valor_fixo=Decimal(request.form["valor_fixo"].replace(",", ".")) if request.form.get("valor_fixo") else None,
            valor_minimo=Decimal(request.form["valor_minimo"].replace(",", ".")) if request.form.get("valor_minimo") else None,
            valor_maximo=Decimal(request.form["valor_maximo"].replace(",", ".")) if request.form.get("valor_maximo") else None,
            faixa_ate=Decimal(request.form["faixa_ate"].replace(",", ".")) if request.form.get("faixa_ate") else None,
            observacao=request.form.get("observacao", "").strip() or None,
            ativo=True,
        )
        db.session.add(linha)
        registrar_log(current_user, "criou", "TabelaCustas", None, f"{linha.tribunal}/{linha.tipo_custa}")
        db.session.commit()
        flash("Linha de custas cadastrada.", "success")
        return redirect(url_for("governanca.tabela_custas_lista"))

    return render_template("governanca/tabela_custas_form.html", linha=None)


@governanca_bp.route("/tabela-custas/<int:linha_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_linha_custas(linha_id):
    linha = db.get_or_404(TabelaCustas, linha_id)
    if request.method == "POST":
        linha.tribunal = request.form["tribunal"].strip().upper()
        linha.tipo_custa = request.form["tipo_custa"].strip().lower().replace(" ", "_")
        linha.descricao = request.form["descricao"].strip()
        linha.percentual = Decimal(request.form["percentual"].replace(",", ".")) if request.form.get("percentual") else None
        linha.valor_fixo = Decimal(request.form["valor_fixo"].replace(",", ".")) if request.form.get("valor_fixo") else None
        linha.valor_minimo = Decimal(request.form["valor_minimo"].replace(",", ".")) if request.form.get("valor_minimo") else None
        linha.valor_maximo = Decimal(request.form["valor_maximo"].replace(",", ".")) if request.form.get("valor_maximo") else None
        linha.faixa_ate = Decimal(request.form["faixa_ate"].replace(",", ".")) if request.form.get("faixa_ate") else None
        linha.observacao = request.form.get("observacao", "").strip() or None
        registrar_log(current_user, "editou", "TabelaCustas", linha.id, f"{linha.tribunal}/{linha.tipo_custa}")
        db.session.commit()
        flash("Linha atualizada.", "success")
        return redirect(url_for("governanca.tabela_custas_lista"))

    return render_template("governanca/tabela_custas_form.html", linha=linha)


@governanca_bp.route("/tabela-custas/<int:linha_id>/alternar-ativo", methods=["POST"])
@login_required
@apenas_admin
def alternar_linha_custas(linha_id):
    linha = db.get_or_404(TabelaCustas, linha_id)
    linha.ativo = not linha.ativo
    registrar_log(current_user, "ativou" if linha.ativo else "desativou", "TabelaCustas", linha.id,
                  f"{linha.tribunal}/{linha.tipo_custa}")
    db.session.commit()
    flash(f"Linha {'ativada' if linha.ativo else 'desativada'}.", "info")
    return redirect(request.referrer or url_for("governanca.tabela_custas_lista"))


@governanca_bp.route("/tabela-custas/<int:linha_id>/excluir", methods=["POST"])
@login_required
@apenas_admin
def excluir_linha_custas(linha_id):
    linha = db.get_or_404(TabelaCustas, linha_id)
    rotulo = f"{linha.tribunal}/{linha.tipo_custa}"
    db.session.delete(linha)
    registrar_log(current_user, "excluiu", "TabelaCustas", linha_id, rotulo)
    db.session.commit()
    flash("Linha excluída.", "info")
    return redirect(request.referrer or url_for("governanca.tabela_custas_lista"))


def fila_triagem_agrupada(limite_processos_por_grupo=5):
    """
    Agrupa as movimentações com `triagem_pendente=True` (sem mapeamento de
    estado cadastrado — ver estado_processual_engine.traduzir_movimentacao)
    por código TPU (ou, quando o código não veio preenchido, pelos
    primeiros 60 caracteres do texto do ato) — pra virar uma lista
    ACIONÁVEL na tela de Mapa de estado, em vez de só um número. Sem isso,
    "23 movimentações aguardando triagem" cresce mas ninguém enxerga COM O
    QUE ele é feito nem em quantos/quais processos aparece, pra decidir o
    que vale a pena cadastrar primeiro.

    Devolve uma lista ordenada por quantidade (decrescente) de dicts:
    {"codigo_tpu", "exemplo_texto", "quantidade", "processos": [Processo,...]}
    — "processos" limitado a `limite_processos_por_grupo` processos
    distintos por grupo (só pra não estourar a tela quando um código
    genérico aparece em centenas de processos; a quantidade total continua
    contando certo).
    """
    pendentes = Movimentacao.query.filter_by(triagem_pendente=True).order_by(Movimentacao.data.desc()).all()
    grupos = {}
    for mov in pendentes:
        chave = mov.codigo_tpu or (mov.texto_integral or "")[:60].lower()
        grupo = grupos.setdefault(chave, {
            "codigo_tpu": mov.codigo_tpu,
            "exemplo_texto": mov.texto_integral,
            "quantidade": 0,
            "processos": [],
            "_processo_ids_vistos": set(),
        })
        grupo["quantidade"] += 1
        if mov.processo_id not in grupo["_processo_ids_vistos"] and len(grupo["processos"]) < limite_processos_por_grupo:
            grupo["_processo_ids_vistos"].add(mov.processo_id)
            grupo["processos"].append(mov.processo)

    for g in grupos.values():
        del g["_processo_ids_vistos"]
    return sorted(grupos.values(), key=lambda g: g["quantidade"], reverse=True)


def _contexto_mapa_estado():
    """
    Extraído da view `mapa_estado_lista()` para ser reaproveitado pelo hub
    `regras_e_parametros()` (simplificação de menu).
    """
    itens = MapaEstadoTPU.query.order_by(MapaEstadoTPU.ativo.desc(), MapaEstadoTPU.codigo_tpu).all()
    fila_triagem = fila_triagem_agrupada()
    triagem_pendente = sum(g["quantidade"] for g in fila_triagem)
    return dict(itens=itens, triagem_pendente=triagem_pendente, fila_triagem=fila_triagem)


@governanca_bp.route("/mapa-estado-tpu")
@login_required
@apenas_admin
def mapa_estado_lista():
    return render_template("governanca/mapa_estado_lista.html", **_contexto_mapa_estado())


@governanca_bp.route("/mapa-estado-tpu/novo", methods=["GET", "POST"])
@login_required
@apenas_admin
def novo_mapa_estado():
    if request.method == "POST":
        codigo_tpu = request.form.get("codigo_tpu", "").strip() or None
        texto_contido = request.form.get("texto_contido", "").strip() or None

        # Pelo menos um dos dois é obrigatório — um mapeamento sem código E
        # sem texto não teria como nunca ser encontrado (ver
        # estado_processual_engine.traduzir_movimentacao).
        if not codigo_tpu and not texto_contido:
            flash("Informe pelo menos o código TPU ou o texto a procurar.", "danger")
            return redirect(url_for("governanca.novo_mapa_estado"))

        if codigo_tpu and MapaEstadoTPU.query.filter_by(codigo_tpu=codigo_tpu).first():
            flash(f"Já existe um mapeamento cadastrado para o código TPU {codigo_tpu}.", "danger")
            return redirect(url_for("governanca.novo_mapa_estado"))

        item = MapaEstadoTPU(
            codigo_tpu=codigo_tpu,
            descricao_tpu=request.form.get("descricao_tpu") or None,
            texto_contido=texto_contido,
            estado_negocio=request.form["estado_negocio"].strip(),
            ativo=True,
        )
        db.session.add(item)
        registrar_log(current_user, "criou", "MapaEstadoTPU", None, item.codigo_tpu or item.texto_contido)
        db.session.commit()
        flash("Mapeamento de estado cadastrado.", "success")
        return redirect(url_for("governanca.mapa_estado_lista"))

    # Atalho "mapear agora" a partir da lista de andamentos de um processo
    # (ver app/templates/processos/detalhe.html) ou da fila de triagem
    # agrupada (ver fila_triagem_agrupada acima e
    # governanca/mapa_estado_lista.html) — chega aqui com o código e o
    # começo do texto do ato já preenchidos via querystring, pra não
    # precisar redigitar o código TPU nem procurar o texto exato de novo.
    prefill = {
        "codigo_tpu": request.args.get("codigo_tpu", ""),
        "descricao_tpu": request.args.get("descricao_tpu", ""),
        "texto_contido": request.args.get("texto_contido", ""),
    }
    return render_template("governanca/mapa_estado_form.html", item=None, prefill=prefill)


@governanca_bp.route("/mapa-estado-tpu/<int:item_id>/editar", methods=["GET", "POST"])
@login_required
@apenas_admin
def editar_mapa_estado(item_id):
    item = db.get_or_404(MapaEstadoTPU, item_id)
    if request.method == "POST":
        item.descricao_tpu = request.form.get("descricao_tpu") or None
        item.texto_contido = request.form.get("texto_contido") or None
        item.estado_negocio = request.form["estado_negocio"].strip()
        registrar_log(current_user, "editou", "MapaEstadoTPU", item.id, item.codigo_tpu)
        db.session.commit()
        flash("Mapeamento atualizado.", "success")
        return redirect(url_for("governanca.mapa_estado_lista"))

    return render_template("governanca/mapa_estado_form.html", item=item)


@governanca_bp.route("/mapa-estado-tpu/<int:item_id>/alternar-ativo", methods=["POST"])
@login_required
@apenas_admin
def alternar_mapa_estado(item_id):
    item = db.get_or_404(MapaEstadoTPU, item_id)
    item.ativo = not item.ativo
    registrar_log(current_user, "ativou" if item.ativo else "desativou", "MapaEstadoTPU", item.id, item.codigo_tpu)
    db.session.commit()
    flash(f"Mapeamento {'ativado' if item.ativo else 'desativado'}.", "info")
    return redirect(request.referrer or url_for("governanca.mapa_estado_lista"))


# ---------- Hub "Regras e parâmetros" (simplificação de menu) ----------
# Reúne em abas de uma tela só os 5 itens que antes ficavam soltos num
# acordeão de 2º nível dentro de Governança de carteira: Regras de
# próxima ação, Mapa de estado (TPU), Verificação de conflitos, Modelos
# de peças e Tabela de custas — pedido explícito do usuário, mesmo padrão
# já aplicado em "Rotina", "Minha conta", "Minha empresa" e "Plataforma"
# (ver PENDENCIAS.md). Único nível de permissão (admin) para o hub
# inteiro, então não precisa de `abas_visiveis` como em "Minha empresa"
# — todas as 5 abas aparecem sempre que alguém consegue abrir a tela. As
# cinco telas antigas (regras_proxima_acao_lista, mapa_estado_lista,
# verificacao_conflitos, modelos_peca_lista, tabela_custas_lista)
# continuam existindo e funcionando normalmente para quem chegar direto
# por um link salvo — só as LISTAS viraram abas; "+ Nova regra"/"Editar"/
# etc. continuam abrindo uma tela de formulário própria, fora do hub.

@governanca_bp.route("/regras-e-parametros")
@login_required
@apenas_admin
def regras_e_parametros():
    abas_visiveis = ["regras", "mapa_estado", "conflitos", "modelos_peca", "tabela_custas"]
    aba_pedida = request.args.get("tab")
    aba_inicial = aba_pedida if aba_pedida in abas_visiveis else "regras"

    return render_template(
        "governanca/regras_e_parametros_hub.html",
        aba_inicial=aba_inicial,
        regras_ctx=_contexto_regras_proxima_acao(),
        mapa_estado_ctx=_contexto_mapa_estado(),
        conflitos_ctx=_contexto_verificacao_conflitos(),
        modelos_peca_ctx=_contexto_modelos_peca(),
        tabela_custas_ctx=_contexto_tabela_custas(),
    )


# ---------- Hub "Painel e filas" (simplificação de menu) ----------
# Reúne em abas de uma tela só os itens que hoje aparecem soltos, sob o
# título fixo "Painel e filas", em Governança de carteira: Painel de
# governança, Fila de intimações, Métricas e relatório semanal (que já
# era, desde a 1ª rodada de simplificação, um hub de 2 abas próprio —
# aqui vira uma aba "aninhada", com as mesmas 2 sub-abas dentro dela, ver
# governanca/_corpo_painel_metricas.html), Produtividade da equipe e
# Contingenciamento. Mesmo nível de permissão de sempre (só
# @login_required — nenhuma dessas 5 telas era admin-only) para o hub
# inteiro, sem variação de visibilidade por aba. As cinco telas antigas
# continuam existindo e funcionando normalmente para quem chegar direto
# por um link salvo.

@governanca_bp.route("/painel-e-filas")
@login_required
def painel_e_filas():
    abas_visiveis = ["painel", "fila", "metricas_relatorio", "produtividade", "contingenciamento"]
    aba_pedida = request.args.get("tab")
    aba_inicial = aba_pedida if aba_pedida in abas_visiveis else "painel"

    aba_metricas_relatorio_inicial = "relatorio" if request.args.get("subtab") == "relatorio" else "metricas"
    metricas_relatorio_ctx = {}
    metricas_relatorio_ctx.update(_contexto_metricas())
    metricas_relatorio_ctx.update(_contexto_relatorio_semanal())

    return render_template(
        "governanca/painel_e_filas_hub.html",
        aba_inicial=aba_inicial,
        aba_metricas_relatorio_inicial=aba_metricas_relatorio_inicial,
        painel_ctx=_contexto_painel_governanca(),
        fila_ctx=_contexto_fila_intimacoes(),
        metricas_relatorio_ctx=metricas_relatorio_ctx,
        produtividade_ctx=_contexto_produtividade(),
        contingenciamento_ctx=_contexto_contingenciamento(),
    )
