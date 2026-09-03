"""
Conector PJe via MNI (Modelo Nacional de Interoperabilidade) — PILOTO.

⚠️⚠️⚠️ AINDA NÃO TESTADO CONTRA NENHUM TRIBUNAL REAL ⚠️⚠️⚠️
Este arquivo foi escrito a partir de documentação técnica PÚBLICA do
CNJ/STF/TJRJ (operação `consultarProcesso`, parâmetros `idConsultante`,
`senhaConsultante`, `numeroProcesso`, `dataReferencia`, `movimentos`,
`incluirDocumentos`, `documento`) — essas são as únicas informações
confirmadas por fonte primária no momento em que este código foi
escrito. NENHUMA chamada real foi feita contra um tribunal de verdade:
não há credencial de teste, nem confirmação de que o WSDL de um
tribunal específico usa exatamente esses nomes de campo (cada um dos
~90 tribunais roda a própria instância do PJe, com as próprias
variações). Antes de usar isto com um processo real:
  1. Rode contra o WSDL do SEU tribunal primeiro (ambiente de homologação,
     se existir) com uma conta de teste.
  2. Confira o WSDL real (a URL abaixo é um PADRÃO observado, não uma
     garantia — ex: "https://pje.trt2.jus.br/1g/intercomunicacao?wsdl")
     e ajuste os nomes de campo que este arquivo assume, se necessário.
  3. Só depois disso marque este conector como "confiável" para uso em
     produção — ver PENDENCIAS.md seção -56.

A lógica de fato da consulta MNI/SOAP (comum a PJe, Projudi e qualquer
outro sistema de tribunal que implemente o mesmo protocolo nacional do
CNJ) mora em `conectores/mni_soap.py` — este arquivo só sabe montar a
URL do WSDL específica do PJe (a partir de tribunal+instância, com um
padrão observado, OU de uma URL confirmada manualmente).

Duas chamadas (padrão confirmado pela documentação da STF e manual da
TJRJ): a 1ª pede `incluirDocumentos=True` só para descobrir os IDs dos
documentos do processo; a 2ª chamada passa esses IDs em `documento=[...]`
para trazer o conteúdo binário (campo `conteudo`, em base64) de cada um.
"""
from conectores.mni_soap import ConectorMniBase

# Padrão observado de URL do WSDL do PJe — CONFIRMAR sempre contra o
# tribunal de destino antes de usar; nem todo tribunal segue exatamente
# este formato (alguns usam "/pje/" em vez de "/<instancia>/", por
# exemplo). "instancia" costuma ser algo como "1g" (1º grau) ou "2g".
PADRAO_URL_WSDL = "https://pje.{tribunal}.jus.br/{instancia}/intercomunicacao?wsdl"


class ConectorPjeMni(ConectorMniBase):
    slug = "pje_mni"
    nome_exibicao = "PJe"

    def __init__(self, tribunal=None, instancia="1g", id_consultante=None, senha_consultante=None, url_wsdl=None):
        """
        `tribunal`/`instancia`: usados só para montar a URL padrão se
        `url_wsdl` não for passada explicitamente — para o piloto,
        SEMPRE prefira passar `url_wsdl` já confirmada manualmente.

        `id_consultante`/`senha_consultante`: credencial de "consultante"
        cadastrada NAQUELE tribunal especificamente (não é nacional —
        cada tribunal tem o próprio cadastro). Alternativa não
        implementada aqui ainda: autenticação só por certificado mTLS,
        sem usuário/senha (o WSDL do CNJ indica que os dois métodos são
        mutuamente exclusivos, mas o comportamento exato varia por
        tribunal e não foi confirmado por testes reais).
        """
        url = url_wsdl or (PADRAO_URL_WSDL.format(tribunal=tribunal, instancia=instancia) if tribunal else None)
        super().__init__(url, id_consultante=id_consultante, senha_consultante=senha_consultante)
