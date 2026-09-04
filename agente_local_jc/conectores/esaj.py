"""
Conector e-SAJ (TJSP, TJMS e outros tribunais estaduais que licenciam o
sistema da Softplan) — tentativa via MNI, mesmo protocolo do PJe/Projudi.

⚠️⚠️⚠️ MAIS INCERTO QUE OS OUTROS DOIS CONECTORES — LEIA ANTES DE USAR ⚠️⚠️⚠️
Diferente do PJe e do Projudi (que reaproveitam a mesma lógica em
`conectores/mni_soap.py`), **não encontrei nenhuma documentação pública
confirmando que o e-SAJ expõe um webservice MNI** para consulta por
sistemas externos — a arquitetura visível do e-SAJ (portal web,
peticionamento com um plugin/applet de assinatura no navegador, "Sistema
Push" próprio pra notificação de movimentação) sugere um desenho bem
diferente do PJe, sem garantia nenhuma de que a operação
`consultarProcesso` do MNI exista nesse sistema.

Mesmo assim, este conector foi escrito reaproveitando a mesma lógica MNI
(pedido explícito: tentar esse caminho primeiro, por ser rápido/barato de
construir — se o tribunal não expuser esse serviço, o erro que volta é
direto e claro: "não foi possível carregar o WSDL", o que já confirma
rapidamente que é preciso um caminho diferente, como automação de
navegador de verdade — ver PENDENCIAS.md).

Assim como no Projudi, NÃO existe um padrão de URL conhecido entre os
tribunais que usam e-SAJ (cada TJ hospeda a própria instância, em domínio
próprio, ex: `esaj.tjms.jus.br`, `esaj.tjsp.jus.br`) — `url_wsdl` é
obrigatória, sem tentativa de adivinhação.

Antes de usar isto com um processo real:
  1. Descubra com o tribunal (DTI) se existe mesmo um serviço de
     intercomunicação MNI publicado, e qual a URL do WSDL dele — pode
     ser que a resposta seja "não existe", e nesse caso este conector
     nunca vai funcionar, não importa o que se ajuste aqui.
  2. Se existir, confirme os nomes de campo (podem não bater com os que
     `mni_soap.py` assume, herdados da documentação do PJe/CNJ).
  3. Se não existir, a alternativa real é automação de navegador (login
     no portal e-SAJ com o certificado, navegar até o processo, baixar o
     PDF) — um conector bem diferente deste, ainda não construído.
"""
from conectores.mni_soap import ConectorMniBase


class ConectorEsaj(ConectorMniBase):
    slug = "esaj_sp"
    nome_exibicao = "e-SAJ"

    def __init__(self, id_consultante=None, senha_consultante=None, url_wsdl=None, **_ignorado):
        """
        Sem URL padrão adivinhada (ver aviso no topo do arquivo) —
        `url_wsdl` é obrigatória; se vier vazia, `ConectorMniBase` já
        levanta um erro legível explicando onde preencher.

        `**_ignorado`: absorve com segurança qualquer chave extra que
        `registro_conectores.py` venha a passar no futuro.
        """
        super().__init__(url_wsdl, id_consultante=id_consultante, senha_consultante=senha_consultante)
