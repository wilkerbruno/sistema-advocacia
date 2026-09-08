"""
Conector "PJe público" — captura automática (sem certificado, sem token,
sem login nenhum) direto da consulta pública de 1º grau do PJe —
PENDENCIAS.md, seção -78. Pedido do usuário: "quero que funcione igual o
e-SAJ para todos os outros tribunais também" (ver
app/utils/conector_esaj_publico.py para a mesma ideia já aplicada à
plataforma e-SAJ/Softplan).

⚠️ Por que só TJRJ e TJMG, e não "todos os outros tribunais" — leia com
atenção antes de assumir que falta só "ligar" mais tribunais aqui. O
e-SAJ é UMA plataforma (Softplan) usada por poucos tribunais; fora dela,
cada tribunal roda um sistema diferente (PJe, Projudi, sistema próprio),
e o Brasil está no meio de uma migração nacional coordenada pelo CNJ de
PJe/SAJ para um sistema mais novo chamado "eproc" ao longo de 2026 — ou
seja, mesmo entre tribunais que usam PJe, uma fatia dos processos pode já
estar migrada para o eproc e não aparecer mais aqui. Pesquisei os 4
tribunais pedidos (TJMG, TJRJ, TJRS, TJPR) antes de escrever qualquer
código — mesma disciplina de nunca adivinhar já usada em
app/utils/tribunais_datajud.py — e o resultado, tribunal por tribunal
(pesquisa de 08/09/2026):

- **TJRJ — COBERTO abaixo.** Roda PJe como sistema principal hoje.
  Migração para eproc começou em jan/2025 mas está avançando por
  competência (parcial, não tudo de uma vez). A consulta pública em
  https://tjrj.pje.jus.br/pje/ConsultaPublica/listView.seam é real: sem
  login, sem CAPTCHA aparente, busca por número/parte/advogado/CPF-CNPJ/
  OAB (confirmado acessando a página).
- **TJMG — COBERTO abaixo, com aviso.** Também expõe consulta pública
  PJe (https://pje-consulta-publica.tjmg.jus.br/) — mas está NO MEIO de
  uma migração PJe -> eproc iniciada em set/2025, em 5 fases, conclusão
  prevista pra maio/2026 (ou seja, na data de hoje já deve estar
  bem avançada ou concluída). Processos mais recentes/ativos do TJMG
  podem já ter migrado pro eproc e não aparecer mais nesta consulta —
  tentamos mesmo assim (processos mais antigos/parados ainda devem estar
  lá), mas espere um retorno incompleto, tendendo a piorar com o tempo.
- **TJRS — NÃO IMPLEMENTADO nesta rodada.** O Rio Grande do Sul já roda
  "eproc" como sistema PRINCIPAL hoje (não é migração em andamento) — o
  endereço PJe que existe (`legado.tjrs.jus.br`) é só pra processos
  antigos, não é onde a maioria dos processos ativos está. Não existe,
  até onde pesquisei, nenhuma implementação de referência aberta (nem no
  juscraper, nem em qualquer outro lugar) de uma consulta pública do
  eproc para tribunal ESTADUAL sem login — os únicos exemplos
  confirmados de eproc que encontrei são de Tribunais Regionais
  Federais, com estrutura de página diferente desta aqui. Implementar
  "no escuro" arriscaria produzir um conector que parece funcionar mas
  nunca acha nada, ou pior, acha a coisa errada — ver PENDENCIAS.md,
  seção -78, pra retomar isso com pesquisa própria quando fizer sentido.
- **TJPR — NÃO IMPLEMENTADO nesta rodada.** Roda Projudi (sistema
  próprio do Paraná, não é PJe nem eproc) hoje; a migração pro eproc só
  começa em abril/2026 (9 fases, previsão de concluir em jan/2027) — ou
  seja, é o alvo mais ESTÁVEL dos quatro pedidos por enquanto, mas o
  Projudi é uma arquitetura totalmente diferente do PJe. A página
  pública dele (`consulta.tjpr.jus.br/projudi_consulta/`) é renderizada
  via JavaScript pesado, e não consegui inspecionar o formulário real
  por trás do JS a partir daqui, nem encontrar uma implementação de
  referência aberta de consulta de PROCESSO (só de jurisprudência) no
  Projudi que confirmasse os campos sem adivinhar — ver PENDENCIAS.md,
  seção -78.

⚠️ Achado à parte, sobre um recurso JÁ EXISTENTE (não é sobre este
arquivo): durante esta pesquisa descobrimos que o próprio TJSP (coberto
por app/utils/conector_esaj_publico.py) está migrando processos do e-SAJ
pro eproc desde 2025, por área (Cível já migrou, Fazenda Pública a partir
de agosto/2026) — ver PENDENCIAS.md, seção -78, para o aviso completo.
Não é uma correção necessária agora, só um heads-up: uma fatia crescente
de processos do TJSP deve parar de aparecer no e-SAJ público com o
tempo, não por bug, mas porque o processo mudou de sistema de verdade.

⚠️ Origem do código deste conector: a estrutura de campos/fluxo
(formulário JSF com IDs gerados em tempo de execução, token "ca" de
conversa Seam, parsing por rótulo visível em vez de ID fixo) é replicada
do pacote open source `juscraper` (MIT,
https://github.com/jtrecenti/juscraper), especificamente do scraper que
ele mantém para a "família ConsultaPública do PJe" dos Tribunais
Regionais Federais (`src/juscraper/courts/_trf/`, usado por TRF1/TRF3/
TRF5) — é a ÚNICA implementação de referência aberta e testada de uma
consulta pública de PJe (sem login) que encontramos, para qualquer
tribunal. O PJe é um produto único do CNJ implantado por vários
tribunais (federais e estaduais) com a mesma base de código — daí a
mesma estrutura de formulário ter uma chance real de funcionar contra
TJRJ/TJMG também —, mas ISTO NÃO FOI CONFIRMADO contra um processo real
de TJRJ/TJMG (diferente do e-SAJ, já testado e ajustado contra processos
reais nas seções -71 a -76 do PENDENCIAS.md): não há como acessar
`tjrj.pje.jus.br` / `pje-consulta-publica.tjmg.jus.br` a partir do
ambiente onde este código foi escrito para confirmar de verdade. Ou
seja: é esperado que a PRIMEIRA tentativa real precise de 1-2 ajustes
(nomes de campo diferentes, por exemplo) — do mesmo jeito que aconteceu
com o e-SAJ do TJCE/TJMS. Quando testar, copie a mensagem de erro exata
(e o log do servidor, se houver) de volta — e, se dermos sorte, funciona
de primeira.

⚠️ Diferenças confirmadas em relação ao e-SAJ público, que afetam o que
este conector consegue devolver:
- **Sigilo/segredo de justiça**: o e-SAJ mostra uma tela pedindo senha
  (detectável, ver `EsajProtegidoPorSenhaError`); o aviso oficial da
  consulta pública do PJe diz que processos em segredo de justiça
  simplesmente "não retornam resultado" — ou seja, aqui NÃO dá pra
  distinguir "sigiloso" de "realmente não existe": os dois casos caem em
  `ErroPjePublico` (processo não encontrado), sem detalhe extra.
- **Advogados**: a tabela de partes do PJe (polo ativo/passivo) só
  expõe o NOME da parte e a situação processual (ex: "Citado") — não o
  nome do advogado, diferente do e-SAJ (que tem "Advogado: Fulano" no
  mesmo bloco). `partes_texto` (PENDENCIAS.md, seção -77) vem sem
  advogados pra processos capturados por este conector.
- **Paginação de movimentações**: o PJe pagina a tabela de andamentos
  (15 por página) quando há mais de 15 — esta primeira versão só lê a
  PRIMEIRA página (as movimentações mais recentes aparecem primeiro).
  Processos com histórico longo terão só os últimos ~15 andamentos
  capturados por aqui; capturas seguintes (quando o motor de captura
  rodar de novo) pegam qualquer andamento novo normalmente, então isso
  só afeta a CARGA INICIAL de processos antigos, não o acompanhamento
  dali pra frente. Ver PENDENCIAS.md, seção -78, se isso incomodar na
  prática (dá pra portar a paginação do juscraper depois).

⚠️ Avisos que já valiam para o e-SAJ público e continuam valendo aqui:
- É scraping de página pública HTML, sem documentação nem contrato
  oficial — o tribunal pode mudar a página a qualquer momento e quebrar
  isto silenciosamente até alguém notar. Fonte COMPLEMENTAR, nunca
  substituta do DataJud.
- Só cobre 1º grau. Processos de 2º grau não são suportados.
- Rodando do servidor (datacenter), existe risco real de bloqueio de IP
  pelo tribunal — se acontecer, `PjeIndisponivelError` é levantado com
  mensagem clara; sem retry automático nem fallback silencioso.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from flask import current_app

from app.utils.captura_conectores import (
    ConectorCaptura,
    MovimentacaoCapturada,
    PublicacaoCapturada,
    ProcessoEncontradoDueDiligence,
)
from app.utils.cnj import somente_digitos, validar_numero_cnj

import warnings
# O detalhe do processo vem como XHTML com prólogo <?xml?>; o parser HTML
# do BeautifulSoup lida bem com isso, mas emite um aviso repetitivo a
# cada parse — mesmo tratamento que o juscraper dá (courts/_trf/parse.py).
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT_SEGUNDOS = 15

SEGMENTO_ESTADUAL = "8"

LISTVIEW_PATH = "ConsultaPublica/listView.seam"
DETAIL_PATH = "ConsultaPublica/DetalheProcessoConsultaPublica/listView.seam"


class ErroPjePublico(Exception):
    """Erro esperado (processo não encontrado — inclusive sigilo, que a
    consulta pública do PJe não distingue de "não existe" — ver aviso no
    topo do arquivo) — mensagem já pronta para mostrar ao usuário."""


class PjeIndisponivelError(ErroPjePublico):
    """Falha de rede, formulário com estrutura inesperada, ou bloqueio —
    não é "processo não existe", é "não deu pra perguntar"."""


@dataclass(frozen=True)
class _TribunalPje:
    slug: str
    nome: str
    base_url: str
    # TRF1/TRF3 (e, aparentemente, a maioria dos deploys estaduais) usam
    # o campo autocomplete "classeJudicial"; alguns deploys (TRF5, no
    # juscraper) usam um popup diferente — ver aviso no topo do arquivo
    # sobre isto não estar confirmado contra TJRJ/TJMG de verdade ainda.
    classe_field_name: str = "classeJudicial"


CANDIDATOS = [
    _TribunalPje("tjrj", "TJRJ", "https://tjrj.pje.jus.br/pje/"),
    _TribunalPje("tjmg", "TJMG", "https://pje-consulta-publica.tjmg.jus.br/pje/"),
]


@dataclass(frozen=True)
class _FormFieldIds:
    processo_referencia: str
    nome_advogado: str
    classe: str
    oab_decoration: str
    search_trigger: str


def _extrair_form_field_ids(form_html: str, classe_field_name: str) -> _FormFieldIds:
    """Extrai os IDs `j_idNNN` gerados dinamicamente pelo JSF na página do
    formulário — variam a cada deploy/reinício do PJe, então precisam ser
    lidos de novo a cada sessão nova (mesma técnica do juscraper,
    `courts/_trf/download.py::extract_form_field_ids`)."""

    def _achar(padrao: str, rotulo: str) -> str:
        m = re.search(padrao, form_html)
        if not m:
            raise PjeIndisponivelError(
                f"Não encontrei o campo \"{rotulo}\" no formulário de consulta pública — a "
                "estrutura da página deste tribunal pode ser diferente do esperado (ver aviso "
                "no topo de app/utils/conector_pje_publico.py) ou o PJe pode ter mudado a página."
            )
        return m.group(1)

    processo_ref_id = _achar(r'name="fPP:(j_id\d+):processoReferenciaInput"', "processoReferenciaInput")
    nome_adv_id = _achar(r'name="fPP:(j_id\d+):nomeAdv"', "nomeAdv")
    classe_id = _achar(rf'name="fPP:(j_id\d+):{re.escape(classe_field_name)}"', classe_field_name)
    oab_deco_id = _achar(r'name="fPP:Decoration:(j_id\d+)"', "Decoration:j_idNNN")

    m = re.search(
        r"'parameters':\s*\{\s*'(fPP:j_id\d+)'\s*:\s*'fPP:j_id\d+'\s*\}",
        form_html,
    )
    if not m:
        raise PjeIndisponivelError(
            "Não encontrei o botão de busca (executarPesquisa) no formulário de consulta pública "
            "— a estrutura da página deste tribunal pode ser diferente do esperado."
        )

    return _FormFieldIds(
        processo_referencia=processo_ref_id,
        nome_advogado=nome_adv_id,
        classe=classe_id,
        oab_decoration=oab_deco_id,
        search_trigger=m.group(1),
    )


def _montar_payload_busca(numero_formatado: str, ids: _FormFieldIds) -> dict:
    """Monta o corpo POST `application/x-www-form-urlencoded` — réplica do
    payload que o próprio JS do PJe monta quando só o número do processo é
    preenchido (todos os outros filtros vão vazios, mas os campos de
    controle precisam estar presentes mesmo assim; ver juscraper,
    `courts/_trf/download.py::build_search_payload`)."""
    return {
        "AJAXREQUEST": "_viewRoot",
        "fPP:numProcesso-inputNumeroProcessoDecoration:numProcesso-inputNumeroProcesso": numero_formatado,
        "mascaraProcessoReferenciaRadio": "on",
        f"fPP:{ids.processo_referencia}:processoReferenciaInput": "",
        "fPP:dnp:nomeParte": "",
        f"fPP:{ids.nome_advogado}:nomeAdv": "",
        f"fPP:{ids.classe}:classeJudicial": "",
        f"fPP:{ids.classe}:sgbClasseJudicial_selection": "",
        "fPP:dataAutuacaoDecoration:dataAutuacaoInicioInputDate": "",
        "fPP:dataAutuacaoDecoration:dataAutuacaoFimInputDate": "",
        "tipoMascaraDocumento": "on",
        "fPP:dpDec:documentoParte": "",
        "fPP:Decoration:numeroOAB": "",
        f"fPP:Decoration:{ids.oab_decoration}": "",
        "fPP:Decoration:estadoComboOAB": "org.jboss.seam.ui.NoSelectionConverter.noSelectionValue",
        "fPP": "fPP",
        "autoScroll": "",
        "javax.faces.ViewState": "j_id1",
        ids.search_trigger: ids.search_trigger,
        "AJAX:EVENTS_COUNT": "1",
    }


_CA_TOKEN_RE = re.compile(r"ca=([0-9a-f]+)")


def _obter_html_processo(sessao: requests.Session, numero_cnj: str, tribunal: _TribunalPje,
                          timeout: int = TIMEOUT_SEGUNDOS) -> str:
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"]:
        raise ErroPjePublico(f"Número CNJ inválido: {validado['motivo']}")
    numero_formatado = validado["partes"]["formatado"]

    url_form = f"{tribunal.base_url}{LISTVIEW_PATH}"
    try:
        resp_form = sessao.get(url_form, timeout=timeout)
    except requests.RequestException as e:
        raise PjeIndisponivelError(f"Falha de conexão com o PJe do {tribunal.nome}: {e}") from e
    if resp_form.status_code != 200:
        raise PjeIndisponivelError(
            f"PJe do {tribunal.nome} respondeu {resp_form.status_code} ao abrir o formulário de "
            "consulta pública — pode ser bloqueio temporário de IP ou instabilidade do tribunal."
        )

    ids = _extrair_form_field_ids(resp_form.text, tribunal.classe_field_name)
    payload = _montar_payload_busca(numero_formatado, ids)

    try:
        resp_busca = sessao.post(
            url_form, data=payload, timeout=timeout,
            headers={
                "Referer": url_form,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
    except requests.RequestException as e:
        raise PjeIndisponivelError(f"Falha de conexão com o PJe do {tribunal.nome}: {e}") from e
    if resp_busca.status_code != 200:
        raise PjeIndisponivelError(
            f"PJe do {tribunal.nome} respondeu {resp_busca.status_code} de forma inesperada ao "
            "buscar o processo."
        )

    m = _CA_TOKEN_RE.search(resp_busca.text)
    if not m:
        # Sem token "ca" = PJe não achou nada pra mostrar. A própria
        # consulta pública do PJe avisa que isso também acontece pra
        # processo em segredo de justiça (ver aviso no topo do arquivo) —
        # não dá pra distinguir os dois casos daqui.
        raise ErroPjePublico(
            f"Processo {numero_formatado} não encontrado na consulta pública do PJe do "
            f"{tribunal.nome} (1º grau) — confira o número, ou pode ser de outro tribunal/grau, "
            "estar em segredo de justiça (a consulta pública do PJe não distingue isso de "
            "\"não encontrado\"), ou ter sido baixado/arquivado fora do PJe."
        )

    url_detalhe = f"{tribunal.base_url}{DETAIL_PATH}"
    try:
        resp_detalhe = sessao.get(
            url_detalhe, params={"ca": m.group(1)}, timeout=timeout,
            headers={"Referer": url_form},
        )
    except requests.RequestException as e:
        raise PjeIndisponivelError(f"Falha de conexão com o PJe do {tribunal.nome}: {e}") from e
    if resp_detalhe.status_code != 200:
        raise PjeIndisponivelError(
            f"PJe do {tribunal.nome} respondeu {resp_detalhe.status_code} ao abrir o detalhe do "
            "processo."
        )
    # Página de detalhe é servida sem charset declarado mas com bytes que
    # não são UTF-8 válido (mesmo comportamento documentado pelo
    # juscraper para a família TRF) — latin-1 decodifica sem perda.
    return resp_detalhe.content.decode("latin-1")


def _nova_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": USER_AGENT})
    return sessao


def _tentar_tribunal_candidato(numero_cnj: str, tribunal: _TribunalPje) -> tuple:
    """Roda em thread separada (mesmo padrão de
    conector_esaj_publico.py::_tentar_tribunal_candidato) — nunca levanta
    exceção pra fora, devolve (status, tribunal, valor)."""
    sessao = _nova_sessao()
    try:
        html = _obter_html_processo(sessao, numero_cnj, tribunal)
        return ("encontrado", tribunal, html)
    except PjeIndisponivelError as e:
        return ("indisponivel", tribunal, e)
    except ErroPjePublico as e:
        return ("nao_encontrado", tribunal, e)


def _norm_ws(texto: str | None) -> str | None:
    if texto is None:
        return None
    s = re.sub(r"\s+", " ", texto).strip()
    return s or None


_ROTULOS_PROPRIEDADES = {
    "Número Processo": "processo",
    "Data da Distribuição": "data_distribuicao",
    "Classe Judicial": "classe",
    "Assunto": "assunto",
    "Jurisdição": "jurisdicao",
    "Órgão Julgador": "orgao_julgador",
}


def _parse_propriedades(soup: BeautifulSoup) -> dict:
    out = dict.fromkeys(_ROTULOS_PROPRIEDADES.values())
    for div in soup.select("div.propertyView"):
        rotulo_el = div.find(class_="name")
        valor_el = div.find(class_="value")
        if not rotulo_el or not valor_el:
            continue
        rotulo = _norm_ws(rotulo_el.get_text())
        if not rotulo:
            continue
        for visivel, chave in _ROTULOS_PROPRIEDADES.items():
            if rotulo.startswith(visivel):
                out[chave] = _norm_ws(valor_el.get_text(separator=" "))
                break
    return out


def _parse_polo(soup: BeautifulSoup, sufixo: str, tipo_rotulo: str) -> list[dict]:
    """Lê a tabela de um polo (ativo/passivo) — só nome + situação
    processual, SEM advogado (o PJe não expõe isso nesta tabela; ver
    aviso no topo do arquivo)."""
    tabela = soup.find("table", id=lambda i: bool(i and i.endswith(f":{sufixo}")))
    if not tabela:
        return []
    partes = []
    for tr in tabela.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        participante = _norm_ws(cells[-2].get_text(separator=" "))
        if participante:
            partes.append({"tipo": tipo_rotulo, "nome": participante, "advogados": []})
    return partes


_MOV_RE = re.compile(r"^\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})\s*-\s*(.+?)\s*$", flags=re.DOTALL)


def _parse_movimentacoes(soup: BeautifulSoup, numero_cnj: str) -> list[MovimentacaoCapturada]:
    tabela = soup.find("table", id=lambda i: bool(i and i.endswith(":processoEvento")))
    if not tabela:
        return []
    movimentacoes = []
    for tr in tabela.find_all("tr"):
        cells = tr.find_all("td")
        if not cells:
            continue
        texto_mov = _norm_ws(cells[0].get_text(separator=" "))
        if not texto_mov:
            continue
        m = _MOV_RE.match(texto_mov)
        if m:
            data_texto, texto_integral = m.group(1), _norm_ws(m.group(2))
            try:
                data = datetime.strptime(data_texto, "%d/%m/%Y %H:%M:%S")
            except ValueError:
                continue
        else:
            continue
        documento = _norm_ws(cells[1].get_text(separator=" ")) if len(cells) > 1 else None

        # Sem "código TPU" disponível aqui (igual ao e-SAJ público — ver
        # aviso equivalente em conector_esaj_publico.py); hash de dedup
        # usa só processo+data+texto, com prefixo "pje" pra nunca colidir
        # com o hash de uma movimentação igual vinda do DataJud/e-SAJ.
        hash_dedup = hashlib.sha256(
            f"pje|{numero_cnj}|{data.isoformat()}|{texto_integral}".encode()
        ).hexdigest()
        movimentacoes.append(MovimentacaoCapturada(
            data=data, codigo_tpu=None, texto_integral=texto_integral or "Movimentação sem descrição",
            hash_dedup=hash_dedup, complemento=documento,
        ))
    return movimentacoes


def _parse_processo(html: str, numero_cnj: str, tribunal_slug: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    propriedades = _parse_propriedades(soup)
    polo_ativo = _parse_polo(soup, "processoPartesPoloAtivoResumidoList", "Polo Ativo")
    polo_passivo = _parse_polo(soup, "processoPartesPoloPassivoResumidoList", "Polo Passivo")
    movimentacoes = _parse_movimentacoes(soup, numero_cnj)

    data_ajuizamento = None
    if propriedades.get("data_distribuicao"):
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", propriedades["data_distribuicao"])
        if m:
            dia, mes, ano = m.groups()
            try:
                data_ajuizamento = datetime(int(ano), int(mes), int(dia))
            except ValueError:
                data_ajuizamento = None

    return {
        "tribunal_slug": tribunal_slug,
        "classe": propriedades.get("classe"),
        "assunto": propriedades.get("assunto"),
        "assuntos_lista": [propriedades["assunto"]] if propriedades.get("assunto") else [],
        "orgao_julgador": propriedades.get("orgao_julgador"),
        "comarca": propriedades.get("jurisdicao"),
        "instancia": "1º grau",
        "data_ajuizamento": data_ajuizamento,
        "valor_causa": None,  # não exposto nesta tela do PJe (diferente do e-SAJ)
        "movimentacoes": movimentacoes,
        "partes": polo_ativo + polo_passivo,
        "sistema": "PJe",
        "formato": None,
        "nivel_sigilo": None,
    }


class ConectorPjePublico(ConectorCaptura):
    """Consulta pública do PJe (TJRJ, TJMG — 1º grau) — sem certificado,
    sem token, sem login. Ver aviso completo no topo do arquivo sobre
    limitações, tribunais NÃO cobertos (TJRS, TJPR) e o motivo."""

    nome_fonte = "pje_publico"

    def consultar_processo(self, numero_cnj: str) -> dict:
        validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
        if not validado["valido"]:
            raise ErroPjePublico(f"Número CNJ inválido: {validado['motivo']}")
        partes = validado["partes"]

        if partes["segmento_codigo"] != SEGMENTO_ESTADUAL:
            nomes = ", ".join(t.nome for t in CANDIDATOS)
            raise ErroPjePublico(
                f"Este conector só sabe consultar tribunais estaduais que usam PJe ({nomes}) — o "
                f"número informado é de outro segmento de Justiça (código {partes['segmento_codigo']})."
            )

        # Mesma estratégia (e mesmo motivo) do e-SAJ público: não existe
        # tabela confiável de "código de tribunal -> qual estado" pra
        # decidir de antemão, então tenta os candidatos EM PARALELO até
        # achar (ver app/utils/conector_esaj_publico.py, seção -73, pra
        # a lição já aprendida sobre por que paralelo em vez de sequencial).
        indisponiveis: list[_TribunalPje] = []
        resultado_encontrado = None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(CANDIDATOS))
        futuros = {
            executor.submit(_tentar_tribunal_candidato, numero_cnj, tribunal): tribunal
            for tribunal in CANDIDATOS
        }
        try:
            for futuro in concurrent.futures.as_completed(futuros):
                status, tribunal, valor = futuro.result()
                if status == "encontrado":
                    resultado_encontrado = (tribunal, valor)
                    break
                if status == "indisponivel":
                    indisponiveis.append(tribunal)
                    try:
                        current_app.logger.warning(
                            f"PJe público: {tribunal.nome} não respondeu à consulta do processo "
                            f"{numero_cnj} dentro do prazo — motivo técnico: {valor}"
                        )
                    except RuntimeError:
                        pass
        finally:
            executor.shutdown(wait=False)

        if resultado_encontrado:
            tribunal, html = resultado_encontrado
            return _parse_processo(html, somente_digitos(numero_cnj), tribunal.slug)

        nomes = ", ".join(t.nome for t in CANDIDATOS)
        if len(indisponiveis) == len(CANDIDATOS):
            raise PjeIndisponivelError(
                f"Não foi possível consultar nenhum dos tribunais PJe testados ({nomes}) agora — "
                "todos falharam ao responder. Tente de novo mais tarde."
            )
        aviso_indisponiveis = ""
        if indisponiveis:
            nomes_indisponiveis = ", ".join(t.nome for t in indisponiveis)
            aviso_indisponiveis = (
                f" ({nomes_indisponiveis} não responderam a tempo e não puderam ser conferidos agora "
                "— pode estar lá mesmo assim)"
            )
        raise ErroPjePublico(
            f"Processo não encontrado em nenhum dos tribunais PJe testados ({nomes})"
            f"{aviso_indisponiveis} — confira o número, ou pode ser de um tribunal (TJRS, TJPR e "
            "outros ainda não são cobertos por este conector — ver aviso no topo do arquivo) ou "
            "segredo de justiça que este conector ainda não alcança."
        )

    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str) -> list[PublicacaoCapturada]:
        raise NotImplementedError(
            "O PJe público não expõe monitoramento de publicações por OAB — isso exigiria um "
            "provedor pago (ver app/utils/captura_conectores.py)."
        )

    def buscar_processos_por_parte(self, cpf_cnpj: str | None = None,
                                    nome: str | None = None) -> list[ProcessoEncontradoDueDiligence]:
        raise NotImplementedError(
            "Busca por parte não implementada neste conector — a consulta pública do PJe por "
            "nome/CPF/OAB existe (é um dos filtros do formulário), mas não foi portada ainda "
            "(fast-follow)."
        )
