"""
Conector de captura por OAB usando a API Comunica do CNJ — a fonte real do
DJEN (Diário de Justiça Eletrônico Nacional), pública e gratuita, sem
cadastro nem chave de API (diferente do DataJud, que exige DATAJUD_API_KEY
— ver app/utils/conector_datajud.py).

Implementa só `monitorar_publicacoes_por_oab` de verdade (contrato em
app/utils/captura_conectores.py::ConectorCaptura) — os outros dois métodos
(`consultar_processo`, `buscar_processos_por_parte`) levantam
`FuncionalidadeNaoDisponivelError`: a API Comunica é fonte de PUBLICAÇÃO
(intimação/citação), não de andamento processual nem de busca por parte —
para essas duas continua sendo necessário o DataJud (andamento, quando já
se tem o número) ou um provedor pago (busca por parte).

⚠️ LIMITAÇÃO REAL, leia antes de prometer algo pro cliente final:

  - Só cobre "push do tribunal onde houver" pela metade — a própria
    documentação técnica pesquisada para este conector é explícita: "This
    is a query-based system, not event-driven; it requires polling for new
    publications rather than receiving push notifications". NENHUM
    tribunal brasileiro oferece hoje um padrão de push registrável para
    "avise-me quando uma intimação nova chegar pra esta OAB" — o único
    jeito real e universal é consultar periodicamente (polling), que é
    exatamente o que este conector faz. Existe, à parte, um endpoint de
    RECEBIMENTO de push em app/routes/captacao_oab.py::webhook_comunicacao
    — pronto pra usar SE algum tribunal específico algum dia oferecer isso
    a este escritório, mas não está validado contra nenhum tribunal real.
  - A API Comunica só devolve PUBLICAÇÕES (o que seria uma intimação/
    citação via Diário) — nunca o inteiro teor de petições/decisões, nem
    andamento processual completo. Depois de uma publicação virar
    conhecida (vinculada a um Processo daqui), o acompanhamento contínuo
    daquele processo continua sendo trabalho do DataJud (`conector
    "padrao"`), não deste conector.
  - Geobloqueio: a API é servida só para IPs do Brasil — chamadas de fora
    do país recebem 403 mesmo com todos os parâmetros corretos. O servidor
    de produção deste sistema precisa estar hospedado no Brasil (ou atrás
    de um proxy/egress brasileiro) para este conector funcionar.

⚠️ Os nomes exatos dos campos do JSON de resposta (`numero_processo` vs.
`numeroProcesso`, `destinatarios[].polo`, `tipoComunicacao`...) foram
levantados por pesquisa (documentação de terceiros e um caso real de
correção de bug — "lê os campos com os nomes reais da ComunicaAPI, nº do
processo e partes vinham vazios" — num projeto aberto que já integra com
esta mesma API) porque este código NÃO pôde ser testado contra uma chamada
real a partir do ambiente onde foi gerado (a API bloqueia IP fora do
Brasil — ver acima, mesmo motivo/mesmo aviso já registrado em
conector_datajud.py). Por isso `_extrair_campo` abaixo tenta VÁRIAS
variações de nome pra cada campo importante, em vez de confiar numa única
grafia — teste com uma OAB real depois do deploy (no Brasil) e, se algum
campo vier vazio de forma consistente, me avise com um exemplo do JSON de
resposta pra eu ajustar o mapeamento.
"""
import time
from datetime import date, datetime

import requests
from flask import current_app

from app.utils.captura_conectores import ConectorCaptura, PublicacaoCapturada
from app.utils.conector_datajud import FuncionalidadeNaoDisponivelError
from app.utils.cnj import somente_digitos
from app.utils.prazos_engine import proxima_data_util

