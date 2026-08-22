"""
Conector de captura real usando a API Pública do DataJud (CNJ) — ver
app/utils/captura_conectores.py para o contrato (ConectorCaptura) que esta
classe implementa, e app/utils/tribunais_datajud.py para o catálogo de
tribunais suportados.

Diferente dos provedores pagos citados no restante do PENDENCIAS.md (Judit,
Escavador, Digesto, Codilo), o DataJud é a base pública e gratuita do
próprio CNJ (Resolução CNJ 331/2020) — qualquer pessoa pode se cadastrar de
graça em https://datajud-wiki.cnj.jus.br/ e gerar uma chave de API própria
(gratuita, sem OAB/CNPJ obrigatório).

⚠️ Escopo real do que este conector cobre — importante estar ciente disso
antes de prometer algo pro cliente final do escritório:

  - FUNCIONA (de graça, cobre todos os 91 tribunais do país — qualquer TJ,
    TRT, TRF, tribunal superior): acompanhar o andamento de um processo
    cujo número CNJ você já tem — carga inicial (classe, assunto, órgão
    julgador, data de ajuizamento) e histórico de movimentações, usados
    pra deduplicar e alimentar a máquina de estados
    (app/utils/estado_processual_engine.py).
  - NÃO FUNCIONA (nem de graça, nem com este conector): "buscar todos os
    processos de uma pessoa/empresa pelo nome ou CPF/CNPJ" sem já saber o
    número do processo — o DataJud não indexa CPF/CNPJ publicamente
    (LGPD) e busca por nome de parte não é confiável o suficiente pra
    automatizar. Isso é exatamente o tipo de busca que os provedores
    pagos (Judit/Escavador/Digesto/Codilo) vendem — se o escritório
    precisar descobrir processos novos sem ter o número, só contratando
    um desses.
  - NÃO FUNCIONA: baixar o inteiro teor de petições/decisões (o DataJud dá
    metadado + texto curto das movimentações, não os arquivos/documentos
    do processo).
  - NÃO FUNCIONA: monitoramento de publicação no Diário de Justiça
    Eletrônico por OAB (é uma fonte de dados diferente — ver
    `monitorar_publicacoes_por_oab` abaixo).
  - Defasagem: os dados do DataJud não são em tempo real — a atualização
    de cada tribunal para a base nacional varia de horas a alguns dias
    (documentado pelo próprio CNJ).

⚠️ Os nomes exatos dos campos do JSON de resposta (`movimentos`, `codigo`,
`nome`, `dataHora`, `classe`, `assuntos`, `orgaoJulgador`...) seguem o
schema publicado do DataJud/MNI, mas este código não pôde ser testado
contra uma chamada real à API a partir do ambiente onde foi gerado (rede
de saída restrita nesse ambiente de geração). Teste com um processo real
depois do deploy — se algum campo vier vazio de forma consistente
(diferente de "processo não encontrado"), me avise com um exemplo do JSON
de resposta pra eu ajustar o mapeamento.

🔎 Identificação do tribunal quando não é Justiça do Trabalho (rodada de
17/08/2026): em vez de exigir que o `tribunal_datajud` seja escolhido
manualmente (ou tentar "adivinhar" via alguma tabela/IA — arriscado,
ver app/utils/tribunais_datajud.py), `consultar_processo` agora tenta
CADA tribunal candidato do segmento (ex: os 27 TJs, para Estadual) contra
a API pública de verdade até achar o processo — sem chute, só perguntando
pra fonte oficial. A API pública documenta limite de 120 requisições por
minuto (ambíguo se é por IP ou geral — ver
https://www.tabnews.com.br/lukexp/4b2885d9-cbae-49ce-8647-b15e7847976d),
folgado mesmo no pior caso (27 chamadas sequenciais). `tribunal_hint`
continua existindo e, quando informado, pula direto pra 1 chamada só —
mais rápido, mas não é mais obrigatório.
"""
import hashlib
from datetime import datetime

