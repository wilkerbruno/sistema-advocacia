"""
Extração de texto de um documento já anexado a um processo — usada hoje
só pela referência de estilo do rascunho de petição por IA (PENDENCIAS.md,
seção -53; ver app/utils/analise_processo_ia.py e
app/routes/processos.py::gerar_analise_ia). Não tem nenhuma outra
finalidade além dessa por enquanto — não é um leitor de documento
genérico do sistema.

Só .pdf, .docx e .txt são suportados: são os formatos plausíveis pra uma
peça processual já protocolada (o objetivo aqui). Os demais tipos aceitos
no upload de documento (imagem, planilha) não têm extração de texto
ESTRUTURAL confiável o bastante pra servir de referência de estilo — em
vez de tentar e devolver lixo (ou pior, texto de OCR errado que a IA
trataria como estilo real), a função recusa esses tipos com um erro
claro. PDF escaneado (imagem sem camada de texto) tampouco funciona —
mesmo motivo, mesma resposta: erro claro, nunca um resultado vazio
disfarçado de sucesso.
"""
import os

LIMITE_PADRAO_CHARS = 2000  # ver comentário sobre orçamento de contexto em analise_processo_ia.py

EXTENSOES_SUPORTADAS = ("pdf", "docx", "txt")


class ExtracaoNaoSuportadaError(Exception):
    pass


def extrair_texto_documento(documento, upload_folder, limite_chars=LIMITE_PADRAO_CHARS):
    """
    Devolve (texto, truncado) com o texto extraído de `documento` (um
    Documento já anexado a um processo), cortado em `limite_chars`
    (orçamento pequeno de propósito — é só referência de ESTILO dentro do
    prompt da IA, que já tem um orçamento de contexto apertado pro modelo
    local, ver app/utils/analise_processo_ia.py).

    Levanta ExtracaoNaoSuportadaError pra tipo de arquivo sem suporte, ou
    ValueError pra qualquer outra falha (arquivo ausente no armazenamento,
    arquivo corrompido/protegido por senha, ou texto extraído vazio) —
    quem chama decide como degradar (nunca deveria travar a geração
    inteira por causa disto, ver o try/except na rota).
    """
    caminho = os.path.join(upload_folder, str(documento.processo_id), documento.nome_arquivo)
    if not os.path.exists(caminho):
        raise ValueError(f'Arquivo de "{documento.nome_original}" não foi encontrado no armazenamento.')

    ext = documento.nome_original.rsplit(".", 1)[-1].lower() if "." in documento.nome_original else ""
    if ext not in EXTENSOES_SUPORTADAS:
        raise ExtracaoNaoSuportadaError(
            f'Não sei ler o conteúdo de um arquivo ".{ext}" — hoje só .pdf, .docx e .txt podem ser '
            "usados como referência de estilo."
        )

    try:
        if ext == "pdf":
            texto = _extrair_pdf(caminho)
        elif ext == "docx":
            texto = _extrair_docx(caminho)
        else:
            with open(caminho, "r", encoding="utf-8", errors="ignore") as f:
                texto = f.read()
    except Exception as e:
        raise ValueError(f'Não consegui ler o conteúdo de "{documento.nome_original}": {e}') from e

    texto = (texto or "").strip()
    if not texto:
        raise ValueError(
            f'Não encontrei texto legível em "{documento.nome_original}" — se for um PDF escaneado '
            "(imagem, sem camada de texto), a extração automática não funciona nele."
        )

    truncado = len(texto) > limite_chars
    return (texto[:limite_chars] if truncado else texto), truncado


def _extrair_pdf(caminho):
    from pypdf import PdfReader

    leitor = PdfReader(caminho)
    partes = []
    for pagina in leitor.pages:
        partes.append(pagina.extract_text() or "")
    return "\n".join(partes)


def _extrair_docx(caminho):
    import docx

    doc = docx.Document(caminho)
    return "\n".join(p.text for p in doc.paragraphs)
