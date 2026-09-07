"""
Timbrado do escritório (logo) nos PDFs gerados pelo sistema — a pedido
explícito ("quero ter uma opção do advogado colocar o timbrado do
escritório dele no sistema", PENDENCIAS.md seção -69). Reaproveitável por
qualquer gerador de PDF futuro do sistema — hoje só o recibo
(`app/routes/financeiro.py::recibo`) usa isto, mas `desenhar_cabecalho`
foi escrito de propósito pra não depender de nada específico do recibo.

Decisões (perguntadas e confirmadas com o usuário antes de construir):
- É uma LOGO somada ao texto que já existia (nome/CNPJ/endereço) — não
  substitui o cabeçalho inteiro por uma imagem única.
- Vale pra empresa INTEIRA (todas as unidades), não por unidade — ver
  `Empresa.logo_arquivo` em app/models/empresa.py.
- Guardada como arquivo em disco (mesmo padrão de `Documento` — ver
  `app/routes/processos.py::add_documento`), nunca no banco — só o NOME
  do arquivo salvo fica na coluna `Empresa.logo_arquivo`.
"""
import os

from PIL import Image
from werkzeug.utils import secure_filename

EXTENSOES_PERMITIDAS = {"png", "jpg", "jpeg"}
TAMANHO_MAXIMO_BYTES = 3 * 1024 * 1024  # 3 MB — logo não precisa de mais que isso
ALTURA_MAXIMA_CM = 1.8  # altura fixa no cabeçalho do PDF; largura escala proporcionalmente


class ArquivoLogoInvalido(Exception):
    """Levantado quando o arquivo enviado não passa na validação (extensão
    errada, não é uma imagem de verdade, ou grande demais) — a mensagem já
    vem pronta pra mostrar ao usuário via flash()."""


def _pasta_logo_empresa(upload_folder, empresa_id):
    return os.path.join(upload_folder, "timbrados_empresas", str(empresa_id))


def _extensao_permitida(nome_arquivo):
    return "." in nome_arquivo and nome_arquivo.rsplit(".", 1)[-1].lower() in EXTENSOES_PERMITIDAS


def caminho_logo(upload_folder, empresa):
    """Caminho completo em disco da logo desta empresa, ou None se ela não
    tiver logo cadastrada (ou se o arquivo tiver sumido por fora do
    sistema, ex.: apagado manualmente do servidor)."""
    if not empresa or not empresa.logo_arquivo:
        return None
    caminho = os.path.join(_pasta_logo_empresa(upload_folder, empresa.id), empresa.logo_arquivo)
    return caminho if os.path.isfile(caminho) else None


def salvar_logo(upload_folder, empresa, arquivo_werkzeug):
    """
    Valida e salva a logo enviada, substituindo a anterior (se houver) —
    guarda só UM arquivo por empresa, sem versionar. Levanta
    ArquivoLogoInvalido (com mensagem pronta) se o arquivo não passar na
    validação; NADA é escrito em disco nesse caso. Devolve o nome do
    arquivo salvo, pra quem chamou setar em `empresa.logo_arquivo` e
    fazer commit.
    """
    nome_original = secure_filename(arquivo_werkzeug.filename or "")
    if not nome_original or not _extensao_permitida(nome_original):
        raise ArquivoLogoInvalido("Envie uma imagem PNG ou JPG.")

    pasta = _pasta_logo_empresa(upload_folder, empresa.id)
    os.makedirs(pasta, exist_ok=True)

    ext = nome_original.rsplit(".", 1)[-1].lower()
    caminho_temporario = os.path.join(pasta, f"_upload_tmp.{ext}")
    arquivo_werkzeug.save(caminho_temporario)

    try:
        if os.path.getsize(caminho_temporario) > TAMANHO_MAXIMO_BYTES:
            raise ArquivoLogoInvalido("Imagem maior que 3 MB — use uma versão menor.")
        try:
            with Image.open(caminho_temporario) as imagem:
                imagem.verify()  # levanta exceção se o conteúdo não for uma imagem de verdade
        except ArquivoLogoInvalido:
            raise
        except Exception:
            raise ArquivoLogoInvalido("O arquivo enviado não é uma imagem válida (PNG ou JPG).")
    except ArquivoLogoInvalido:
        os.remove(caminho_temporario)
        raise

    nome_final = f"logo.{ext}"
    caminho_final = os.path.join(pasta, nome_final)

    # Remove uma logo anterior com extensão DIFERENTE (ex.: trocou de .png
    # pra .jpg) — sem isso, o arquivo velho ficaria esquecido na pasta pra
    # sempre, sem nunca mais ser referenciado por nada.
    if empresa.logo_arquivo and empresa.logo_arquivo != nome_final:
        caminho_antigo = os.path.join(pasta, empresa.logo_arquivo)
        if os.path.isfile(caminho_antigo):
            os.remove(caminho_antigo)

    os.replace(caminho_temporario, caminho_final)
    return nome_final