import requests
from flask import current_app

from app.utils.captura_conectores import ConectorCaptura, MovimentacaoCapturada
from app.utils.cnj import validar_numero_cnj, somente_digitos
from app.utils.tribunais_datajud import slug_valido, candidatos_do_segmento
from app.utils import ibge

BASE_URL = "https://api-publica.datajud.cnj.jus.br"

_ROTULOS_GRAU = {
    "G1": "1º grau", "G2": "2º grau", "G3": "3º grau",
    "GRAU_1": "1º grau", "GRAU_2": "2º grau", "GRAU_3": "3º grau",
    "JE": "Juizado Especial", "JR": "Justiça de Recursos",
}


def _formatar_complementos(complementos_brutos):
    """
    Formata `complementosTabelados` (lista de objetos que o DataJud manda
    junto de ALGUNS tipos de movimentação, com detalhe extra que não cabe
    no campo `nome` genérico — ex: resultado de um julgamento
    procedente/improcedente, tipo de uma audiência, meio de uma intimação)
    num texto curto e legível, guardado em `Movimentacao.complemento` (ver
    PENDENCIAS.md, seção -37).

    A maioria das movimentações NÃO tem nada aqui (lista vazia/ausente) —
    isso é o normal, não falha de captura. Best-effort: os nomes exatos dos
    campos de cada item ("nome", "descricao", "valor"...) seguem o schema
    publicado do DataJud, mas — mesmo aviso já registrado no topo deste
    arquivo — não foi possível testar contra uma chamada real (rede de
    saída restrita neste ambiente de geração). Se o texto sair estranho
    ou vazio de forma consistente pra um tipo de ato que claramente tem
    complemento, me avise com um exemplo do JSON de resposta pra ajustar.
    """
    if not complementos_brutos:
        return None
    partes = []
    for item in complementos_brutos:
        if not isinstance(item, dict):
            continue
        rotulo = item.get("nome") or item.get("descricao")
        valor = item.get("descricao") if item.get("nome") else None
        if rotulo and valor and rotulo != valor:
            partes.append(f"{rotulo}: {valor}")
        elif rotulo:
            partes.append(str(rotulo))
    return "; ".join(partes) or None


def rotulo_grau(grau):
    """'G1' -> '1º grau', etc. — ver campo `grau` no exemplo de resposta do
    DataJud (https://www.tabnews.com.br/joaotextor/abstraindo-a-api-publica-do-cnj-datajud).
    Código não mapeado: devolve o valor cru (melhor um rótulo estranho do
    que nada) — nunca inventa um grau que não veio da resposta."""
    if not grau:
        return None
    return _ROTULOS_GRAU.get(grau.upper(), grau)


class TribunalNaoIdentificadoError(Exception):
    """Não dá pra saber em qual tribunal consultar: o segmento de Justiça
    do processo (Eleitoral, Militar Estadual...) ainda não tem nenhum
    tribunal cadastrado no catálogo (app/utils/tribunais_datajud.py), então
    nem a busca automática por tentativa (ver `candidatos_do_segmento`) tem
    o que testar."""


class FuncionalidadeNaoDisponivelError(Exception):
    """Funcionalidade que o DataJud não cobre — nunca finge suporte."""


class ConexaoDataJudError(Exception):
    """Erro de rede/HTTP, ou processo não encontrado (em um tribunal
    específico, ou em nenhum dos tribunais candidatos tentados
    automaticamente) na chamada real ao DataJud."""


