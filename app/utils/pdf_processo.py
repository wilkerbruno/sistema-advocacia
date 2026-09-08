"""
PDF de resumo do processo (PENDENCIAS.md, seção -77) — pedido explícito do
usuário depois de testar a busca no e-SAJ público: "gostaria que aparecesse
um PDF com esse processo".

Importante — o que este PDF NÃO é: não é o PDF real do processo (a íntegra
dos documentos/petições no sistema do tribunal). Nem o DataJud nem o e-SAJ
público (ver app/utils/conector_esaj_publico.py) devolvem o conteúdo real
de documentos — só metadados e o texto das movimentações. Isso é um
RESUMO gerado pelo próprio JusControl com tudo que o sistema já tem
cadastrado sobre o processo (capa, partes, movimentações, prazos e
audiências) — não substitui abrir o processo no site do tribunal quando o
inteiro teor de uma peça específica for necessário.

Segue o mesmo padrão de geração de PDF já usado no recibo financeiro
(app/routes/financeiro.py::recibo — reportlab.pdfgen.canvas de baixo
nível) e reaproveita o timbrado do escritório (app/utils/timbrado.py,
PENDENCIAS.md seção -69) quando cadastrado.
"""
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from app.utils import timbrado

MARGEM = 2.5 * cm
LIMITE_INFERIOR = 2 * cm  # abaixo disso, pula de página


def _formatar_moeda(valor):
    if valor is None:
        return "—"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_data(valor):
    if not valor:
        return "—"
    return valor.strftime("%d/%m/%Y")


def _formatar_data_hora(valor):
    if not valor:
        return "—"
    return valor.strftime("%d/%m/%Y %H:%M")


class _Escritor:
    """Envolve o Canvas do reportlab pra cuidar da posição vertical (y) e
    trocar de página sozinho quando o conteúdo não cabe mais — sem isso,
    cada seção (movimentações, prazos, audiências) teria que repetir a
    mesma lógica de "será que ainda cabe uma linha?" na mão."""

    def __init__(self, c, largura, altura, processo):
        self.c = c
        self.largura = largura
        self.altura = altura
        self.processo = processo
        self.y = altura - MARGEM
        self.pagina = 1

    def _rodape(self):
        self.c.setFont("Helvetica", 7)
        self.c.setFillGray(0.5)
        numero = self.processo.numero_processo or self.processo.numero_interno or "—"
        self.c.drawString(
            MARGEM, 1.2 * cm,
            f"Gerado por JusControl em {datetime.now().strftime('%d/%m/%Y %H:%M')} — "
            f"processo {numero} — página {self.pagina}",
        )
        self.c.setFillGray(0)

    def nova_pagina(self):
        self._rodape()
        self.c.showPage()
        self.pagina += 1
        self.y = self.altura - MARGEM

    def garantir_espaco(self, altura_necessaria):
        if self.y - altura_necessaria < LIMITE_INFERIOR:
            self.nova_pagina()

    def titulo_secao(self, texto):
        self.garantir_espaco(1.2 * cm)
        self.y -= 0.3 * cm
        self.c.setFont("Helvetica-Bold", 12)
        self.c.drawString(MARGEM, self.y, texto)
        self.y -= 0.15 * cm
        self.c.line(MARGEM, self.y, self.largura - MARGEM, self.y)
        self.y -= 0.6 * cm

    def linha(self, texto, negrito=False, tamanho=9, indentacao=0):
        fonte = "Helvetica-Bold" if negrito else "Helvetica"
        largura_max = self.largura - 2 * MARGEM - indentacao
        pedacos = simpleSplit(texto, fonte, tamanho, largura_max) or [""]
        for pedaco in pedacos:
            self.garantir_espaco(0.5 * cm)
            self.c.setFont(fonte, tamanho)
            self.c.drawString(MARGEM + indentacao, self.y, pedaco)
            self.y -= 0.45 * cm

    def linha_vazia(self, altura=0.25):
        self.y -= altura * cm

    def sem_registro(self, texto):
        self.c.setFont("Helvetica-Oblique", 9)
        self.garantir_espaco(0.5 * cm)
        self.c.setFillGray(0.4)
        self.c.drawString(MARGEM, self.y, texto)
        self.c.setFillGray(0)
        self.y -= 0.45 * cm


