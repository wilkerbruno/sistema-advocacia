"""
Conector "e-SAJ público" — captura automática (sem certificado, sem
token, sem login nenhum) direto da consulta pública de 1º grau do e-SAJ —
PENDENCIAS.md, seções -71 (TJSP) e -72 (demais tribunais + busca antes de
cadastrar).

Cobre hoje: TJSP, TJAC, TJAL, TJAM, TJCE e TJMS — os únicos tribunais que
confirmamos rodarem a plataforma e-SAJ (Softplan) olhando o código-fonte do
pacote open source `juscraper` (MIT, https://github.com/jtrecenti/juscraper,
`src/juscraper/courts/{tjsp,tjac,tjal,tjam,tjce,tjms}/`) — a mesma fonte já
usada na seção -71 pra confirmar a URL/parâmetros/campos do TJSP.

⚠️ Sobre COMO cada tribunal é escolhido a partir do número CNJ — leia com
atenção, é uma limitação real, não só um detalhe de implementação:
- TJSP tem código de tribunal "26" confirmado (é o único que tratamos como
  certeza — amplamente documentado e já usado desde a seção -71). Pra esse
  caso o conector sabe direto qual domínio consultar: 1 requisição só.
- Para os outros 5 tribunais (TJAC/TJAL/TJAM/TJCE/TJMS) NÃO existe uma
  tabela confiável e verificada de "código de tribunal (2 dígitos) -> qual
  estado" que desse pra usar aqui com segurança. Isto não é um detalhe
  novo: app/utils/tribunais_datajud.py já tinha decidido, antes desta
  rodada, NÃO adivinhar isso, pelo mesmo motivo (ver o docstring daquele
  arquivo) — o risco é atribuir dados de um processo ao tribunal errado.
  Conferimos também uma biblioteca de terceiros
  (github.com/joaotextor/busca-processos-judiciais) que faz a mesma busca
  contra o DataJud: ela também exige que o tribunal seja informado
  explicitamente por quem chama, nunca adivinha só pelo número.
  Por isso, pra qualquer processo estadual que NÃO seja TJSP, este
  conector tenta os outros 5 tribunais até achar — a MESMA estratégia (e o
  mesmo motivo) que app/utils/conector_datajud.py já usa pra tentar os 27
  tribunais estaduais no DataJud, mas nunca arrisca atribuir o processo
  errado a um tribunal errado: cada tribunal só "acha" um processo que
  realmente exista lá — os demais respondem "não encontrado" de forma
  limpa (mesmo comportamento já tratado desde a seção -71).
- ATUALIZADO NA SEÇÃO -73 (relato real de demora em produção): esses 5
  tribunais são consultados EM PARALELO (não mais um de cada vez), porque
  tentar 5 tribunais em sequência, com qualquer um deles fora do ar/lento,
  deixava a busca visivelmente lenta (cada tentativa esperava até 20s antes
  de desistir e passar pro próximo). Rodando em paralelo, a espera total é
  a do tribunal mais lento, não a soma de todos — e o timeout por tribunal
  também caiu pra 10s nesse caminho (só o TJSP, que é 1 requisição só,
  mantém 20s). Efeito colateral aceito dessa mudança: como as 5 chamadas
  saem praticamente ao mesmo tempo, não dá mais pra "parar antes" de
  consultar um tribunal só porque outro já respondeu — os 5 sempre são
  perguntados, mesmo quando um deles responde rapidinho que está protegido
  por senha (nesse caso a resposta certa já foi encontrada e as demais só
  são descartadas ao chegar, mas a requisição de rede já tinha saído).
  Quando um ou mais tribunais não respondem a tempo, a mensagem de erro
  agora aponta exatamente QUAL(IS) — antes só dizia "N tribunal(is) não
  respondeu/responderam", sem dizer quais, dificultando diagnosticar se é
  um bloqueio específico e permanente (ex: aquele tribunal bloqueia o IP
  do servidor) ou só uma lentidão pontual.
- ATUALIZADO NA SEÇÃO -74: em produção, os mesmos dois tribunais (TJCE e
  TJMS) voltaram a aparecer como "não responderam a tempo" — inclusive já
  tinha acontecido antes da mudança pra paralelo (seção -73), quando cada
  um tinha 20s pra responder em vez de 10s. Isso é evidência de que pode
  não ser só "faltou tempo": pode ser bloqueio/indisponibilidade real
  desses dois tribunais específicos a partir do servidor. Como a mensagem
  mostrada ao usuário precisa ficar simples, o motivo técnico REAL de cada
  falha (timeout puro, conexão recusada, falha de TLS, DNS etc. —
  `requests.RequestException` cobre todos esses casos igual) agora também
  vai pro log do servidor (`current_app.logger.warning`, mesmo padrão já
  usado em app/utils/email.py e app/utils/whatsapp.py) quando um tribunal
  fica indisponível — próxima vez que isso acontecer, os logs do
  servidor (os mesmos que você já colou aqui outras vezes) vão trazer o
  motivo técnico específico de cada tribunal, não só "não respondeu".
- ATUALIZADO NA SEÇÃO -75 (log de produção trouxe a resposta): com o log
  novo da seção -74, o motivo técnico real de cada um dos dois apareceu, e
  são DOIS problemas DIFERENTES, não um só:
  - TJCE: `SSLCertVerificationError` — certificado autoassinado na cadeia
    do servidor do TJCE (comum em sites .jus.br com certificado ICP-Brasil,
    cuja raiz não está no repositório de CAs confiáveis que o Python usa
    por padrão). Isto é CORRIGÍVEL em código — ver aviso completo junto de
    `_TJCETLSAdapter` mais abaixo — e já foi corrigido nesta seção.
  - TJMS: `ConnectTimeoutError` — a conexão nem chegou a ser aberta (timeout
    já na fase de conectar, não de esperar resposta). Esse é o padrão
    clássico de bloqueio de IP em nível de rede/firewall (um "descarte
    silencioso" de pacote, diferente de "conexão recusada" ou "DNS não
    resolvido") — o mesmo risco já avisado no item anterior sobre bloqueio
    de IP por datacenter. NÃO tem correção possível em código pra isto: se
    o tribunal (ou a rede dele) está descartando pacotes vindos do IP do
    seu servidor, nenhum ajuste de timeout ou retry vai resolver. Continua
    em aberto — ver PENDENCIAS.md seção -75 para o que dá pra fazer a
    respeito (nada garantido do lado do código).

⚠️ Demais avisos, já válidos desde a seção -71 e que continuam valendo pra
todos os tribunais cobertos aqui:
- É scraping de página pública HTML, sem documentação nem contrato oficial
  — cada tribunal pode mudar a estrutura da página a qualquer momento e
  quebrar isto silenciosamente até alguém notar. Trate como fonte
  COMPLEMENTAR (mais partes/detalhes quando disponíveis), nunca substituta
  do DataJud (que é uma API oficial do CNJ, com contrato estável).
- Só cobre 1º grau (`cpopg`). Processos de 2º grau não são suportados.
- Processos com sigilo/senha no e-SAJ não são legíveis por esta consulta
  pública (ver `EsajProtegidoPorSenhaError` acima).
- Rodando do servidor (datacenter), existe risco real de bloqueio de IP
  pelo tribunal — o próprio juscraper documenta isso para outros tribunais
  (TJAP passou a exigir CAPTCHA). Se acontecer aqui, `EsajIndisponivelError`
  é levantado com mensagem clara; NÃO há retry automático nem fallback
  silencioso (evita mascarar o problema). Ver TJMS na seção -75 acima —
  provável exemplo real disso.
- TJCE exige uma configuração de TLS mais permissiva (SECLEVEL=1) por causa
  do servidor dele — mesma necessidade documentada pelo próprio juscraper
  (`src/juscraper/courts/tjce/_tls.py`); replicada aqui em `_TJCETLSAdapter`.
  ATUALIZADO NA SEÇÃO -75: o mesmo servidor também apresenta um certificado
  autoassinado na cadeia, então a verificação de certificado é desligada
  SÓ pra este domínio (ver aviso completo junto do `_TJCETLSAdapter` mais
  abaixo) — nenhum dado sensível é enviado nesta consulta (sem
  certificado/login), então o risco aceito é limitado a uma eventual
  resposta forjada por um MITM, não vazamento de credencial.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import requests
import urllib3
from bs4 import BeautifulSoup
from flask import current_app
from requests.adapters import HTTPAdapter

from app.utils.captura_conectores import (
    ConectorCaptura,
    MovimentacaoCapturada,
    PublicacaoCapturada,
    ProcessoEncontradoDueDiligence,
)
from app.utils.cnj import somente_digitos, validar_numero_cnj

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT_SEGUNDOS = 20
# Timeout menor usado só na busca em PARALELO pelos 5 tribunais candidatos
# (ver seção -73) — com 5 chamadas simultâneas, esperar 20s por cada uma
# faria a busca inteira demorar 20s de qualquer jeito se UM tribunal
# estiver fora do ar; 10s é generoso pra um e-SAJ respondendo normalmente
# e mantém a espera total razoável mesmo no pior caso.
TIMEOUT_CANDIDATOS_SEGUNDOS = 10

# Segmento "8" = Justiça Estadual (ver app/utils/cnj.py) — único segmento
# que algum tribunal coberto aqui atende; "26" = TJSP, o único código de
# tribunal que tratamos como confirmado (ver aviso completo no topo do
# arquivo sobre os demais).
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


class _TJCETLSAdapter(HTTPAdapter):
    """TJCE exige TLS SECLEVEL=1 por causa da configuração do servidor
    dele — mesma necessidade já documentada pelo juscraper
    (src/juscraper/courts/tjce/_tls.py). Sem isso, toda requisição pro
    domínio do TJCE falha na negociação TLS, mesmo com a URL certa."""

    def init_poolmanager(self, *args, **kwargs):
        from urllib3.util.ssl_ import create_urllib3_context
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# ATUALIZADO NA SEÇÃO -75 (log de produção confirmou a causa real): o
# certificado do domínio do TJCE tem um certificado AUTOASSINADO na cadeia
# ("self-signed certificate in certificate chain"), que a verificação
# padrão do Python rejeita — problema comum em sites .jus.br que usam
# certificado ICP-Brasil (cuja raiz não faz parte do repositório de CAs
# confiáveis que o Python usa por padrão), diferente do domínio dos outros
# tribunais cobertos aqui. Sem desligar a verificação SÓ pra este domínio,
# toda requisição pro TJCE falhava (SSLCertVerificationError), mesmo com o
# ajuste de SECLEVEL acima. Aceito o mesmo tipo de contrapartida documentada
# já para o SECLEVEL=1 deste tribunal (é scraping de página pública, sem
# certificado nem login, sem nenhum dado sensível enviado — o risco real de
# desligar a verificação aqui é um MITM conseguir forjar uma RESPOSTA falsa
# desta consulta específica, não vazamento de credencial nenhuma, já que
# nenhuma é enviada). Suprime o aviso correspondente do urllib3 pra não
# poluir o log do servidor com um aviso sobre algo já decidido conscientemente.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass(frozen=True)
class _TribunalEsaj:
    slug: str
    nome: str
    base_url: str
    tls_seclevel1: bool = False


# TJSP primeiro (é o único com código de tribunal confirmado — ver aviso no
# topo do arquivo); os demais na ordem em que o juscraper os documenta.
# Domínios conferidos linha por linha contra o BASE_URL de cada scraper em
# src/juscraper/courts/<sigla>/client.py (juscraper, MIT).
TJSP = _TribunalEsaj("tjsp", "TJSP", "https://esaj.tjsp.jus.br/")
CANDIDATOS_DEMAIS_TRIBUNAIS = [
    _TribunalEsaj("tjac", "TJAC", "https://esaj.tjac.jus.br/"),
    _TribunalEsaj("tjal", "TJAL", "https://www2.tjal.jus.br/"),
    _TribunalEsaj("tjam", "TJAM", "https://consultasaj.tjam.jus.br/"),
    _TribunalEsaj("tjce", "TJCE", "https://esaj.tjce.jus.br/", tls_seclevel1=True),
    _TribunalEsaj("tjms", "TJMS", "https://esaj.tjms.jus.br/"),
]
TODOS_TRIBUNAIS = [TJSP] + CANDIDATOS_DEMAIS_TRIBUNAIS


def _nova_sessao_para(tribunal: _TribunalEsaj) -> requests.Session:
    """Sessão nova (não compartilhada) pra consultar um tribunal — usada
    na busca em PARALELO pelos 5 candidatos (ver seção -73):
    `requests.Session` não é documentado como garantidamente seguro pra
    uso concorrente entre threads, então cada chamada em paralelo recebe a
    própria sessão em vez de reaproveitar `self.session`/`self._sessao_tjce`
    (esses continuam existindo só pro caminho direto do TJSP, que nunca
    roda em paralelo com mais nada)."""
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": USER_AGENT})
    if tribunal.tls_seclevel1:
        sessao.mount("https://", _TJCETLSAdapter())
        # Ver aviso completo na seção -75 acima de _TJCETLSAdapter: o
        # certificado do TJCE tem um certificado autoassinado na cadeia,
        # que a verificação padrão rejeita (SSLCertVerificationError) —
        # sem isso a requisição falha antes mesmo do SECLEVEL importar.
        sessao.verify = False
    return sessao


def _tentar_tribunal_candidato(numero_cnj: str, tribunal: _TribunalEsaj) -> tuple:
    """Roda em thread separada (ver ConectorEsajPublico.consultar_processo)
    — nunca levanta exceção pra fora, devolve (status, tribunal, valor)
    pra quem chamou decidir o que fazer com cada resultado à medida que
    forem chegando, na ordem em que completarem (não na ordem de início)."""
    sessao = _nova_sessao_para(tribunal)
    try:
        html = _obter_html_processo(sessao, numero_cnj, tribunal, timeout=TIMEOUT_CANDIDATOS_SEGUNDOS)
        return ("encontrado", tribunal, html)
    except EsajProtegidoPorSenhaError as e:
        return ("senha", tribunal, e)
    except EsajIndisponivelError as e:
        return ("indisponivel", tribunal, e)
    except ErroEsajPublico as e:
        return ("nao_encontrado", tribunal, e)


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
        "partes": partes,
    }


def _obter_html_processo(session: requests.Session, numero_cnj: str, tribunal: _TribunalEsaj,
                          timeout: int = TIMEOUT_SEGUNDOS) -> str:
    dados_busca = _montar_parametros_busca(numero_cnj)
    url = f"{tribunal.base_url}cpopg/search.do"
    try:
        resposta = session.get(url, params=dados_busca["params"], timeout=timeout)
    except requests.RequestException as e:
        raise EsajIndisponivelError(f"Falha de conexão com o e-SAJ do {tribunal.nome}: {e}") from e

    if resposta.status_code != 200:
        raise EsajIndisponivelError(
            f"e-SAJ do {tribunal.nome} respondeu {resposta.status_code} de forma inesperada — pode ser "
            "bloqueio temporário de IP ou instabilidade do tribunal; tente de novo mais tarde."
        )

    soup = BeautifulSoup(resposta.text, "html.parser")

    # Já é a página do processo (achou exatamente um resultado)?
    if soup.find("span", id="numeroProcesso") or soup.find("span", class_="unj-larger"):
        return resposta.text

    # Pediu senha do processo (sigilo) antes de mostrar qualquer coisa?
    if soup.find("form", id="popupSenha"):
        raise EsajProtegidoPorSenhaError(
            f"Este processo está protegido por senha no e-SAJ do {tribunal.nome} — a consulta "
            "pública não alcança os dados sem ela. Submissão de senha não é suportada por este "
            "conector ainda (ver app/models/senha_processo.py para a senha já cadastrada no "
            "JusControl, se houver)."
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
                url_final = f"{tribunal.base_url}{url_processo.lstrip('/')}"
            try:
                resposta2 = session.get(url_final, timeout=timeout)
            except requests.RequestException as e:
                raise EsajIndisponivelError(f"Falha de conexão com o e-SAJ do {tribunal.nome}: {e}") from e
            if resposta2.status_code != 200:
                raise EsajIndisponivelError(
                    f"e-SAJ do {tribunal.nome} respondeu {resposta2.status_code} ao abrir o processo."
                )
            return resposta2.text

    raise ErroEsajPublico(
        f"Processo {dados_busca['formatado']} não encontrado na consulta pública do e-SAJ do "
        f"{tribunal.nome} (1º grau) — confira o número, ou é possível que o processo seja de outro "
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


def _parse_processo(html: str, numero_cnj: str, tribunal_slug: str = "tjsp") -> dict:
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
        "tribunal_slug": tribunal_slug,
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
    """Consulta pública do e-SAJ (TJSP, TJAC, TJAL, TJAM, TJCE, TJMS — 1º
    grau) — sem certificado, sem token, sem login. Ver aviso completo no
    topo do arquivo sobre como cada tribunal é escolhido e as limitações
    em relação ao DataJud."""

    nome_fonte = "esaj_publico"

    def __init__(self, sleep_time: float = 0.0):
        self.sleep_time = sleep_time
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        # Sessão à parte, criada só se/quando precisar — o TJCE exige um
        # adaptador de TLS diferente (ver _TJCETLSAdapter), que não faz
        # sentido montar em toda sessão só por causa de um tribunal.
        self._sessao_tjce: requests.Session | None = None

    def _sessao_para(self, tribunal: _TribunalEsaj) -> requests.Session:
        if not tribunal.tls_seclevel1:
            return self.session
        if self._sessao_tjce is None:
            self._sessao_tjce = requests.Session()
            self._sessao_tjce.headers.update({"User-Agent": USER_AGENT})
            self._sessao_tjce.mount("https://", _TJCETLSAdapter())
            # Ver aviso da seção -75 acima de _TJCETLSAdapter: certificado
            # autoassinado na cadeia do TJCE — verificação padrão rejeitaria.
            self._sessao_tjce.verify = False
        return self._sessao_tjce

    def consultar_processo(self, numero_cnj: str) -> dict:
        validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
        if not validado["valido"]:
            raise ErroEsajPublico(f"Número CNJ inválido: {validado['motivo']}")
        partes = validado["partes"]

        if partes["segmento_codigo"] != SEGMENTO_ESTADUAL:
            nomes = ", ".join(t.nome for t in TODOS_TRIBUNAIS)
            raise ErroEsajPublico(
                f"Este conector só sabe consultar tribunais estaduais que usam a plataforma "
                f"e-SAJ ({nomes}) — o número informado é de outro segmento de Justiça "
                f"(código {partes['segmento_codigo']})."
            )

        if partes["tribunal_codigo"] == TRIBUNAL_TJSP:
            html = _obter_html_processo(self._sessao_para(TJSP), numero_cnj, TJSP)
            return _parse_processo(html, somente_digitos(numero_cnj), TJSP.slug)

        # Código de tribunal diferente do único confirmado (TJSP) — tenta
        # os outros tribunais e-SAJ conhecidos EM PARALELO até achar (ver
        # aviso completo no topo do arquivo sobre por que não adivinhamos
        # qual deles é só pelo número, e sobre a mudança pra paralelo —
        # antes era um de cada vez — feita na seção -73 por causa de
        # relato real de lentidão em produção).
        indisponiveis: list[_TribunalEsaj] = []
        resultado_senha = None
        resultado_encontrado = None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(CANDIDATOS_DEMAIS_TRIBUNAIS))
        futuros = {
            executor.submit(_tentar_tribunal_candidato, numero_cnj, tribunal): tribunal
            for tribunal in CANDIDATOS_DEMAIS_TRIBUNAIS
        }
        try:
            for futuro in concurrent.futures.as_completed(futuros):
                status, tribunal, valor = futuro.result()
                if status == "encontrado":
                    resultado_encontrado = (tribunal, valor)
                    break  # já achou — não precisa esperar os outros terminarem
                if status == "senha":
                    resultado_senha = (tribunal, valor)
                    break  # achou o tribunal certo, só não tem acesso sem senha
                if status == "indisponivel":
                    indisponiveis.append(tribunal)
                    # ATUALIZADO NA SEÇÃO -74: a mensagem que o usuário vê
                    # (mais abaixo) precisa ficar simples/não-técnica, então
                    # o motivo técnico REAL (timeout puro? recusa de
                    # conexão? falha de TLS? DNS?) só vai pro log do
                    # servidor — é o que precisamos pra saber se é só
                    # lentidão (aumentar o timeout resolveria) ou bloqueio
                    # de fato (não resolveria). Roda na thread principal
                    # (não na thread de rede), então `current_app` funciona
                    # normalmente aqui, EXCETO em teste unitário direto (sem
                    # request Flask nenhuma) — nesse caso só não loga, nunca
                    # deixa uma falha de log quebrar a consulta de verdade.
                    try:
                        current_app.logger.warning(
                            f"e-SAJ público: {tribunal.nome} não respondeu à consulta do processo "
                            f"{numero_cnj} dentro do prazo — motivo técnico: {valor}"
                        )
                    except RuntimeError:
                        pass
                # "nao_encontrado": não faz nada especial, só segue esperando os outros
        finally:
            # wait=False: não trava a resposta esperando os tribunais que
            # ainda não terminaram quando já temos o que precisamos (achou,
            # ou achou a tela de senha) — as threads restantes terminam
            # sozinhas em segundo plano e são descartadas.
            executor.shutdown(wait=False)

        if resultado_encontrado:
            tribunal, html = resultado_encontrado
            return _parse_processo(html, somente_digitos(numero_cnj), tribunal.slug)
        if resultado_senha:
            _, erro = resultado_senha
            raise erro

        nomes = ", ".join(t.nome for t in CANDIDATOS_DEMAIS_TRIBUNAIS)
        if len(indisponiveis) == len(CANDIDATOS_DEMAIS_TRIBUNAIS):
            raise EsajIndisponivelError(
                f"Não foi possível consultar nenhum dos tribunais e-SAJ testados ({nomes}) agora — "
                "todos falharam ao responder. Tente de novo mais tarde."
            )
        aviso_indisponiveis = ""
        if indisponiveis:
            nomes_indisponiveis = ", ".join(t.nome for t in indisponiveis)
            # Nomeia EXATAMENTE quais tribunais não responderam (antes só
            # dizia "N tribunal(is)", sem dizer quais — dificultava saber
            # se é um bloqueio específico e permanente daquele tribunal ou
            # só lentidão pontual; ver seção -73).
            aviso_indisponiveis = (
                f" ({nomes_indisponiveis} não responderam a tempo e não puderam ser conferidos agora "
                "— pode estar lá mesmo assim)"
            )
        raise ErroEsajPublico(
            f"Processo não encontrado em nenhum dos tribunais e-SAJ testados ({nomes})"
            f"{aviso_indisponiveis} — confira o número, ou pode ser de um tribunal/segredo de "
            "justiça que este conector ainda não alcança."
        )

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
