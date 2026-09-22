"""
Ingestão e indexação de documentos (item 3 da lista de pipeline de IA
jurídica — PENDENCIAS.md, seção -103): "OCR, índice vetorial, corte por
evento". Pipeline completo: extrai texto (com fallback de OCR pra PDF
escaneado, ver app/utils/ocr_documento.py), corta em pedaços NUNCA no meio
de uma frase/palavra ("corte por evento" — cada pedaço é uma unidade
lógica fechada: uma página, ou um trecho dela quando a página é grande),
gera embedding de cada pedaço (via Gemini BYOK, quando a empresa tem chave
configurada — ver app/utils/gemini_api.py —, ou via o modelo local, sem
custo, quando não tem — ver app/utils/ia_local.py) e guarda tudo em
`DocumentoIndexado` (app/models/indexacao.py).

Roda em segundo plano via RQ (app/jobs/indexacao_jobs.py) — nunca dentro
do ciclo de requisição/resposta do upload, mesmo motivo de
app/utils/fila.py: um documento de centenas de páginas com OCR pode levar
minutos.

Onde isso é usado: `buscar_trechos_relevantes` alimenta
app/utils/analise_processo_ia.py::montar_digest_processo com os trechos
mais RELEVANTES dos documentos anexados (busca por similaridade, quando
disponível) em vez do corte cego por caractere que existia antes.

⚠️ Escopo real, sem fingir mais do que está pronto: indexação e busca são
por PROCESSO (nunca cruza processo, muito menos empresa — mesma disciplina
de isolamento multi-tenant do resto do sistema, ver `processo_id`
denormalizado em DocumentoIndexado). Embedding tem DOIS caminhos possíveis
hoje (PENDENCIAS.md, seção -123): Gemini BYOK (empresa com chave
cadastrada — mais preciso, é a opção paga) OU o modelo local, bem menor,
que roda pela mesma biblioteca do chat local (`app/utils/ia_local.py`,
sem custo, sem BYOK) — Gemini sempre tem prioridade quando a empresa tem
chave; o modelo local só entra pra quem NÃO tem. Sem nenhum dos dois
disponíveis, o sistema ainda indexa (chunking por evento já ajuda sozinho)
mas `buscar_trechos_relevantes` cai para "mais recentes primeiro", nunca
inventa uma similaridade que não foi calculada. Vetores de proveniências
diferentes NUNCA são comparados entre si — cada linha grava de qual modelo
veio (`embedding_modelo`), e a busca só considera vetores do MESMO modelo
que geraria o vetor da consulta agora.
"""
import json
import os
import re
from datetime import datetime

from flask import current_app

from app.extensions import db
from app.models import Documento, DocumentoIndexado
from app.utils import gemini_api, ia_local, cofre
from app.utils.ocr_documento import ocr_disponivel, ocr_pagina_pdf, OcrIndisponivelError

TAMANHO_CHUNK_PADRAO = 1500
OCR_MAX_PAGINAS_PADRAO = 80
LIMIAR_TEXTO_VAZIO_CHARS = 20  # abaixo disso, a página é tratada como "sem camada de texto" (candidata a OCR)


class IndexacaoNaoSuportadaError(Exception):
    pass


def _config(chave, padrao):
    try:
        return current_app.config.get(chave, padrao)
    except RuntimeError:  # fora de app context (ex.: chamada direta em script) — usa o padrão
        return padrao


def _obter_chave_gemini(empresa):
    """
    Devolve a chave do Gemini já DECIFRADA da empresa, ou None se ela não
    tiver cadastrado nenhuma — independente de qual provedor está
    SELECIONADO como padrão do chat do Agente de IA (ver
    app/utils/agente_ia_router.py): ter uma chave do Gemini cadastrada já
    é suficiente pra habilitar embedding, mesmo que a empresa prefira usar
    o modelo local ou o Claude BYOK pro chat.
    """
    if empresa is None or not empresa.agente_ia_gemini_chave_cifrada:
        return None
    try:
        return cofre.decifrar_segredo(empresa.agente_ia_gemini_chave_cifrada)
    except Exception:
        return None


def _provedor_embedding_ativo(empresa):
    """
    Decide QUAL caminho de embedding usar agora, nesta ordem (PENDENCIAS.md,
    seção -123): Gemini BYOK primeiro (empresa com chave cadastrada — mais
    preciso, é a opção paga), senão o modelo local (grátis, sem BYOK,
    disponível quando o arquivo de pesos foi baixado — ver
    app/utils/ia_local.py::embedding_disponivel), senão nenhum.

    Devolve uma tupla (gerar_fn, nome_modelo) ou (None, None) quando nenhum
    dos dois está disponível. `gerar_fn(textos)` sempre tem a MESMA
    assinatura nos dois casos (lista de textos -> lista de vetores),
    escondendo a diferença de API entre gemini_api e ia_local de quem chama.
    """
    chave = _obter_chave_gemini(empresa)
    if chave:
        return (lambda textos: gemini_api.gerar_embeddings_lote(textos, chave)), gemini_api.MODELO_EMBEDDING_PADRAO
    if ia_local.embedding_disponivel():
        return ia_local.gerar_embeddings_lote, ia_local.nome_modelo_embedding()
    return None, None