def _parse_data(valor):
    if not valor:
        return None
    try:
        # DataJud costuma devolver ISO 8601 (ex: "2024-03-15T14:30:00.000Z")
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class ConectorDataJud(ConectorCaptura):
    nome_fonte = "datajud"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or current_app.config.get("DATAJUD_API_KEY")
        if not self.api_key:
            raise ValueError("DATAJUD_API_KEY não configurada.")

    def _consultar(self, slug: str, numero_cnj_digitos: str) -> dict:
        url = f"{BASE_URL}/api_publica_{slug}/_search"
        headers = {
            "Authorization": f"APIKey {self.api_key}",
            "Content-Type": "application/json",
        }
        corpo = {"query": {"match": {"numeroProcesso": numero_cnj_digitos}}}
        try:
            resposta = requests.post(url, json=corpo, headers=headers, timeout=20)
        except requests.RequestException as e:
            raise ConexaoDataJudError(f"Falha de conexão com a API pública do DataJud: {e}") from e

        if resposta.status_code == 401:
            raise ConexaoDataJudError(
                "DataJud recusou a chave de API (401) — confira DATAJUD_API_KEY no .env "
                "(gere/confira em https://datajud-wiki.cnj.jus.br/)."
            )
        if resposta.status_code == 404:
            raise ConexaoDataJudError(
                f"Tribunal '{slug}' não encontrado no DataJud (404) — confira o valor "
                "escolhido em \"Tribunal (DataJud)\" no cadastro do processo."
            )
        if resposta.status_code != 200:
            raise ConexaoDataJudError(
                f"DataJud respondeu {resposta.status_code} de forma inesperada: {resposta.text[:300]}"
            )
        return resposta.json()

    def _buscar_no_tribunal(self, slug: str, numero_cnj: str, digitos: str) -> dict | None:
        """
        Consulta UM tribunal específico. Devolve o dict de dados (ver
        `consultar_processo`) se achou o processo lá, ou `None` se aquele
        tribunal simplesmente não tem esse processo (resposta 200 com lista
        de resultados vazia — não é erro, só "não é aqui"). Erros de
        verdade (rede, 401, 5xx...) continuam subindo como
        `ConexaoDataJudError` — quem chama decide se aborta ou tenta o
        próximo tribunal candidato.
        """
        payload = self._consultar(slug, digitos)
        hits = payload.get("hits", {}).get("hits", [])
        if not hits:
            return None
        origem = hits[0].get("_source", {})

        movimentacoes = []
        for mov in origem.get("movimentos", []) or []:
            data_hora = mov.get("dataHora")
            codigo = mov.get("codigo")
            nome = mov.get("nome") or "Movimentação sem descrição"
            hash_dedup = hashlib.sha256(f"{numero_cnj}|{data_hora}|{codigo}|{nome}".encode()).hexdigest()
            # hash_dedup NÃO leva o complemento em conta de propósito — ele
            # continua identificando o mesmo ato pelos mesmos campos de
            # sempre, então uma futura correção/preenchimento tardio do
            # complemento pelo tribunal não faria a movimentação (que já
            # existe) parecer "nova" e duplicar.
            movimentacoes.append(MovimentacaoCapturada(
                data=_parse_data(data_hora),
                codigo_tpu=str(codigo) if codigo is not None else None,
                texto_integral=nome,
                hash_dedup=hash_dedup,
                complemento=_formatar_complementos(mov.get("complementosTabelados")),
            ))

        # "assuntos" normalmente é uma lista de objetos ({"nome": ...}), mas
        # pelo menos um exemplo real documentado tem uma lista ANINHADA
        # (lista de listas) — trata os dois formatos pra nunca quebrar por
        # causa disso (campo secundário, não vale travar a captura toda).
        assuntos_brutos = origem.get("assuntos") or []
        assuntos_nomes = []
        for item in assuntos_brutos:
            candidatos = item if isinstance(item, list) else [item]
            for c in candidatos:
                if isinstance(c, dict) and c.get("nome"):
                    assuntos_nomes.append(c["nome"])

        orgao_julgador = origem.get("orgaoJulgador") or {}
        codigo_municipio = orgao_julgador.get("codigoMunicipioIBGE")

        return {
            "tribunal_slug": slug,
            "classe": (origem.get("classe") or {}).get("nome"),
            "assunto": ", ".join(assuntos_nomes) or None,
            "assuntos_lista": assuntos_nomes,
            "orgao_julgador": orgao_julgador.get("nome"),
            "data_ajuizamento": _parse_data(origem.get("dataAjuizamento")),
            "valor_causa": origem.get("valorCausa"),
            "movimentacoes": movimentacoes,
            # Adicionados nesta rodada pra autopreencher mais campos do
            # cadastro (Instância, Comarca — ver app/routes/processos.py e
            # app/templates/processos/form.html). Melhor esforço: quando o
            # tribunal não devolve esses campos (nem todos preenchem), ou a
            # consulta ao IBGE falha, ficam None sem travar o resto.
            "grau": origem.get("grau"),
            "instancia": rotulo_grau(origem.get("grau")),
            "comarca": ibge.nome_municipio(codigo_municipio),
            # Guarda o código cru mesmo quando a comarca não foi resolvida,
            # pra dar pra distinguir os dois motivos possíveis de vir vazia:
            # (a) este tribunal/registro não devolveu codigoMunicipioIBGE
            # nenhum (comum — nem todo tribunal preenche esse campo em todo
            # processo), de (b) o código veio, mas a consulta à API do IBGE
            # falhou ou não achou o nome (aí sim vale tentar de novo depois).
            # Ver aviso montado em app/routes/governanca.py::consultar_cnj_preview.
            "comarca_codigo_ibge": codigo_municipio,
            # Sistema/formato/sigilo — dados que não têm campo próprio no
            # cadastro, então viram nota na Descrição/objeto (ver
            # app/utils/captura_pipeline.py::montar_nota_datajud) em vez de
            # ficarem perdidos sem aparecer em lugar nenhum.
            "sistema": (origem.get("sistema") or {}).get("nome"),
            "formato": (origem.get("formato") or {}).get("nome"),
            "nivel_sigilo": origem.get("nivelSigilo"),
        }

    def consultar_processo(self, numero_cnj: str, tribunal_hint: str | None = None) -> dict:
        """
        tribunal_hint: slug de app/utils/tribunais_datajud.py (ex: "tjsp"),
        opcional — quando informado (e válido), consulta direto só nesse
        tribunal (1 chamada, mais rápido). Quando ausente:

          - Justiça do Trabalho (segmento "5"): o número do TRT já está
            embutido no próprio número do processo, sem ambiguidade — usa
            direto, sem tentar mais de um tribunal.
          - Demais segmentos com candidatos cadastrados (Estadual, Federal,
            STF, STJ, Justiça Militar da União — ver
            `tribunais_datajud.candidatos_do_segmento`): tenta CADA
            tribunal candidato de verdade contra a API pública do DataJud,
            na ordem do catálogo, até achar o processo. Isso evita ter que
            adivinhar (ou pedir pra escolher manualmente) qual dos ~27
            tribunais estaduais é o certo — o "chute" vira uma pergunta
            real pra fonte oficial. Só continua para o próximo candidato
            quando a resposta for "não encontrado aqui" (200, sem
            resultado); qualquer erro de verdade (chave inválida, rede
            fora do ar, resposta inesperada) interrompe na hora, sem gastar
            as tentativas restantes.
          - Segmentos sem candidato cadastrado (Eleitoral, Militar
            Estadual): levanta `TribunalNaoIdentificadoError` — não tem
            tribunal nenhum no catálogo pra sequer tentar.
        """
        # exigir_dv=False: quem decide se o processo existe de verdade é a
        # busca real no DataJud, não o cálculo do dígito verificador aqui —
        # processos legados (principalmente antigos) às vezes têm número
        # registrado com um dígito que não bate com a fórmula atual, mas
        # que é exatamente como o tribunal/DataJud indexou de verdade. Ver
        # docstring de `validar_numero_cnj` (app/utils/cnj.py).
        resultado = validar_numero_cnj(numero_cnj, exigir_dv=False)
        if not resultado["valido"]:
            raise ValueError(f"Número CNJ inválido: {resultado['motivo']}")
        partes = resultado["partes"]
        digitos = somente_digitos(numero_cnj)

        if partes["segmento_codigo"] == "5":  # Justiça do Trabalho — sem ambiguidade
            slug = f"trt{int(partes['tribunal_codigo'])}"
            dados = self._buscar_no_tribunal(slug, numero_cnj, digitos)
            if dados is None:
                raise ConexaoDataJudError(
                    f"Processo não encontrado no DataJud em '{slug}' — pode ser defasagem de "
                    "indexação (leva de horas a dias) ou o processo estar em segredo de "
                    "justiça (não indexado publicamente)."
                )
            dados["aviso_dv"] = resultado["aviso_dv"]
            return dados

        if tribunal_hint and slug_valido(tribunal_hint):
            dados = self._buscar_no_tribunal(tribunal_hint, numero_cnj, digitos)
            if dados is None:
                raise ConexaoDataJudError(
                    f"Processo não encontrado no DataJud em '{tribunal_hint}' — pode ser "
                    "defasagem de indexação (leva de horas a dias), o processo estar em "
                    "segredo de justiça (não indexado publicamente), ou o tribunal escolhido "
                    "não ser o correto para este processo."
                )
            dados["aviso_dv"] = resultado["aviso_dv"]
            return dados

        candidatos = candidatos_do_segmento(partes["segmento_codigo"])
        if not candidatos:
            raise TribunalNaoIdentificadoError(
                f"Não foi possível identificar automaticamente o tribunal deste processo "
                f"(segmento: {partes['segmento_nome']}) — esse segmento ainda não tem "
                "nenhum tribunal cadastrado para busca automática. Abra o processo e "
                "selecione o tribunal no campo \"Tribunal (DataJud)\" para habilitar a "
                "captura automática, se souber qual é."
            )

        for slug in candidatos:
            dados = self._buscar_no_tribunal(slug, numero_cnj, digitos)
            if dados is not None:
                dados["aviso_dv"] = resultado["aviso_dv"]
                return dados

        raise ConexaoDataJudError(
            f"Processo não encontrado no DataJud em nenhum dos {len(candidatos)} tribunais de "
            f"{partes['segmento_nome']} testados automaticamente — pode ser defasagem de "
            "indexação (leva de horas a dias) ou o processo estar em segredo de justiça "
            "(não indexado publicamente)."
        )

    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str):
        raise FuncionalidadeNaoDisponivelError(
            "A API pública do DataJud não oferece monitoramento de publicação por OAB "
            "(isso é Diário de Justiça Eletrônico, uma fonte de dados diferente). Para "
            "essa funcionalidade seria necessário contratar um provedor pago (Judit, "
            "Escavador, Digesto ou Codilo) ou integrar diretamente o DJEN."
        )

    def buscar_processos_por_parte(self, cpf_cnpj=None, nome=None):
        # Due diligence de cliente novo (PENDENCIAS.md, seção -53). A API
        # pública do DataJud exige informar o TRIBUNAL de antemão pra
        # consultar por parte (não existe endpoint de busca nacional único
        # por CPF/CNPJ/nome), o que a torna inviável pra este uso — o
        # objetivo aqui é "todo processo no Brasil", não "processo neste
        # tribunal que eu já suspeito". Continua sendo terreno exclusivo de
        # provedor pago.
        raise FuncionalidadeNaoDisponivelError(
            "A API pública do DataJud não oferece busca nacional de processos por CPF/CNPJ/nome "
            "sem já saber o tribunal — para due diligence de cliente novo seria necessário "
            "contratar um provedor pago (Judit, Escavador, Digesto, Codilo ou Jusbrasil Soluções)."
        )
