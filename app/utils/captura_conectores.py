"""
Interface de conectores de captura automática (seções 5.0/5.2 do briefing).

⚠️ BLOQUEADO — depende de decisão e credenciais fora do código.

Este módulo define o "encaixe" (interface) que qualquer conector de
captura precisa implementar, para que a ingestão automática (seção 5.0)
possa ser ligada assim que:

  1. O cliente/você escolher um provedor (Judit, Escavador, Digesto ou
     Codilo — recomendação do próprio briefing, seção 5.2) ou decidir por
     scraping direto de DJE/e-SAJ/PJe.
  2. Uma chave de API (ou credencial de scraping) for obtida junto a esse
     provedor e configurada em variável de ambiente.
  3. O ambiente de produção tiver rede de saída liberada para o domínio
     do provedor (este sandbox de geração de código só acessa uma lista
     restrita de domínios — PyPI, GitHub, npm etc. — e não alcança APIs de
     dados processuais; a integração real só pode ser testada a partir do
     servidor onde o sistema for hospedado).

Sem isso, não há como escrever nem testar a chamada real: cada provedor
tem contrato de API, autenticação e formato de resposta próprios que só
existem na documentação da conta contratada.

Nenhuma classe abaixo faz requisição de rede — são apenas o contrato que
uma implementação futura deve seguir.
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
    Fábrica de conectores. Hoje sempre levanta ConectorNaoConfiguradoError —
    nenhum provedor foi contratado/configurado ainda. Quando um provedor for
    escolhido, implemente uma subclasse de ConectorCaptura (ex:
    ConectorJudit, ConectorEscavador) e registre aqui.
    """
    raise ConectorNaoConfiguradoError(
        f"Nenhum conector de captura configurado para '{nome_fonte}'. "
        "É necessário escolher um provedor de dados processuais (Judit, "
        "Escavador, Digesto ou Codilo — seção 5.2 do briefing), contratar a "
        "API, e implementar a subclasse correspondente de ConectorCaptura."
    )