# Os dois erros possíveis (Gemini ou local) — tratados sempre juntos, no
# mesmo `except`, pelos dois pontos deste módulo que chamam `gerar_fn`.
ErrosProvedorEmbeddingIndisponivel = (gemini_api.GeminiIndisponivelError, ia_local.ModeloIndisponivelError)


def _particionar_paragrafo_longo(texto, tamanho_alvo):
    """Um parágrafo sozinho maior que o alvo — corta por FRASE (nunca no
    meio de uma palavra), acumulando até chegar perto do alvo. Só cai pra
    corte bruto por caractere no caso extremo de uma frase única maior que
    o alvo inteiro (ex.: bloco de texto sem pontuação nenhuma)."""
    sentencas = re.split(r"(?<=[.;!?])\s+", texto)
    pedacos = []
    atual = ""
    for s in sentencas:
        candidato = (atual + " " + s) if atual else s
        if len(candidato) <= tamanho_alvo:
            atual = candidato
            continue
        if atual:
            pedacos.append(atual)
            atual = ""
        if len(s) <= tamanho_alvo:
            atual = s
        else:
            # frase única maior que o alvo — último recurso, corte bruto
            for i in range(0, len(s), tamanho_alvo):
                pedacos.append(s[i:i + tamanho_alvo])
    if atual:
        pedacos.append(atual)
    return pedacos


def particionar_em_chunks(texto, tamanho_alvo=TAMANHO_CHUNK_PADRAO):
    """
    "Corte por evento" (item 3): divide `texto` em pedaços de até
    `tamanho_alvo` caracteres, sempre respeitando fronteira de parágrafo
    (e, quando um parágrafo sozinho é grande demais, de FRASE) — nunca
    corta cego no meio de uma frase/palavra, ao contrário do corte por
    caractere bruto que `montar_digest_processo` usava antes (ver
    app/utils/analise_processo_ia.py).
    """
    texto = (texto or "").strip()
    if not texto:
        return []
    if len(texto) <= tamanho_alvo:
        return [texto]

    paragrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    if not paragrafos:
        paragrafos = [texto]

    chunks = []
    atual = ""
    for p in paragrafos:
        candidato = (atual + "\n\n" + p) if atual else p
        if len(candidato) <= tamanho_alvo:
            atual = candidato
            continue
        if atual:
            chunks.append(atual)
            atual = ""
        if len(p) <= tamanho_alvo:
            atual = p
        else:
            chunks.extend(_particionar_paragrafo_longo(p, tamanho_alvo))
    if atual:
        chunks.append(atual)
    return chunks


def _extrair_por_pagina_pdf(caminho, max_paginas_ocr):
    """Devolve lista de (pagina_1based, texto, origem) pra um PDF —
    tenta a camada de texto nativa (pypdf) primeiro; só recorre a OCR
    quando a página vem praticamente vazia (indício de PDF escaneado) E o
    OCR está disponível E ainda não estourou o teto de páginas OCR'd desta
    rodada (ver OCR_MAX_PAGINAS em config.py)."""
    from pypdf import PdfReader

    leitor = PdfReader(caminho)
    disponivel = ocr_disponivel()
    paginas_ocr_usadas = 0
    resultado = []

    for i, pagina in enumerate(leitor.pages, start=1):
        texto_nativo = (pagina.extract_text() or "").strip()
        if len(texto_nativo) >= LIMIAR_TEXTO_VAZIO_CHARS:
            resultado.append((i, texto_nativo, "camada_pdf"))
            continue

        if not disponivel or paginas_ocr_usadas >= max_paginas_ocr:
            # sem OCR disponível, ou teto de páginas OCR'd já estourado
            # nesta rodada — página fica sem texto (nunca finge sucesso).
            resultado.append((i, texto_nativo, "camada_pdf"))
            continue

        try:
            texto_ocr = ocr_pagina_pdf(caminho, i)
            paginas_ocr_usadas += 1
            resultado.append((i, texto_ocr, "ocr"))
        except OcrIndisponivelError:
            resultado.append((i, texto_nativo, "camada_pdf"))

    return resultado


