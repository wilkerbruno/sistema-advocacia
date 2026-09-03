"""
Contrato que TODO conector de tribunal do Agente Local precisa
implementar — mesmo espírito de `ConectorCaptura`
(app/utils/captura_conectores.py, no repositório principal do
JusControl), mas do lado do agente local: quem implementa aqui roda na
MÁQUINA DO ADVOGADO, com o certificado dele, nunca no servidor.

Arquitetura multi-tribunal desde o início (pedido explícito do
cliente): cada tribunal (ou família de sistemas de tribunal — PJe,
e-SAJ, e-Proc, Projudi...) ganha um conector próprio em
conectores/, todos implementando esta mesma interface. main.py escolhe
qual conector usar olhando o campo `tribunal_conector` que veio do
JusControl (ver app/utils/tribunais_conectores.py no repo principal —
o slug escolhido lá na tela do processo é o mesmo nome usado no
REGISTRO_CONECTORES abaixo).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ResultadoBuscaAutos:
    """O que um conector devolve depois de buscar os autos completos."""
    caminho_pdf: str          # arquivo local temporário com o PDF consolidado dos autos
    historico_movimentacoes: list  # lista de dicts — mesmo formato aproximado do DataJud
    # (data, descricao/codigo_tpu, orgao_julgador) — não precisa ser
    # idêntico ao DataJud; o backend só guarda o PDF por enquanto (ver
    # app/routes/agente_local_api.py::enviar_resultado), o histórico
    # detalhado é uma extensão futura (fast-follow: casar com
    # app/utils/captura_pipeline.py::registrar_movimentacoes_capturadas).


class ErroConectorTribunal(Exception):
    """Erro esperado (credencial inválida, processo não encontrado,
    tribunal fora do ar etc.) — a mensagem desta exceção é o que vira
    `mensagem_erro` na tela do JusControl, então deve ser compreensível
    para o advogado, não um traceback técnico cru."""
    pass


class ConectorTribunalLocal(ABC):
    """Um conector = "sei buscar os autos completos de UM tribunal (ou
    família de tribunais), autenticando com o certificado do advogado,
    rodando na máquina dele"."""

    slug: str  # precisa bater com o slug em app/utils/tribunais_conectores.py

    @abstractmethod
    def buscar_autos_completos(self, numero_processo_cnj: str, certificado) -> ResultadoBuscaAutos:
        """
        `certificado`: um CertificadoCarregado (ver certificado.py) já
        aberto — a chave privada nunca sai deste processo.

        Deve levantar ErroConectorTribunal com uma mensagem legível em
        qualquer falha esperada (não deixar exceção crua da lib SOAP/
        HTTP vazar para quem chama).
        """
        raise NotImplementedError