BASE_URL = "https://comunicaapi.pje.jus.br/api/v1"
ITENS_POR_PAGINA = 40  # documentação de terceiros diverge entre "≤50" e "5 ou 100" — valor conservador
PAUSA_ENTRE_PAGINAS_SEGUNDOS = 0.5  # gentileza com o rate limit não documentado publicamente
MAX_PAGINAS_POR_CHAMADA = 20  # trava de segurança (nunca deveria chegar perto disso numa janela de 1 dia)


class ConexaoComunicaError(Exception):
    """Erro de rede/HTTP na chamada real à API Comunica."""


def _extrair_campo(item: dict, *nomes, default=None):
    """Tenta várias grafias possíveis do mesmo campo (ver aviso no topo do
    módulo) — devolve o primeiro valor não vazio encontrado."""
    for nome in nomes:
        valor = item.get(nome)
        if valor not in (None, ""):
            return valor
    return default


def _parse_data(valor):
    if not valor:
        return None
    texto = str(valor)[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _formatar_destinatarios(item: dict) -> str | None:
    """'destinatarios' costuma vir como lista de {nome, polo, ...} — ver
    aviso no topo do módulo. Formata em texto legível curto, mesmo padrão
    de app/utils/analise_processo_ia.py (nunca guarda o JSON cru pro
    usuário final ler)."""
    destinatarios = item.get("destinatarios") or item.get("destinatariosadvogados") or []
    if not isinstance(destinatarios, list) or not destinatarios:
        return None
    rotulos_polo = {"A": "ativo", "P": "passivo"}
    linhas = []
    for d in destinatarios:
        if not isinstance(d, dict):
            continue
        nome = d.get("nome") or d.get("advogado") or "—"
        polo = rotulos_polo.get((d.get("polo") or "").upper(), d.get("polo"))
        linhas.append(f"{nome}" + (f" (polo {polo})" if polo else ""))
    return "; ".join(linhas) or None


class ConectorDJEN(ConectorCaptura):
    nome_fonte = "djen"

    def consultar_processo(self, numero_cnj: str) -> dict:
        raise FuncionalidadeNaoDisponivelError(
            "A API Comunica do CNJ (DJEN) só devolve PUBLICAÇÕES — não faz consulta de andamento "
            "processual completo por número. Para acompanhar um processo já cadastrado, use o "
            "conector \"padrao\" (DataJud)."
        )

    def buscar_processos_por_parte(self, cpf_cnpj: str | None = None, nome: str | None = None):
        raise FuncionalidadeNaoDisponivelError(
            "A API Comunica do CNJ (DJEN) não faz busca de processo por CPF/CNPJ/nome — só devolve "
            "publicações já endereçadas a uma OAB específica. Para due diligence de cliente novo, "
            "seria necessário um provedor pago (ver app/utils/captura_conectores.py)."
        )

    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str,
                                       data_inicio: date | None = None,
                                       data_fim: date | None = None) -> list[PublicacaoCapturada]:
        """
        Consulta GET /comunicacao da API Comunica, paginando até acabar.
        Público, sem autenticação (ver docstring do módulo).

        `data_inicio`/`data_fim`: janela de disponibilização a consultar —
        quem chama (app/utils/captura_djen_pipeline.py) decide a janela
        (normalmente "desde a última captura bem-sucedida desta OAB" até
        hoje); default de 1 dia (hoje) se nada for informado, só pra este
        método nunca fazer uma varredura gigante sem querer se usado
        isoladamente.
        """
        hoje = date.today()
        data_inicio = data_inicio or hoje
        data_fim = data_fim or hoje

        numero_digitos = somente_digitos(numero_oab)
        if not numero_digitos:
            raise ValueError("Número da OAB inválido (nenhum dígito encontrado).")
        uf_normalizada = (uf or "").strip().upper()
        if len(uf_normalizada) != 2:
            raise ValueError("UF da OAB precisa ter 2 letras (ex: SP, RJ, MG).")

        timeout = current_app.config.get("DJEN_TIMEOUT_SEGUNDOS", 20) if current_app else 20

        publicacoes = []
        pagina = 1
        while pagina <= MAX_PAGINAS_POR_CHAMADA:
            params = {
                "numeroOab": numero_digitos,
                "ufOab": uf_normalizada,
                "dataDisponibilizacaoInicio": data_inicio.isoformat(),
                "dataDisponibilizacaoFim": data_fim.isoformat(),
                "pagina": pagina,
                "itensPorPagina": ITENS_POR_PAGINA,
            }
            try:
                resposta = requests.get(f"{BASE_URL}/comunicacao", params=params,
                                         headers={"Accept": "application/json"}, timeout=timeout)
            except requests.RequestException as e:
                raise ConexaoComunicaError(
                    f"Falha de conexão com a API Comunica do CNJ (OAB {numero_oab}/{uf_normalizada}): {e}"
                ) from e

            if resposta.status_code == 403:
                raise ConexaoComunicaError(
                    "A API Comunica recusou a chamada (403) — o motivo mais comum é geobloqueio: "
                    "esta API só responde a partir de IP do Brasil. Confirme que o servidor está "
                    "hospedado no Brasil (ou atrás de um proxy/egress brasileiro)."
                )
            if resposta.status_code != 200:
                raise ConexaoComunicaError(
                    f"API Comunica devolveu HTTP {resposta.status_code} para a OAB "
                    f"{numero_oab}/{uf_normalizada}: {resposta.text[:300]}"
                )

            try:
                corpo = resposta.json()
            except ValueError as e:
                raise ConexaoComunicaError(f"Resposta da API Comunica não é JSON válido: {e}") from e

            itens = corpo.get("items") or corpo.get("itens") or []
            if not itens:
                break

            for item in itens:
                if not isinstance(item, dict):
                    continue
                id_fonte = str(_extrair_campo(item, "id", "hash", "numeroComunicacao"))
                data_disp = _parse_data(_extrair_campo(item, "data_disponibilizacao", "dataDisponibilizacao"))
                # Lei 11.419/2006, art. 4º, §3º: "Considera-se como data da publicação o primeiro
                # dia útil seguinte ao da disponibilização da informação no Diário da Justiça
                # eletrônico" — reaproveita o mesmo calendário de dias úteis do motor de prazos
                # (feriados nacionais + do tribunal + recesso forense), não um "+1 dia corrido" cru.
                tribunal = _extrair_campo(item, "siglaTribunal", "tribunal")
                data_pub = proxima_data_util(data_disp, tribunal=tribunal) if data_disp else None

                publicacoes.append(PublicacaoCapturada(
                    diario="DJEN",
                    data_disponibilizacao=data_disp,
                    data_publicacao=data_pub,
                    teor=_extrair_campo(item, "texto", default="") or "",
                    oab_destinataria=f"{numero_digitos}/{uf_normalizada}",
                    hash_dedup=id_fonte,
                    numero_processo=somente_digitos(_extrair_campo(item, "numero_processo", "numeroProcesso", default="")) or None,
                    numero_processo_mascara=_extrair_campo(item, "numeroprocessocommascara", "numeroProcessoMascara"),
                    tribunal=tribunal,
                    orgao=_extrair_campo(item, "nomeOrgao", "orgao"),
                    tipo_comunicacao=_extrair_campo(item, "tipoComunicacao", "tipo_comunicacao"),
                    tipo_documento=_extrair_campo(item, "tipoDocumento", "tipo_documento"),
                    meio=_extrair_campo(item, "meio"),
                    destinatarios_texto=_formatar_destinatarios(item),
                    link_certidao=_extrair_campo(item, "link", "url"),
                    id_comunicacao_fonte=id_fonte,
                ))

            total = corpo.get("count") or corpo.get("total")
            if total is not None and len(publicacoes) >= int(total):
                break
            if len(itens) < ITENS_POR_PAGINA:
                break

            pagina += 1
            time.sleep(PAUSA_ENTRE_PAGINAS_SEGUNDOS)

        return publicacoes