def remover_logo(upload_folder, empresa):
    """Apaga o arquivo em disco, se existir. Quem limpa
    `empresa.logo_arquivo` e faz commit é sempre quem chamou (rota) — este
    módulo nunca mexe no banco diretamente, só em arquivo."""
    caminho = caminho_logo(upload_folder, empresa)
    if caminho:
        os.remove(caminho)


def desenhar_cabecalho(c, empresa, unidade, margem, y_topo, upload_folder):
    """
    Desenha o cabeçalho de um PDF (nome do escritório + CNPJ + endereço,
    com a logo ao lado quando cadastrada) usando um `reportlab.pdfgen.canvas.Canvas`
    já aberto (`c`). Devolve o novo `y` (posição vertical, mesma unidade do
    reportlab) pra quem chamou continuar desenhando o resto do documento
    logo abaixo.

    SEM logo cadastrada (o único caso possível antes desta funcionalidade
    existir, e ainda o mais comum): desenha só o texto, exatamente como
    sempre foi — nenhuma mudança visual pra quem não configurar nada.

    COM logo cadastrada: desenha a imagem à esquerda (altura fixa em
    `ALTURA_MAXIMA_CM`, largura proporcional à imagem original) e o texto
    ao lado dela, à direita.
    """
    from reportlab.lib.units import cm

    y = y_topo
    caminho = caminho_logo(upload_folder, empresa)

    x_texto = margem
    altura_logo_desenhada = 0
    if caminho:
        try:
            with Image.open(caminho) as imagem:
                largura_original, altura_original = imagem.size
            altura_logo_desenhada = ALTURA_MAXIMA_CM * cm
            largura_desenho = altura_logo_desenhada * (largura_original / altura_original)
            c.drawImage(caminho, margem, y - altura_logo_desenhada, width=largura_desenho,
                        height=altura_logo_desenhada, preserveAspectRatio=True, mask="auto")
            x_texto = margem + largura_desenho + 0.4 * cm
        except Exception:
            # Nunca deixa uma logo corrompida/ilegível derrubar a emissão
            # do PDF inteiro — na dúvida, cai pro cabeçalho só com texto.
            x_texto = margem
            altura_logo_desenhada = 0

    c.setFont("Helvetica-Bold", 16)
    c.drawString(x_texto, y, empresa.nome if empresa else "Escritório de advocacia")
    y -= 0.6 * cm
    c.setFont("Helvetica", 9)
    if empresa and empresa.cnpj:
        c.drawString(x_texto, y, f"CNPJ: {empresa.cnpj}")
        y -= 0.45 * cm
    if unidade and unidade.endereco:
        partes_endereco = unidade.endereco
        if unidade.cidade:
            partes_endereco += f" — {unidade.cidade}/{unidade.estado or ''}"
        c.drawString(x_texto, y, partes_endereco)
        y -= 0.45 * cm

    if altura_logo_desenhada:
        # Garante que "y" não fica ACIMA da base da logo, caso o texto
        # (nome + CNPJ + endereço) tenha ocupado menos altura que ela.
        y = min(y, y_topo - altura_logo_desenhada)

    return y
