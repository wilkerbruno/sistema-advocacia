"""
Conector "e-SAJ público" — captura automática (sem certificado, sem
token, sem login nenhum) direto da consulta pública de 1º grau do e-SAJ
(TJSP), a mesma página que qualquer pessoa acessa em
https://esaj.tjsp.jus.br/cpopg/open.do sem estar logada — PENDENCIAS.md,
seção -71.

Origem da lógica: portado (estrutura de URL/parâmetros e nomes de
campo/IDs do HTML) do pacote open source `juscraper`
(https://github.com/jtrecenti/juscraper, licença MIT,
`src/juscraper/courts/tjsp/cpopg_download.py` e `cpopg_parse.py`) — a
única fonte que encontramos com esses detalhes confirmados contra o
e-SAJ de verdade. Reimplementado aqui sem a dependência de `pandas`
(devolve `dict`/`list` simples, não DataFrame) pra não engordar as
dependências do servidor Flask só por causa disto, e adaptado ao
contrato `ConectorCaptura` já usado pelo DataJud (ver
app/utils/captura_conectores.py).

⚠️ Diferença importante em relação ao DataJud (app/utils/conector_datajud.py):
- DataJud é uma API OFICIAL do CNJ, documentada, com contrato estável.
- Isto aqui é **scraping de página pública HTML**, sem documentação nem
  contrato oficial — o TJSP pode mudar a estrutura da página a qualquer
  momento e quebrar isto silenciosamente até alguém notar. Trate como
  fonte COMPLEMENTAR (mais partes/detalhes quando disponíveis), nunca
  como substituta do DataJud.
- Só cobre TJSP (1º grau, `cpopg`). Processos de outros tribunais, ou de
  2º grau, não são suportados por este conector.
- Processos com sigilo/senha no e-SAJ (a mesma "senha do processo" que
  o JusControl já modela em app/models/senha_processo.py) não são
  legíveis por esta consulta pública — o e-SAJ mostra uma tela pedindo
  a senha em vez dos dados; este conector detecta isso e levanta
  `EsajProtegidoPorSenhaError` em vez de fingir que não achou nada.
- Rodando do servidor (datacenter), existe risco real de bloqueio de IP
  pelo tribunal — o próprio juscraper documenta isso para outros
  tribunais (TJAP passou a exigir CAPTCHA). Se isso acontecer aqui,
  `EsajIndisponivelError` é levantado com uma mensagem clara; NÃO há
  fallback automático (evita mascarar o problema).
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from app.utils.captura_conectores import (
    ConectorCaptura,
    MovimentacaoCapturada,
    PublicacaoCapturada,
    ProcessoEncontradoDueDiligence,
)
from app.utils.cnj import somente_digitos, validar_numero_cnj

BASE_URL = "https://esaj.tjsp.jus.br/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT_SEGUNDOS = 20

# Segmento "8" = Justiça Estadual, tribunal "26" = TJSP — únicos números
# CNJ que este conector sabe consultar (ver app/utils/cnj.py).
SEGMENTO_ESTADUAL = "8"
TRIBUNAL_TJSP = "26"


class ErroEsajPublico(Exception):
    """Erro esperado (processo não encontrado, tribunal fora do ar,
    resposta em formato inesperado etc.) — mensagem já pronta para
    mostrar ao usuário."""


class EsajProtegidoPorSenhaError(ErroEsajPublico):
    """O e-SAJ pediu a senha do processo antes de mostrar qualquer dado —
    consulta pública sem senha não alcança este processo."""


class EsajIndisponivelError(ErroEsajPublico):
    """Falha de rede, CAPTCHA ou bloqueio — não é "processo não existe",
    é "não deu pra perguntar" (ver aviso sobre bloqueio de IP no topo do
    arquivo)."""


def _parse_data_br(texto: str | None) -> datetime | None:
    """Aceita 'dd/mm/aaaa' ou 'dd/mm/aaaa às HH:MM - ...' (formato do
    campo de distribuição). Devolve None em vez de levantar exceção —
    campo secundário, não deve travar a captura inteira."""
    if not texto:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})(?:\s+às\s+(\d{2}):(\d{2}))?", texto)
    if not m:
        return None
    dia, mes, ano, hora, minuto = m.groups()
    try:
        return datetime(int(ano), int(mes), int(dia), int(hora or 0), int(minuto or 0))
    except ValueError:
        return None


def _texto_valor(texto: str | None) -> float | None:
    """'R$ 12.345,67' -> 12345.67. None em qualquer formato inesperado."""
    if not texto:
        return None
    limpo = re.sub(r"[^\d,.]", "", texto).replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _montar_parametros_busca(numero_cnj: str) -> dict:
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"]:
        raise ErroEsajPublico(f"Número CNJ inválido: {validado['motivo']}")
    partes = validado["partes"]
    if partes["segmento_codigo"] != SEGMENTO_ESTADUAL or partes["tribunal_codigo"] != TRIBUNAL_TJSP:
        raise ErroEsajPublico(
            "Este conector só sabe consultar o TJSP (Justiça Estadual, tribunal 26) — "
            f"o número informado é de outro tribunal (segmento {partes['segmento_codigo']}, "
            f"tribunal {partes['tribunal_codigo']})."
        )
    return {
        "params": {
            "conversationId": "",
            "cbPesquisa": "NUMPROC",
            "numeroDigitoAnoUnificado": f"{partes['sequencial']}-{partes['digito_verificador']}.{partes['ano']}",
            "foroNumeroUnificado": partes["origem_codigo"],
            "dadosConsulta.valorConsultaNuUnificado": partes["formatado"],
            "dadosConsulta.valorConsulta": "",
            "dadosConsulta.tipoNuProcesso": "UNIFICADO",
        },
        "formatado": partes["formatado"],
    }


def _obter_html_processo(session: requests.Session, numero_cnj: str) -> str:
    dados_busca = _montar_parametros_busca(numero_cnj)
    url = f"{BASE_URL}cpopg/search.do"
    try:
        resposta = session.get(url, params=dados_busca["params"], timeout=TIMEOUT_SEGUNDOS)
    except requests.RequestException as e:
        raise EsajIndisponivelError(f"Falha de conexão com o e-SAJ: {e}") from e

    if resposta.status_code != 200:
        raise EsajIndisponivelError(
            f"e-SAJ respondeu {resposta.status_code} de forma inesperada — pode ser bloqueio "
            "temporário de IP ou instabilidade do tribunal; tente de novo mais tarde."
        )

    soup = BeautifulSoup(resposta.text, "html.parser")

    # Já é a página do processo (achou exatamente um resultado)?
    if soup.find("span", id="numeroProcesso") or soup.find("span", class_="unj-larger"):
        return resposta.text

    # Pediu senha do processo (sigilo) antes de mostrar qualquer coisa?
    if soup.find("form", id="popupSenha"):
        raise EsajProtegidoPorSenhaError(
            "Este processo está protegido por senha no e-SAJ — a consulta pública não "
            "alcança os dados sem ela. Submissão de senha não é suportada por este "
            "conector ainda (ver app/models/senha_processo.py para a senha já cadastrada "
            "no JusControl, se houver)."
        )

    # Lista de resultados (mais de uma tramitação/instância com o mesmo
    # número) — segue o primeiro link, mesmo comportamento do juscraper.
    listagem = soup.find("div", id="listagemDeProcessos")
    if listagem:
        link = listagem.find("a", href=True)
        if link:
            url_processo = link["href"]
            if url_processo.startswith("http"):
                url_final = url_processo
            else:
                url_final = f"{BASE_URL}{url_processo.lstrip('/')}"
            try:
                resposta2 = session.get(url_final, timeout=TIMEOUT_SEGUNDOS)
            except requests.RequestException as e:
                raise EsajIndisponivelError(f"Falha de conexão com o e-SAJ: {e}") from e
            if resposta2.status_code != 200:
                raise EsajIndisponivelError(f"e-SAJ respondeu {resposta2.status_code} ao abrir o processo.")
            return resposta2.text

    raise ErroEsajPublico(
        f"Processo {dados_busca['formatado']} não encontrado na consulta pública do e-SAJ "
        "(TJSP, 1º grau) — confira o número, ou é possível que o processo seja de outro "
        "tribunal/grau, ou tenha sido baixado/arquivado fora do e-SAJ."
    )


def _extrair_texto(soup: BeautifulSoup, seletor_tag: str, seletor_id: str) -> str | None:
    tag = soup.find(seletor_tag, id=seletor_id)
    return tag.get_text(strip=True) if tag else None


def _parse_partes(soup: BeautifulSoup) -> list[dict]:
    partes = []
    tabela = soup.find("table", id="tablePartesPrincipais")
    if not tabela:
        return partes
    for tr in tabela.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        tipo_tag = tds[0].find("span", class_="tipoDeParticipacao")
        tipo_parte = tipo_tag.get_text(strip=True) if tipo_tag else tds[0].get_text(strip=True)

        raw_text = tds[1].get_text("||", strip=True)
        advogados: list[str] = []
        if "Advogado:" in raw_text:
            nome_parte, _, resto = raw_text.partition("Advogado:")
            nome_parte = nome_parte.replace("||", " ").strip()
            advogados = [a.strip() for a in resto.replace("||", " ").split(",") if a.strip()]
        else:
            nome_parte = raw_text.replace("||", " ").strip()

        if nome_parte:
            partes.append({"tipo": tipo_parte, "nome": nome_parte, "advogados": advogados})
    return partes


def _parse_movimentacoes(soup: BeautifulSoup, numero_cnj: str) -> list[MovimentacaoCapturada]:
    movimentacoes = []
    tabela = soup.find("tbody", id="tabelaTodasMovimentacoes")
    if not tabela:
        return movimentacoes
    for tr in tabela.find_all("tr", class_="containerMovimentacao"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        data_texto = tds[0].get_text(strip=True)
        data = _parse_data_br(data_texto)
        if not data:
            continue

        descricao_html = tds[2]
        descricao_principal = (descricao_html.find(string=True, recursive=False) or "").strip()
        span_obs = descricao_html.find("span", style=re.compile(r"italic"))
        observacao = span_obs.get_text(strip=True) if span_obs else ""
        texto_integral = descricao_principal or "Movimentação sem descrição"
        complemento = observacao or None

        # Sem "código TPU" nenhum disponível na página pública do e-SAJ
        # (diferente do DataJud) — o hash de dedup usa só
        # processo+data+texto. Isso é uma limitação aceita: a MESMA
        # movimentação capturada depois também pelo DataJud (que inclui
        # código na chave) vai gerar um hash diferente e aparecer
        # duplicada — ver PENDENCIAS.md seção -71. Preferimos isso a
        # inventar um "código" que a página não fornece.
        hash_dedup = hashlib.sha256(
            f"esaj|{numero_cnj}|{data.isoformat()}|{texto_integral}".encode()
        ).hexdigest()

        movimentacoes.append(MovimentacaoCapturada(
            data=data, codigo_tpu=None, texto_integral=texto_integral,
            hash_dedup=hash_dedup, complemento=complemento,
        ))
    return movimentacoes


def _parse_processo(html: str, numero_cnj: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    classe = _extrair_texto(soup, "span", "classeProcesso")
    assunto = _extrair_texto(soup, "span", "assuntoProcesso")
    foro = _extrair_texto(soup, "span", "foroProcesso")
    vara = _extrair_texto(soup, "span", "varaProcesso")

    # Fallback (processos de incidente/cumprimento de sentença, sem os
    # IDs acima — mesmo comportamento observado pelo juscraper): classe +
    # número saem juntos num <span class="unj-larger">.
    if not classe:
        larger = soup.find("span", class_="unj-larger")
        if larger:
            texto = larger.get_text(strip=True)
            classe = re.sub(r"\s*\(.*$", "", texto).strip() or None

    distribuicao_tag = soup.find("div", id="dataHoraDistribuicaoProcesso")
    data_ajuizamento = _parse_data_br(distribuicao_tag.get_text(strip=True)) if distribuicao_tag else None

    valor_tag = soup.find("div", id="valorAcaoProcesso")
    valor_causa = _texto_valor(valor_tag.get_text(strip=True)) if valor_tag else None

    partes = _parse_partes(soup)
    movimentacoes = _parse_movimentacoes(soup, numero_cnj)

    return {
        "tribunal_slug": "tjsp",
        "classe": classe,
        "assunto": assunto,
        "assuntos_lista": [assunto] if assunto else [],
        "orgao_julgador": vara,
        "comarca": foro,
        "instancia": "1º grau",
        "data_ajuizamento": data_ajuizamento,
        "valor_causa": valor_causa,
        "movimentacoes": movimentacoes,
        "partes": partes,
        "sistema": "e-SAJ",
        "formato": None,
        "nivel_sigilo": None,
    }


class ConectorEsajPublico(ConectorCaptura):
    """Consulta pública do e-SAJ (TJSP, 1º grau) — sem certificado, sem
    token, sem login. Ver aviso completo no topo do arquivo sobre as
    limitações em relação ao DataJud."""

    nome_fonte = "esaj_publico"

    def __init__(self, sleep_time: float = 0.0):
        self.sleep_time = sleep_time
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def consultar_processo(self, numero_cnj: str) -> dict:
        html = _obter_html_processo(self.session, numero_cnj)
        return _parse_processo(html, somente_digitos(numero_cnj))

    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str) -> list[PublicacaoCapturada]:
        raise NotImplementedError(
            "O e-SAJ público não expõe monitoramento de publicações por OAB — "
            "isso exigiria um provedor pago (ver app/utils/captura_conectores.py)."
        )

    def buscar_processos_por_parte(self, cpf_cnpj: str | None = None,
                                    nome: str | None = None) -> list[ProcessoEncontradoDueDiligence]:
        raise NotImplementedError(
            "Busca por parte não implementada neste conector — a consulta pública do "
            "e-SAJ por nome/documento existe, mas não foi portada ainda (fast-follow)."
        )