def _extrair_por_secao_docx(caminho):
    import docx

    doc = docx.Document(caminho)
    texto = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return [(None, texto, "docx")] if texto.strip() else []


def _extrair_por_secao_txt(caminho):
    with open(caminho, "r", encoding="utf-8", errors="ignore") as f:
        texto = f.read()
    return [(None, texto, "txt")] if texto.strip() else []


def indexar_documento(documento, upload_folder):
    """
    Indexa (ou REindexa — apaga chunks anteriores deste documento antes de
    recriar, idempotente) um `Documento` já anexado a um processo. Roda
    dentro de um job da fila (app/jobs/indexacao_jobs.py) — nunca na
    requisição web do upload.

    Devolve um dict-resumo {chunks, paginas_ocr, com_embedding, erro} e já
    grava `documento.indexado_em`/`documento.erro_indexacao` (quem chama
    ainda precisa dar commit).
    """
    caminho = os.path.join(upload_folder, str(documento.processo_id), documento.nome_arquivo)
    if not os.path.exists(caminho):
        documento.indexado_em = datetime.utcnow()
        documento.erro_indexacao = "Arquivo não encontrado no armazenamento no momento da indexação."
        return {"chunks": 0, "paginas_ocr": 0, "com_embedding": 0, "erro": documento.erro_indexacao}

    ext = documento.nome_original.rsplit(".", 1)[-1].lower() if "." in documento.nome_original else ""
    max_paginas_ocr = _config("OCR_MAX_PAGINAS", OCR_MAX_PAGINAS_PADRAO)
    tamanho_chunk = _config("INDEXACAO_TAMANHO_CHUNK_CHARS", TAMANHO_CHUNK_PADRAO)

    try:
        if ext == "pdf":
            secoes = _extrair_por_pagina_pdf(caminho, max_paginas_ocr)
        elif ext == "docx":
            secoes = _extrair_por_secao_docx(caminho)
        elif ext == "txt":
            secoes = _extrair_por_secao_txt(caminho)
        else:
            raise IndexacaoNaoSuportadaError(
                f'Não sei indexar um arquivo ".{ext}" — hoje só .pdf, .docx e .txt são suportados.'
            )
    except IndexacaoNaoSuportadaError as e:
        documento.indexado_em = datetime.utcnow()
        documento.erro_indexacao = str(e)
        return {"chunks": 0, "paginas_ocr": 0, "com_embedding": 0, "erro": str(e)}
    except Exception as e:
        documento.indexado_em = datetime.utcnow()
        documento.erro_indexacao = f"Falha ao extrair texto para indexação: {e}"[:500]
        return {"chunks": 0, "paginas_ocr": 0, "com_embedding": 0, "erro": documento.erro_indexacao}

    # Reindexação idempotente — apaga chunks anteriores deste documento.
    DocumentoIndexado.query.filter_by(documento_id=documento.id).delete()

    paginas_ocr = sum(1 for (_, _, origem) in secoes if origem == "ocr")
    linhas = []
    ordem = 0
    for pagina, texto, origem in secoes:
        for pedaco in particionar_em_chunks(texto, tamanho_chunk):
            linhas.append(DocumentoIndexado(
                documento_id=documento.id, processo_id=documento.processo_id,
                pagina=pagina, ordem=ordem, texto=pedaco, origem_texto=origem,
            ))
            ordem += 1

    for linha in linhas:
        db.session.add(linha)
    db.session.flush()

    com_embedding = 0
    erro_embedding = None
    if linhas:
        empresa = documento.processo.unidade.empresa if documento.processo and documento.processo.unidade else None
        gerar_fn, nome_modelo = _provedor_embedding_ativo(empresa)
        if gerar_fn:
            try:
                vetores = gerar_fn([l.texto for l in linhas])
                for linha, vetor in zip(linhas, vetores):
                    if vetor:
                        linha.embedding = json.dumps(vetor)
                        linha.embedding_modelo = nome_modelo
                        com_embedding += 1
            except ErrosProvedorEmbeddingIndisponivel as e:
                # Degrada com honestidade: os chunks (já criados acima) ficam
                # SEM embedding, mas continuam indexados e úteis pro fallback
                # de buscar_trechos_relevantes — nunca perde a indexação
                # inteira por causa de embedding indisponível no momento.
                erro_embedding = f"Chunks indexados, mas embedding falhou: {e}"[:500]

    resumo_erro = None
    if not linhas:
        resumo_erro = "Nenhum texto encontrado para indexar (documento vazio, ou PDF escaneado sem OCR disponível)."
    elif erro_embedding:
        resumo_erro = erro_embedding

    documento.indexado_em = datetime.utcnow()
    documento.erro_indexacao = resumo_erro

    return {"chunks": len(linhas), "paginas_ocr": paginas_ocr, "com_embedding": com_embedding, "erro": resumo_erro}


