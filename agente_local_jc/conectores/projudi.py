"""
Conector Projudi via MNI (Modelo Nacional de Interoperabilidade) — PILOTO.

⚠️⚠️⚠️ AINDA NÃO TESTADO CONTRA NENHUM TRIBUNAL REAL ⚠️⚠️⚠️
O Projudi (sistema da Softplan, usado por vários TJs e alguns TRTs) é
OBRIGADO, pela mesma resolução do CNJ que obriga o PJe, a expor um
webservice MNI para consulta por sistemas externos — mesma operação
`consultarProcesso`, mesmos nomes de campo (`idConsultante`,
`senhaConsultante`, `numeroProcesso`, `movimentos`, `incluirDocumentos`,
`documento`) — ver `conectores/mni_soap.py` para a lógica compartilhada
com o conector do PJe.

A diferença prática que importa aqui: ao contrário do PJe (onde os
tribunais costumam seguir o padrão de domínio "pje.<sigla>.jus.br"),
NÃO existe um padrão de URL conhecido/observado para o WSDL do Projudi
entre os tribunais que o usam — cada TJ hospeda o Projudi (e o endpoint
MNI dele) num domínio próprio, sem convenção nacional confirmada. Por
isso este conector NÃO tenta adivinhar a URL: `url_wsdl` é obrigatória,
e precisa ser obtida diretamente com a área técnica (DTI) do tribunal
específico antes de usar — normalmente junto com o cadastro de
"consultante" (`idConsultante`/`senhaConsultante`) do MNI daquele
tribunal.

Antes de usar isto com um processo real:
  1. Descubra com o tribunal a URL real do WSDL de intercomunicação MNI
     dele (pergunte pela "URL do serviço de intercomunicação MNI" — o
     termo costuma ser reconhecido pela área técnica/DTI).
  2. Confirme se os nomes de campo que este arquivo assume (herdados do
     protocolo nacional, os mesmos usados no conector do PJe) batem com
     a resposta real desse tribunal — se não baterem, é preciso ajustar
     `conectores/mni_soap.py` (ou, se for uma diferença específica só
     do Projudi, criar um ajuste aqui neste arquivo).
  3. Só depois disso marque este conector como "confiável" para uso em
     produção — ver PENDENCIAS.md.
"""
from conectores.mni_soap import ConectorMniBase


class ConectorProjudi(ConectorMniBase):
    slug = "projudi"
    nome_exibicao = "Projudi"

    def __init__(self, id_consultante=None, senha_consultante=None, url_wsdl=None, **_ignorado):
        """
        Sem URL padrão adivinhada (ver aviso no topo do arquivo) —
        `url_wsdl` é obrigatória; se vier vazia, `ConectorMniBase` já
        levanta um erro legível explicando onde preencher.

        `**_ignorado`: absorve com segurança qualquer chave extra que
        `registro_conectores.py` venha a passar no futuro (ex: campos
        específicos de outro sub-tipo do Projudi), sem quebrar por causa
        de um argumento inesperado.
        """
        super().__init__(url_wsdl, id_consultante=id_consultante, senha_consultante=senha_consultante)
