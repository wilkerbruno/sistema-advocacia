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
"""
import hashlib
from datetime import datetime

import requests
from flask import current_app

from app.utils.captura_conectores import ConectorCaptura, MovimentacaoCapturada
from app.utils.cnj import validar_numero_cnj, somente_digitos
from app.utils.tribunais_datajud import slug_valido

BASE_URL = "https://api-publica.datajud.cnj.jus.br"


class TribunalNaoIdentificadoError(Exception):
    """Não dá pra saber com segurança em qual tribunal consultar (processo
    fora da Justiça do Trabalho e sem `tribunal_datajud` escolhido
    manualmente no cadastro do processo)."""


class FuncionalidadeNaoDisponivelError(Exception):
    """Funcionalidade que o DataJud não cobre — nunca finge suporte."""


class ConexaoDataJudError(Exception):
    """Erro de rede/HTTP/dado não encontrado na chamada real ao DataJud."""


def _slug_do_tribunal(numero_cnj: str, tribunal_hint: str | None) -> str:
    """
    Deriva o slug do tribunal (ex: "trt2") a partir do número CNJ quando
    possível — Justiça do Trabalho, onde o número da região do TRT está
    direto no número do processo, sem ambiguidade nenhuma (ver
    app/utils/cnj.py) — ou usa o `tribunal_hint` escolhido manualmente
    pelo usuário (Processo.tribunal_datajud) para os demais segmentos
    (Estadual, Federal, etc.), onde não há como derivar com segurança só
    pelo número.
    """
    resultado = validar_numero_cnj(numero_cnj)
    if not resultado["valido"]:
        raise ValueError(f"Número CNJ inválido: {resultado['motivo']}")

    partes = resultado["partes"]
    if partes["segmento_codigo"] == "5":  # Justiça do Trabalho — automático, sem ambiguidade
        return f"trt{int(partes['tribunal_codigo'])}"

    if tribunal_hint and slug_valido(tribunal_hint):
        return tribunal_hint

    raise TribunalNaoIdentificadoError(
        f"Não foi possível identificar automaticamente o tribunal deste processo "
        f"(segmento: {partes['segmento_nome']}). Abra o processo e selecione o "
        "tribunal no campo \"Tribunal (DataJud)\" para habilitar a captura automática."
    )


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

    def consultar_processo(self, numero_cnj: str, tribunal_hint: str | None = None) -> dict:
        """
        tribunal_hint: slug de app/utils/tribunais_datajud.py (ex: "tjsp"),
        obrigatório para tudo que não for Justiça do Trabalho. Parâmetro
        extra em relação ao contrato base (ConectorCaptura), opcional para
        manter compatibilidade com quem só passa o número.
        """
        slug = _slug_do_tribunal(numero_cnj, tribunal_hint)
        digitos = somente_digitos(numero_cnj)
        payload = self._consultar(slug, digitos)

        hits = payload.get("hits", {}).get("hits", [])
        if not hits:
            raise ConexaoDataJudError(
                f"Processo não encontrado no DataJud em '{slug}' — pode ser defasagem de "
                "indexação (leva de horas a dias) ou o processo estar em segredo de "
                "justiça (não indexado publicamente)."
            )
        origem = hits[0].get("_source", {})

        movimentacoes = []
        for mov in origem.get("movimentos", []) or []:
            data_hora = mov.get("dataHora")
            codigo = mov.get("codigo")
            nome = mov.get("nome") or "Movimentação sem descrição"
            hash_dedup = hashlib.sha256(f"{numero_cnj}|{data_hora}|{codigo}|{nome}".encode()).hexdigest()
            movimentacoes.append(MovimentacaoCapturada(
                data=_parse_data(data_hora),
                codigo_tpu=str(codigo) if codigo is not None else None,
                texto_integral=nome,
                hash_dedup=hash_dedup,
            ))

        return {
            "tribunal_slug": slug,
            "classe": (origem.get("classe") or {}).get("nome"),
            "assunto": ", ".join(a.get("nome") for a in (origem.get("assuntos") or []) if a.get("nome")) or None,
            "orgao_julgador": (origem.get("orgaoJulgador") or {}).get("nome"),
            "data_ajuizamento": _parse_data(origem.get("dataAjuizamento")),
            "valor_causa": origem.get("valorCausa"),
            "movimentacoes": movimentacoes,
        }

    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str):
        raise FuncionalidadeNaoDisponivelError(
            "A API pública do DataJud não oferece monitoramento de publicação por OAB "
            "(isso é Diário de Justiça Eletrônico, uma fonte de dados diferente). Para "
            "essa funcionalidade seria necessário contratar um provedor pago (Judit, "
            "Escavador, Digesto ou Codilo) ou integrar diretamente o DJEN."
        )
