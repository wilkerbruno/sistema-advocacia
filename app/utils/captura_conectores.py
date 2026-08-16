"""
Interface de conectores de captura automática (seções 5.0/5.2 do briefing).

Status: o conector "padrão" (`obter_conector("padrao")`) já está LIGADO —
usa o DataJud, a API pública e gratuita do CNJ (ver
app/utils/conector_datajud.py para o que ela cobre e o que não cobre).
Não depende de contrato pago; depende só de `DATAJUD_API_KEY` configurada
no `.env` (cadastro individual gratuito em https://datajud-wiki.cnj.jus.br/).

Um provedor pago (Judit, Escavador, Digesto ou Codilo — recomendação
original do briefing, seção 5.2) continua sendo a única forma de cobrir o
que o DataJud não cobre: busca de processos por nome/CPF sem já ter o
número, inteiro teor de documentos, e monitoramento de publicação por OAB.
Se um desses for contratado no futuro, basta implementar uma nova
subclasse de `ConectorCaptura` (ex: `ConectorJudit`) e registrá-la aqui.

Nenhuma classe/função abaixo faz requisição de rede diretamente — só a
implementação concreta (ConectorDataJud) faz.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class MovimentacaoCapturada:
    data: object  # datetime
    codigo_tpu: str | None
    texto_integral: str
    hash_dedup: str


@dataclass
class PublicacaoCapturada:
    diario: str
    data_disponibilizacao: object  # date
    data_publicacao: object  # date
    teor: str
    oab_destinataria: str | None
    hash_dedup: str


class ConectorCaptura(ABC):
    """Contrato que um conector real (Judit/Escavador/Digesto/Codilo/scraper
    de DJE ou PJe) precisa implementar."""

    nome_fonte: str

    @abstractmethod
    def consultar_processo(self, numero_cnj: str) -> dict:
        """
        Deve devolver um dicionário com a carga inicial completa do
        processo (seção 5.0, item 2): capa, classe, assunto, partes,
        advogados, valor da causa, data de distribuição, e o histórico
        integral de movimentações (lista de MovimentacaoCapturada).
        """
        raise NotImplementedError

    @abstractmethod
    def monitorar_publicacoes_por_oab(self, numero_oab: str, uf: str) -> list[PublicacaoCapturada]:
        """Deve devolver as publicações novas no DJE para a OAB informada."""
        raise NotImplementedError


class ConectorNaoConfiguradoError(Exception):
    pass


def obter_conector(nome_fonte: str) -> ConectorCaptura:
    """
    Fábrica de conectores. "padrao" usa o DataJud (gratuito, ver
    app/utils/conector_datajud.py) quando DATAJUD_API_KEY está configurada;
    sem a chave, levanta ConectorNaoConfiguradoError como antes — nunca
    finge que a captura está funcionando.
    """
    if nome_fonte == "padrao":
        from flask import current_app
        from app.utils.conector_datajud import ConectorDataJud

        if current_app.config.get("DATAJUD_API_KEY"):
            return ConectorDataJud()

        raise ConectorNaoConfiguradoError(
            "DATAJUD_API_KEY não configurada — cadastre-se de graça em "
            "https://datajud-wiki.cnj.jus.br/, gere uma chave de API e "
            "defina DATAJUD_API_KEY no .env do servidor. Para cobrir o que "
            "o DataJud não cobre (busca por nome/CPF sem número, inteiro "
            "teor, publicação por OAB), seria necessário um provedor pago "
            "(Judit, Escavador, Digesto ou Codilo)."
        )

    raise ConectorNaoConfiguradoError(
        f"Nenhum conector de captura configurado para '{nome_fonte}'."
    )
