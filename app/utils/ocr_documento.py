"""
OCR de PDF escaneado — complemento do item 3 da lista de pipeline de IA
jurídica (PENDENCIAS.md, seção -103): "ingestão e indexação". Antes deste
módulo, um PDF sem camada de texto (imagem pura, comum em autos antigos
digitalizados ou juntados por escaneamento) simplesmente não podia ser
lido pelo sistema (ver app/utils/extracao_documento.py, que recusava esse
caso com um erro claro em vez de devolver lixo) — este módulo é o que
preenche essa lacuna, usado por app/utils/indexacao_documentos.py.

Depende de DUAS coisas que não são wheel Python comuns — precisam estar
instaladas no SISTEMA OPERACIONAL do servidor, não só no requirements.txt:
  - o binário `tesseract` (motor de OCR de verdade — pacote `pytesseract`
    é só um wrapper Python que CHAMA esse binário via subprocess) com o
    pacote de idioma português (`tesseract-ocr-por`);
  - `poppler-utils` (usado por `pdf2image` para rasterizar cada página do
    PDF em imagem antes do OCR — sem poppler instalado, pdf2image não
    consegue converter nada).
Ver Dockerfile (linha do `apt-get install`) — ambos foram adicionados lá.

⚠️ Degrada honestamente, mesmo padrão do resto do projeto (ver
app/utils/whatsapp.py, app/utils/email.py etc.): se o binário `tesseract`
ou o `poppler` não estiverem instalados no servidor (por exemplo, uma
imagem Docker mais antiga que ainda não rodou o Dockerfile atualizado),
`ocr_disponivel()` devolve False e `ocr_pagina_pdf` levanta
`OcrIndisponivelError` com uma mensagem clara — NUNCA finge que rodou OCR
com um texto vazio disfarçado de sucesso, e nunca derruba o processo de
indexação inteiro por causa disso (quem chama decide como degradar, ver
app/utils/indexacao_documentos.py).

⚠️ Não testado contra um volume real de autos escaneados de tribunal —
testado só com PDFs sintéticos gerados para o desenvolvimento. Qualidade
do OCR em documentos jurídicos reais (assinaturas sobrepostas, carimbos,
papel amarelado, letra manuscrita) pode variar bastante; sempre trate o
texto extraído por OCR como aproximado, nunca como transcrição garantida
(por isso `origem_texto="ocr"` fica marcado em cada chunk, ver
app/models/indexacao.py — nunca escondido de quem for auditar depois).
"""
import shutil

IDIOMA_PADRAO = "por"


class OcrIndisponivelError(Exception):
    """tesseract e/ou poppler não instalados no servidor — ver docstring do módulo."""


def ocr_disponivel():
    """
    Checagem rápida e barata (não importa as libs pesadas, só confere se o
    binário do tesseract está no PATH) — usada pra decidir, ANTES de tentar
    indexar, se vale a pena nem tentar OCR (evita import lento/erro tardio
    no meio do processamento de página 47 de 80).
    """
    return shutil.which("tesseract") is not None


def ocr_pagina_pdf(caminho_pdf, numero_pagina, idioma=IDIOMA_PADRAO):
    """
    Roda OCR em UMA página específica (1-based) de um PDF já salvo em
    disco. Devolve o texto reconhecido (string, pode vir vazia se a página
    realmente não tiver nada reconhecível — carimbo borrado, página em
    branco etc.) ou levanta `OcrIndisponivelError` se as dependências não
    estiverem instaladas.

    Processa página por página (não o PDF inteiro de uma vez) de propósito:
    permite ao chamador (`indexacao_documentos.py`) aplicar o teto de
    `OCR_MAX_PAGINAS` e seguir em frente mesmo se UMA página específica
    falhar (imagem corrompida, timeout), sem perder as demais.
    """
    if not ocr_disponivel():
        raise OcrIndisponivelError(
            "OCR não está disponível neste servidor — o binário \"tesseract\" não foi encontrado. "
            "Instale tesseract-ocr e tesseract-ocr-por (ver Dockerfile) para ativar leitura de PDF "
            "escaneado."
        )

    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as e:
        raise OcrIndisponivelError(
            f"Dependência Python de OCR ausente ({e}) — confira se pytesseract e pdf2image estão "
            "instalados (ver requirements.txt)."
        ) from e

    try:
        imagens = convert_from_path(caminho_pdf, first_page=numero_pagina, last_page=numero_pagina, dpi=200)
    except Exception as e:
        # poppler ausente/página corrompida — mesma família de erro "sem
        # dependência do sistema operacional" citada no docstring do módulo,
        # mas só detectável na hora de converter de verdade (pdf2image não
        # expõe um jeito barato de checar poppler sem tentar converter).
        raise OcrIndisponivelError(
            f"Não consegui converter a página {numero_pagina} do PDF em imagem para OCR: {e}. Confira "
            "se poppler-utils está instalado no servidor (ver Dockerfile)."
        ) from e

    if not imagens:
        return ""

    try:
        texto = pytesseract.image_to_string(imagens[0], lang=idioma)
    except Exception as e:
        raise OcrIndisponivelError(
            f"O tesseract falhou ao reconhecer texto na página {numero_pagina}: {e}. Confira se o "
            f"pacote de idioma \"{idioma}\" (tesseract-ocr-{idioma}) está instalado."
        ) from e

    return (texto or "").strip()