def info_ultima_busca_autos(processo):
    """
    Item 2 da lista de pipeline de IA jurídica (PENDENCIAS.md, seção -103):
    "não existe hoje nenhuma lógica de 'baixa só o que ainda não está
    indexado'". Esta função é essa lógica — não IMPEDE um novo pedido (o
    Agente Local ainda é sempre um piloto, vale poder tentar de novo), mas
    dá ao advogado (e à rota que decide o que mostrar/flashar, ver
    app/routes/governanca.py::buscar_processo) a informação que faltava
    pra decidir com consciência: já baixamos os autos completos antes? já
    foram indexados? alguma coisa nova foi capturada desde então?

    Devolve None se este processo nunca teve um download completo (via
    Agente Local — `Documento.categoria == "autos_completo_agente_local"`)
    concluído, ou um dict:
      {"documento": Documento, "indexado": bool, "qtd_movimentacoes_novas": int}

    `qtd_movimentacoes_novas`: quantas `Movimentacao` (não deletadas) foram
    CAPTURADAS (por `criado_em`, não pela data do próprio ato) depois do
    último download completo — 0 significa "nada de novo chegou por
    nenhuma fonte de captura deste sistema desde então" (não é uma garantia
    de que o tribunal não mudou nada, só do que este sistema já sabe).
    """
    from datetime import datetime
    from app.models import Movimentacao

    ultimo = (Documento.query.filter_by(processo_id=processo.id, categoria="autos_completo_agente_local")
              .order_by(Documento.enviado_em.desc()).first())
    if ultimo is None:
        return None

    indexado = DocumentoIndexado.query.filter_by(documento_id=ultimo.id).count() > 0
    referencia = ultimo.enviado_em or datetime.min
    qtd_novas = Movimentacao.query.filter(
        Movimentacao.processo_id == processo.id,
        Movimentacao.deletado_em.is_(None),
        Movimentacao.criado_em > referencia,
    ).count()
    return {"documento": ultimo, "indexado": indexado, "qtd_movimentacoes_novas": qtd_novas}


def _cosine_similaridade(a, b):
    import numpy as np

    va, vb = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def buscar_trechos_relevantes(processo, consulta, top_k=6):
    """
    Devolve até `top_k` `DocumentoIndexado` do processo, os mais
    RELEVANTES para `consulta` (texto livre — ex.: a delimitação do objeto
    ou o tipo de peça pedido, ver app/utils/analise_processo_ia.py).

    Caminho principal (busca semântica de verdade): se existe algum
    provedor de embedding disponível agora (Gemini BYOK, ou o modelo local
    quando a empresa não tem chave — ver `_provedor_embedding_ativo`) E
    pelo menos um chunk deste processo tem embedding gravado DO MESMO
    modelo (nunca compara vetores de modelos diferentes entre si — não são
    o mesmo espaço vetorial, a "similaridade" entre eles não significaria
    nada), embeda `consulta` e ordena por similaridade de cosseno.

    Caminho de fallback (nenhum provedor disponível, chunk nenhum do
    modelo atual, ou falha na hora): devolve os chunks mais RECENTES,
    ainda respeitando fronteira de evento (chunk inteiro, nunca corte cru)
    — pior que busca semântica, mas nunca finge uma relevância que não foi
    calculada.

    Devolve lista vazia se o processo não tem nenhum documento indexado
    ainda — nunca gera erro pra quem chama (montar_digest_processo só
    inclui o bloco se a lista vier não-vazia).
    """
    base = DocumentoIndexado.query.filter_by(processo_id=processo.id)
    todos = base.order_by(DocumentoIndexado.criado_em.desc()).all()
    if not todos:
        return []

    empresa = processo.unidade.empresa if processo.unidade else None
    gerar_fn, nome_modelo = _provedor_embedding_ativo(empresa) if consulta else (None, None)
    com_embedding = [c for c in todos if c.embedding and c.embedding_modelo == nome_modelo] if gerar_fn else []

    if gerar_fn and com_embedding:
        try:
            vetor_consulta = gerar_fn([consulta])[0]
            pontuados = [
                (c, _cosine_similaridade(vetor_consulta, json.loads(c.embedding)))
                for c in com_embedding
            ]
            pontuados.sort(key=lambda par: par[1], reverse=True)
            return [c for c, _pontuacao in pontuados[:top_k]]
        except ErrosProvedorEmbeddingIndisponivel:
            pass  # cai pro fallback abaixo — nunca quebra a geração da análise por causa disso
        except (ValueError, TypeError):
            pass  # embedding salvo malformado numa linha antiga — mesmo fallback, nunca derruba a busca

    return todos[:top_k]