def gerar_pdf_processo(processo, empresa, unidade, upload_folder) -> io.BytesIO:
    """
    Gera o PDF de resumo do processo em memória (BytesIO, pronto pra
    `send_file`). Não faz nenhuma consulta a rede — só lê o que já está
    cadastrado no processo (capa, partes_texto, movimentações, prazos,
    audiências), então é seguro/rápido chamar quantas vezes quiser.
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4

    y = timbrado.desenhar_cabecalho(c, empresa, unidade, MARGEM, altura - MARGEM, upload_folder)
    escritor = _Escritor(c, largura, altura, processo)
    escritor.y = y - 0.8 * cm

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(largura / 2, escritor.y, "RESUMO DO PROCESSO")
    escritor.y -= 1 * cm

    # ---------- Capa ----------
    numero = processo.numero_processo or processo.numero_interno or "Sem número"
    escritor.linha(numero, negrito=True, tamanho=12)
    escritor.linha_vazia()
    campos_capa = [
        ("Cliente", processo.cliente.nome if processo.cliente else "—"),
        ("Área do direito", processo.area_direito or "—"),
        ("Tipo de ação", processo.tipo_acao or "—"),
        ("Classe processual", processo.classe_processual or "—"),
        ("Assunto (CNJ)", processo.assunto_cnj or "—"),
        ("Fase", processo.fase or "—"),
        ("Tribunal", processo.tribunal or "—"),
        ("Instância", processo.instancia or "—"),
        ("Comarca/Vara", " ".join(p for p in (processo.comarca, processo.vara) if p) or "—"),
        ("Status", processo.status or "—"),
        ("Polo do cliente", processo.polo_cliente or "—"),
        ("Parte contrária", processo.parte_contraria or "—"),
        ("Valor da causa", _formatar_moeda(processo.valor_causa)),
        ("Data de distribuição", _formatar_data(processo.data_distribuicao)),
        ("Responsável", processo.responsavel.nome if processo.responsavel else "—"),
    ]
    for rotulo, valor in campos_capa:
        escritor.linha(f"{rotulo}: {valor}")
    if processo.descricao:
        escritor.linha_vazia()
        escritor.linha(processo.descricao)

    # ---------- Partes ----------
    escritor.titulo_secao("Partes")
    if processo.partes_texto:
        for linha_parte in processo.partes_texto.split("\n"):
            escritor.linha(linha_parte)
    else:
        escritor.sem_registro(
            "Nenhuma parte identificada por captura automática ainda — só \"Parte contrária\" "
            "acima, cadastrada manualmente."
        )

    # ---------- Prazos ----------
    escritor.titulo_secao("Prazos")
    prazos = sorted(processo.prazos, key=lambda p: p.data_vencimento)
    if prazos:
        for prazo in prazos:
            escritor.linha(
                f"{_formatar_data(prazo.data_vencimento)} — {prazo.descricao} "
                f"[{prazo.status}, prioridade {prazo.prioridade}]",
                negrito=True,
            )
            if prazo.observacoes:
                escritor.linha(prazo.observacoes, indentacao=0.4 * cm, tamanho=8)
    else:
        escritor.sem_registro("Nenhum prazo cadastrado.")

    # ---------- Audiências ----------
    escritor.titulo_secao("Audiências")
    audiencias = sorted(processo.audiencias, key=lambda a: a.data_hora)
    if audiencias:
        for audiencia in audiencias:
            marca_auto = " (detectado automaticamente)" if audiencia.deteccao_automatica else ""
            tipo = audiencia.tipo or "audiência"
            escritor.linha(
                f"{_formatar_data_hora(audiencia.data_hora)} — {tipo} [{audiencia.status}]{marca_auto}",
                negrito=True,
            )
            detalhe = " ".join(p for p in (audiencia.local, audiencia.modalidade) if p)
            if detalhe:
                escritor.linha(detalhe, indentacao=0.4 * cm, tamanho=8)
    else:
        escritor.sem_registro("Nenhuma audiência cadastrada.")

    # ---------- Movimentações ----------
    escritor.titulo_secao("Movimentações")
    movimentacoes = sorted(processo.movimentacoes, key=lambda m: m.data, reverse=True)
    if movimentacoes:
        for mov in movimentacoes:
            escritor.linha(f"{_formatar_data_hora(mov.data)} — {mov.texto_integral}", negrito=True)
            if mov.complemento:
                escritor.linha(mov.complemento, indentacao=0.4 * cm, tamanho=8)
    else:
        escritor.sem_registro("Nenhuma movimentação registrada.")

    escritor._rodape()
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer
