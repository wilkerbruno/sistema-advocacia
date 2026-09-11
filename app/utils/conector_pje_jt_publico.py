"""
Conector "PJe-JT público" — captura automática (sem certificado, sem
token, sem login nenhum) direto da consulta pública de 1º e 2º graus da
Justiça do Trabalho (os 22 TRTs confirmados livres de captcha) —
PENDENCIAS.md, seção -90. Pedido do usuário depois de eu testar os 24
TRTs ao vivo: "pode construir um conector para todos os 22 TRTs".

⚠️ Por que 22 e não os 24 — os únicos DOIS excluídos por terem proteção
anti-bot confirmada ao vivo (mesma disciplina de nunca incluir um
tribunal sem checar antes, já usada em conector_pje_publico.py):
- **TRT-3 (MG)** — bloqueio "Human Verification" antes até de mostrar a
  página.
- **TRT-23 (MT)** — protegido por "Anubis" (desafio de prova de
  trabalho/proof-of-work), um provedor de anti-bot diferente de tudo que
  já apareceu neste projeto.

⚠️ Arquitetura BEM diferente do `conector_pje_publico.py` (TJRJ/TJMG) —
não reaproveita nada de lá. Os TRTs rodam uma plataforma nacional
padronizada ("PJe-JT", Angular SPA) com uma API REST JSON própria, em vez
do formulário JSF clássico do PJe:

    GET https://pje.trtN.jus.br/pje-consulta-api/api/processos/dadosbasicos/{20 dígitos}
    Header: X-Grau-Instancia: 1 (ou 2, para 2º grau)

Sem formulário pra abrir antes, sem token de sessão, sem ID gerado
dinamicamente — só essa uma chamada GET. Isso foi confirmado ao vivo
(seção -90): o endpoint devolve JSON estruturado tanto pra número com
dígito verificador inválido (400, `{"codigoErro":"ARQ-033", ...}`) quanto
pra número com formato válido mas processo inexistente (204, corpo
vazio) — nenhuma das duas respostas pediu captcha nem qualquer
verificação extra.

⚠️ IMPORTANTE — isto NÃO é garantia de que todo TRT vai se comportar
assim pra sempre nem que a extração de dado de um processo REAL vai
funcionar de primeira. Duas ressalvas sérias:

1. **Nomes de campo do retorno "encontrado" (200) inferidos do próprio
   JS compilado do app** (`main-*.js` de pje.trt1.jus.br/consultaprocessual/),
   não de um processo real de verdade (não existe processo de teste
   disponível pra confirmar). Isto é mais confiável que adivinhar — é
   ground truth do código-fonte do próprio tribunal —, mas ainda assim
   NÃO FOI CONFIRMADO contra uma resposta JSON real populada. Os campos
   abaixo apareceram no código do app sendo lidos de `processo.<campo>`:
   `classe`, `numero`, `orgaoJulgador`, `orgaoJulgadorColegiado`,
   `poloAtivo`/`poloPassivo` (cada item com `.nome`), `movimentos` (cada
   item com `.descricao` e `.atualizadoEm`) — usados abaixo. Já
   `assunto`/`dataAjuizamento`/`valorCausa` NÃO apareceram em lugar
   nenhum do código lido — são tentados de forma defensiva (`.get(...)`,
   sem quebrar se não existirem) mas é esperado que fiquem `None`/vazios
   até confirmarmos contra um processo real.
2. **Diferente de TJRJ/TJMG (só 1º grau)**, aqui eu tento 1º grau e,
   se vier 204 (não encontrado), tento 2º grau em seguida — mas a mesma
   ressalva de sigilo do PJe clássico vale aqui: não há como distinguir
   "processo em segredo de justiça" de "processo não existe" — os dois
   dão 204.
3. Igual ao `conector_pje_publico.py`: é scraping de API pública sem
   contrato oficial nem documentação — o tribunal pode mudar isso a
   qualquer momento sem aviso. Rodando do datacenter, existe risco real
   de bloqueio de IP; se acontecer, `PjeJtIndisponivelError` é levantado
   com mensagem clara, sem retry automático.

⚠️ Cobertura: **só Justiça do Trabalho (segmento "5")**. Número de
qualquer outro segmento é rejeitado de cara — este conector nunca tenta
"adivinhar" qual TRT pelo código "TR" embutido no número (mesma regra já
usada nos outros conectores: sempre tenta todos os candidatos em
paralelo, nunca decodifica o "TR").
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

import requests
from flask import current_app

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

SEGMENTO_TRABALHO = "5"

DADOSBASICOS_PATH = "processos/dadosbasicos/{numero}"


class ErroPjeJtPublico(Exception):
    """Erro esperado (processo não encontrado — inclusive sigilo, que
    esta consulta também não distingue de "não existe") — mensagem já
    pronta pra mostrar ao usuário."""


class PjeJtIndisponivelError(ErroPjeJtPublico):
    """Falha de rede, resposta com formato inesperado, ou bloqueio — não
    é "processo não existe", é "não deu pra perguntar"."""


@dataclass(frozen=True)
class _TribunalPjeJt:
    slug: str
    nome: str
    # ex: "https://pje.trt1.jus.br/pje-consulta-api/api/"
    base_url: str


# Os 22 TRTs confirmados ao vivo sem nenhum script de captcha (Google
# reCAPTCHA, hCaptcha, Turnstile ou Cloudflare) — PENDENCIAS.md, seção
# -90. TRT-3 e TRT-23 ficam de fora (ver aviso no topo do arquivo).
CANDIDATOS = [
    _TribunalPjeJt("trt1", "TRT-1 (RJ)", "https://pje.trt1.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt2", "TRT-2 (SP)", "https://pje.trt2.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt4", "TRT-4 (RS)", "https://pje.trt4.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt5", "TRT-5 (BA)", "https://pje.trt5.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt6", "TRT-6 (PE)", "https://pje.trt6.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt7", "TRT-7 (CE)", "https://pje.trt7.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt8", "TRT-8 (PA/AP)", "https://pje.trt8.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt9", "TRT-9 (PR)", "https://pje.trt9.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt10", "TRT-10 (DF/TO)", "https://pje.trt10.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt11", "TRT-11 (AM/RR)", "https://pje.trt11.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt12", "TRT-12 (SC)", "https://pje.trt12.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt13", "TRT-13 (PB)", "https://pje.trt13.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt14", "TRT-14 (RO/AC)", "https://pje.trt14.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt15", "TRT-15 (SP-Campinas)", "https://pje.trt15.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt16", "TRT-16 (MA)", "https://pje.trt16.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt17", "TRT-17 (ES)", "https://pje.trt17.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt18", "TRT-18 (GO)", "https://pje.trt18.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt19", "TRT-19 (AL)", "https://pje.trt19.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt20", "TRT-20 (SE)", "https://pje.trt20.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt21", "TRT-21 (RN)", "https://pje.trt21.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt22", "TRT-22 (PI)", "https://pje.trt22.jus.br/pje-consulta-api/api/"),
    _TribunalPjeJt("trt24", "TRT-24 (MS)", "https://pje.trt24.jus.br/pje-consulta-api/api/"),
]


def _nova_sessao() -> requests.Session:
    sessao = requests.Session()
    sessao.headers.update({"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    return sessao


def _obter_dados_processo(sessao: requests.Session, numero_cnj: str, tribunal: _TribunalPjeJt,
                           timeout: int = TIMEOUT_SEGUNDOS) -> tuple[dict, str]:
    """Devolve (dados, instancia) — instancia é "1º grau" ou "2º grau",
    conforme qual delas achou o processo."""
    validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
    if not validado["valido"]:
        raise ErroPjeJtPublico(f"Número CNJ inválido: {validado['motivo']}")
    numero_digitos = somente_digitos(numero_cnj)

    url = f"{tribunal.base_url}{DADOSBASICOS_PATH.format(numero=numero_digitos)}"
    for grau, rotulo_instancia in (("1", "1º grau"), ("2", "2º grau")):
        try:
            resp = sessao.get(url, timeout=timeout, headers={"X-Grau-Instancia": grau})
        except requests.RequestException as e:
            raise PjeJtIndisponivelError(f"Falha de conexão com o PJe-JT do {tribunal.nome}: {e}") from e

        if resp.status_code == 204:
            # Não encontrado NESTE grau — tenta o outro antes de desistir.
            continue
        if resp.status_code == 400:
            # Formato/dígito verificador rejeitado pela própria API do
            # tribunal (ex.: "ARQ-033" - dígitos verificadores inválidos)
            # — trata como "não encontrado" (mesma decisão do PJe
            # clássico: quem decide se existe é a consulta real).
            continue
        if resp.status_code != 200:
            raise PjeJtIndisponivelError(
                f"PJe-JT do {tribunal.nome} respondeu {resp.status_code} de forma inesperada ao "
                f"consultar o processo ({rotulo_instancia})."
            )
        try:
            corpo = resp.json()
        except ValueError as e:
            raise PjeJtIndisponivelError(
                f"PJe-JT do {tribunal.nome} devolveu uma resposta que não é JSON válido — a "
                "estrutura da API pode ter mudado."
            ) from e

        # A própria tela do tribunal trata o corpo como uma lista e pega
        # o primeiro item "truthy" (ver aviso no topo do arquivo) — replica
        # esse comportamento em vez de assumir que é sempre um objeto único.
        if isinstance(corpo, list):
            achado = next((item for item in corpo if item), None)
        else:
            achado = corpo or None
        if achado:
            return achado, rotulo_instancia

    raise ErroPjeJtPublico(
        f"Processo {validado['partes']['formatado']} não encontrado na consulta pública do PJe-JT "
        f"do {tribunal.nome} (1º nem 2º grau) — confira o número, ou pode ser de outro tribunal, "
        "estar em segredo de justiça (esta consulta não distingue isso de \"não encontrado\"), ou "
        "ter sido baixado/arquivado fora do PJe."
    )


def _tentar_tribunal_candidato(numero_cnj: str, tribunal: _TribunalPjeJt) -> tuple:
    """Roda em thread separada (mesmo padrão de conector_pje_publico.py)
    — nunca levanta exceção pra fora, devolve (status, tribunal, valor)."""
    sessao = _nova_sessao()
    try:
        dados, instancia = _obter_dados_processo(sessao, numero_cnj, tribunal)
        return ("encontrado", tribunal, (dados, instancia))
    except PjeJtIndisponivelError as e:
        return ("indisponivel", tribunal, e)
    except ErroPjeJtPublico as e:
        return ("nao_encontrado", tribunal, e)


def _norm_ws(texto) -> str | None:
    if texto is None:
        return None
    s = re.sub(r"\s+", " ", str(texto)).strip()
    return s or None


def _parse_polo(itens, tipo_rotulo: str) -> list[dict]:
    if not itens:
        return []
    partes = []
    for item in itens:
        nome = _norm_ws(item.get("nome")) if isinstance(item, dict) else None
        if nome:
            partes.append({"tipo": tipo_rotulo, "nome": nome, "advogados": []})
    return partes


_FORMATOS_DATA = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y")


def _parse_data(valor) -> datetime | None:
    if not valor:
        return None
    texto = str(valor)
    # Corta timezone/offset se vier (ex.: "2026-01-01T10:00:00-04:00" ou
    # com "Z") — só a parte de data/hora importa aqui.
    texto = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", texto)
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def _parse_movimentacoes(dados: dict, numero_cnj: str) -> list[MovimentacaoCapturada]:
    itens = dados.get("movimentos") or []
    movimentacoes = []
    for item in itens:
        if not isinstance(item, dict):
            continue
        texto_integral = _norm_ws(item.get("descricao"))
        data = _parse_data(item.get("atualizadoEm"))
        if not texto_integral or not data:
            continue
        hash_dedup = hashlib.sha256(
            f"pje-jt|{numero_cnj}|{data.isoformat()}|{texto_integral}".encode()
        ).hexdigest()
        movimentacoes.append(MovimentacaoCapturada(
            data=data, codigo_tpu=None, texto_integral=texto_integral, hash_dedup=hash_dedup,
            complemento=None,
        ))
    return movimentacoes


def _parse_processo(dados: dict, numero_cnj: str, tribunal_slug: str, instancia: str) -> dict:
    orgao_julgador = _norm_ws(dados.get("orgaoJulgador"))
    orgao_colegiado = _norm_ws(dados.get("orgaoJulgadorColegiado"))
    if orgao_colegiado and orgao_julgador:
        orgao_julgador = f"{orgao_colegiado} - {orgao_julgador}"
    elif orgao_colegiado:
        orgao_julgador = orgao_colegiado

    # "assunto"/"dataAjuizamento"/"valorCausa" não foram confirmados no
    # payload real (ver aviso no topo do arquivo) — tentados de forma
    # defensiva, com nomes de campo prováveis, sem quebrar se não existirem.
    assunto = _norm_ws(dados.get("assunto"))
    assuntos_bruto = dados.get("assuntos")
    assuntos_lista = []
    if isinstance(assuntos_bruto, list):
        assuntos_lista = [_norm_ws(a.get("descricao") if isinstance(a, dict) else a) for a in assuntos_bruto]
        assuntos_lista = [a for a in assuntos_lista if a]
        if not assunto and assuntos_lista:
            assunto = assuntos_lista[0]
    elif assunto:
        assuntos_lista = [assunto]

    data_ajuizamento = _parse_data(dados.get("dataAjuizamento") or dados.get("dataDistribuicao"))

    return {
        "tribunal_slug": tribunal_slug,
        "classe": _norm_ws(dados.get("classe")),
        "assunto": assunto,
        "assuntos_lista": assuntos_lista,
        "orgao_julgador": orgao_julgador,
        "comarca": None,  # Justiça do Trabalho não usa "comarca" — não se aplica aqui.
        "instancia": instancia,
        "data_ajuizamento": data_ajuizamento,
        "valor_causa": None,  # campo não confirmado no payload real (ver aviso no topo do arquivo)
        "movimentacoes": _parse_movimentacoes(dados, numero_cnj),
        "partes": _parse_polo(dados.get("poloAtivo"), "Polo Ativo") + _parse_polo(dados.get("poloPassivo"), "Polo Passivo"),
        "sistema": "PJe-JT",
        "formato": None,
        "nivel_sigilo": None,
    }


class ConectorPjeJtPublico(ConectorCaptura):
    """Consulta pública do PJe-JT (22 TRTs — ver CANDIDATOS acima) — sem
    certificado, sem token, sem login. Ver aviso completo no topo do
    arquivo sobre os 2 TRTs não cobertos e as limitações do parsing."""

    nome_fonte = "pje_jt_publico"

    def consultar_processo(self, numero_cnj: str) -> dict:
        validado = validar_numero_cnj(numero_cnj, exigir_dv=False)
        if not validado["valido"]:
            raise ErroPjeJtPublico(f"Número CNJ inválido: {validado['motivo']}")
        partes = validado["partes"]

        if partes["segmento_codigo"] != SEGMENTO_TRABALHO:
            raise ErroPjeJtPublico(
                "Este conector só sabe consultar a Justiça do Trabalho (PJe-JT) — o número "
                f"informado é de outro segmento de Justiça (código {partes['segmento_codigo']})."
            )

        # Mesma estratégia (e mesmo motivo) do PJe clássico: não decodifica
        # o "TR" do número pra adivinhar o TRT — tenta todos os 22
        # candidatos EM PARALELO até achar.
        indisponiveis: list[_TribunalPjeJt] = []
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
                            f"PJe-JT público: {tribunal.nome} não respondeu à consulta do processo "
                            f"{numero_cnj} dentro do prazo — motivo técnico: {valor}"
                        )
                    except RuntimeError:
                        pass
        finally:
            executor.shutdown(wait=False)

        if resultado_encontrado:
            tribunal, (dados, instancia) = resultado_encontrado
            return _parse_processo(dados, somente_digitos(numero_cnj), tribunal.slug, instancia)

        nomes = ", ".join(t.nome for t in CANDIDATOS)
        if len(indisponiveis) == len(CANDIDATOS):
            raise PjeJtIndisponivelError(
                f"Não foi possível consultar nenhum dos {len(CANDIDATOS)} TRTs testados agora — "
                "todos falharam ao responder. Tente de novo mais tarde."
            )
        aviso_indisponiveis = ""
        if indisponiveis:
            nomes_indisponiveis = ", ".join(t.nome for t in indisponiveis)
            if len(indisponiveis) == 1:
                frase = f"{nomes_indisponiveis} não respondeu a tempo e não pôde ser conferido agora"
            else:
                frase = f"{nomes_indisponiveis} não responderam a tempo e não puderam ser conferidos agora"
            aviso_indisponiveis = f" ({frase} — pode estar lá mesmo assim)"
        raise ErroPjeJtPublico(
            f"Processo não encontrado em nenhum dos {len(CANDIDATOS)} TRTs testados{aviso_indisponiveis} "
            "— confira o número, ou pode ser do TRT-3/TRT-23 (não cobertos por este conector — ver "
            "aviso no topo do arquivo) ou segredo de justiça que esta consulta não alcança."
        )

    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str) -> list[PublicacaoCapturada]:
        raise NotImplementedError(
            "O PJe-JT público não expõe monitoramento de publicações por OAB — isso exigiria um "
            "provedor pago (ver app/utils/captura_conectores.py)."
        )

    def buscar_processos_por_parte(self, cpf_cnpj: str | None = None,
                                    nome: str | None = None) -> list[ProcessoEncontradoDueDiligence]:
        raise NotImplementedError(
            "Busca por parte não implementada neste conector — a consulta pública do PJe-JT tem um "
            "filtro de nome/CPF/CNPJ (não portado ainda — fast-follow)."
        )
