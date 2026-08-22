"""
Interface de conectores de captura automática (seções 5.0/5.2 do briefing).

Status: o conector "padrão" (`obter_conector("padrao")`) já está LIGADO —
usa o DataJud, a API pública e gratuita do CNJ (ver
app/utils/conector_datajud.py para o que ela cobre e o que não cobre).
Por padrão usa `DATAJUD_API_KEY` configurada no `.env` do servidor
(compartilhada entre todas as empresas), mas desde a rodada BYOK cada
empresa também pode cadastrar a PRÓPRIA chave do DataJud (também gratuita,
cadastro individual em https://datajud-wiki.cnj.jus.br/) em "Minhas
Integrações" (app/routes/integracoes.py) e usá-la no lugar da chave
compartilhada — passe a `empresa` para `obter_conector` para isso ser
respeitado (ver app/routes/governanca.py e capturar_movimentacoes.py).

Um provedor pago (Judit, Escavador, Digesto ou Codilo — recomendação
original do briefing, seção 5.2) continua sendo a única forma de cobrir o
que o DataJud não cobre: busca de processos por nome/CPF sem já ter o
número, inteiro teor de documentos, e monitoramento de publicação por OAB.
Isso NÃO está implementado (nem como opção "traga sua própria chave"):
cada um desses provedores tem um contrato de API próprio e diferente, e
implementar contra um deles sem a documentação e credenciais reais do
provedor contratado arriscaria produzir uma integração que parece
funcionar mas devolve dado errado ou incompleto silenciosamente — o
oposto do princípio deste módulo. Se/quando um desses for contratado,
basta implementar uma nova subclasse de `ConectorCaptura` (ex:
`ConectorJudit`) e registrá-la aqui (o ponto de extensão já existe).

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
    # Detalhe estruturado extra que algumas fontes trazem por movimentação
    # (ex: resultado de julgamento, tipo de audiência — ver PENDENCIAS.md,
    # seção -37) — já formatado como texto legível por quem implementa o
    # conector, porque o formato varia conforme o tipo de ato. None é o
    # normal (a maioria dos atos não tem nada aqui), não um erro.
    complemento: str | None = None


@dataclass
class PublicacaoCapturada:
    diario: str
    data_disponibilizacao: object  # date
    data_publicacao: object  # date
    teor: str
    oab_destinataria: str | None
    hash_dedup: str


@dataclass
class ProcessoEncontradoDueDiligence:
    """
    Um processo encontrado numa busca por parte (CPF/CNPJ/nome) — due
    diligence de cliente novo, PENDENCIAS.md seção -53. Só metadado de
    identificação/situação, nunca o inteiro teor (isso é
    `consultar_processo`, chamado depois se o advogado decidir acompanhar
    o processo de verdade).
    """
    numero_processo: str
    tribunal: str | None
    classe: str | None
    assunto: str | None
    situacao: str | None  # "ativo", "arquivado", "baixado"... conforme o vocabulário da fonte
    data_distribuicao: object | None  # date
    polo_da_parte_buscada: str | None  # "autor", "réu", "outro"...
    fonte: str  # nome_fonte do conector que encontrou


class ConectorCaptura(ABC):
    """Contrato que um conector real (Judit/Escavador/Digesto/Codilo/Jusbrasil
    Soluções/scraper de DJE ou PJe) precisa implementar."""

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

    @abstractmethod
    def buscar_processos_por_parte(self, cpf_cnpj: str | None = None,
                                    nome: str | None = None) -> list[ProcessoEncontradoDueDiligence]:
        """
        Due diligence de cliente novo (PENDENCIAS.md, seção -53): busca
        TODO processo no Brasil em que a pessoa/empresa informada (por
        CPF/CNPJ, ou por nome quando o documento não for informado) apareça
        como parte — não só nos processos já cadastrados neste escritório
        (isso é `app/utils/conflito_interesse.py`, uma checagem diferente e
        gratuita). Pelo menos um dos dois parâmetros deve vir preenchido.

        Este é exatamente o tipo de busca que o DataJud (gratuito) NÃO
        cobre — exige um provedor pago (ver `obter_conector` abaixo).
        """
        raise NotImplementedError


class ConectorNaoConfiguradoError(Exception):
    pass


def obter_conector(nome_fonte: str, empresa=None) -> ConectorCaptura:
    """
    Fábrica de conectores. "padrao" usa o DataJud (gratuito, ver
    app/utils/conector_datajud.py).

    `empresa`: quando informada e configurada para usar chave própria
    (`empresa.datajud_provedor_efetivo == Empresa.PROVEDOR_DATAJUD_CHAVE_PROPRIA`,
    ver app/routes/integracoes.py), usa a chave cifrada dessa empresa em
    vez de `DATAJUD_API_KEY` do `.env`. Sem `empresa` (ou empresa
    configurada para o padrão), usa a chave compartilhada da plataforma
    como sempre. Nunca finge que a captura está funcionando: sem nenhuma
    chave disponível, levanta ConectorNaoConfiguradoError.
    """
    if nome_fonte == "padrao":
        from flask import current_app
        from app.models import Empresa
        from app.utils.conector_datajud import ConectorDataJud
        from app.utils import cofre

        if empresa is not None and empresa.datajud_provedor_efetivo == Empresa.PROVEDOR_DATAJUD_CHAVE_PROPRIA:
            if not empresa.datajud_chave_propria_cifrada:
                raise ConectorNaoConfiguradoError(
                    f"A empresa \"{empresa.nome}\" está configurada para usar uma chave própria do "
                    "DataJud, mas nenhuma chave foi cadastrada ainda. Cadastre em \"Minhas "
                    "Integrações\", ou volte a usar a chave padrão da plataforma."
                )
            try:
                chave_propria = cofre.decifrar_segredo(empresa.datajud_chave_propria_cifrada)
            except (cofre.CofreNaoConfiguradoError, ValueError) as e:
                raise ConectorNaoConfiguradoError(str(e)) from e
            return ConectorDataJud(api_key=chave_propria)

        if current_app.config.get("DATAJUD_API_KEY"):
            return ConectorDataJud()

        raise ConectorNaoConfiguradoError(
            "DATAJUD_API_KEY não configurada — cadastre-se de graça em "
            "https://datajud-wiki.cnj.jus.br/, gere uma chave de API e "
            "defina DATAJUD_API_KEY no .env do servidor (ou cada empresa pode cadastrar sua "
            "própria chave em \"Minhas Integrações\"). Para cobrir o que o DataJud não cobre "
            "(busca por nome/CPF sem número, inteiro teor, publicação por OAB), seria necessário "
            "um provedor pago (Judit, Escavador, Digesto, Codilo ou Jusbrasil Soluções) — não "
            "implementado."
        )

    if nome_fonte == "due_diligence":
        # Ponto de extensão pronto (PENDENCIAS.md, seção -53), mas SEM
        # nenhum provedor implementado — mesma regra do resto deste módulo:
        # nunca implementar contra um provedor pago sem credencial e
        # documentação reais dele (arriscaria devolver dado errado/incompleto
        # silenciosamente). Quando um for contratado, implemente uma
        # subclasse de ConectorCaptura (ex: ConectorJudit) com os 3 métodos
        # do contrato e registre aqui — o resto do sistema (rota, template)
        # já está pronto pra chamar `buscar_processos_por_parte`.
        raise ConectorNaoConfiguradoError(
            "Nenhum provedor de due diligence (busca de processo por CPF/CNPJ/nome em todo o "
            "Brasil) configurado. Isso exige contratar um provedor pago — as opções conhecidas hoje "
            "são Judit, Escavador, Digesto, Codilo ou Jusbrasil Soluções (cada um com contrato de "
            "API próprio e preço diferente). Depois de escolher e contratar um, é só implementar "
            "esse conector aqui (o ponto de extensão já existe)."
        )

    raise ConectorNaoConfiguradoError(
        f"Nenhum conector de captura configurado para '{nome_fonte}'."
    )
