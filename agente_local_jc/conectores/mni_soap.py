"""
Lógica COMPARTILHADA do protocolo MNI (Modelo Nacional de Interoperabilidade
— padrão nacional definido pelo CNJ, não é exclusivo de nenhum sistema de
tribunal específico). PJe, Projudi, e-Proc e e-SAJ são todos OBRIGADOS por
resolução do CNJ a expor um webservice MNI para consulta por sistemas
externos como este — cada um roda numa URL diferente (o domínio/caminho do
WSDL varia por tribunal e por sistema), mas a OPERAÇÃO em si
(`consultarProcesso`, com os parâmetros `idConsultante`, `senhaConsultante`,
`numeroProcesso`, `movimentos`, `incluirDocumentos`, `documento`) é a mesma
definida no XSD/WSDL nacional do CNJ.

Por isso este arquivo existe: em vez de duplicar a mesma lógica de SOAP em
cada `conectores/<tribunal>.py`, ela mora aqui UMA vez, e cada conector de
tribunal (`conectores/pje_mni.py`, `conectores/projudi.py`, ...) vira só
uma casca fina que sabe montar a URL certa (ou exige que ela seja informada
manualmente, quando não há um padrão confiável de URL para aquele sistema)
e empresta o nome certo nas mensagens de erro.

⚠️⚠️⚠️ NENHUMA chamada real foi feita contra nenhum tribunal com este
código — foi escrito a partir da documentação técnica PÚBLICA do CNJ/STF/
TJRJ sobre o protocolo MNI. Antes de usar com um processo de verdade, ver
os avisos específicos em cada arquivo de conector (pje_mni.py, projudi.py)
e agente_local_jc/README.md.
"""
import base64
import os
import tempfile

from conector_base import ConectorTribunalLocal, ResultadoBuscaAutos, ErroConectorTribunal

# Import pesado (zeep -> lxml, requests) ADIADO de propósito (ver
# PENDENCIAS.md, seção sobre lentidão na abertura do agente): importar
# zeep no topo do arquivo — mesmo dentro de um try/except — faz a
# importação de verdade rodar toda vez que QUALQUER módulo importa este
# arquivo, inclusive só de abrir o ícone da bandeja (que passa por
# `motor.py` -> `registro_conectores.py` -> aqui, mesmo sem nenhuma busca
# de autos em andamento). Isso deixava a abertura do agente visivelmente
# lenta (janela em branco por vários segundos). Agora só importa de
# verdade na primeira vez que um conector MNI é realmente construído
# (ou seja, quando uma tarefa de busca de autos é processada).
Client = None
Transport = None
requests = None
_dependencias_carregadas = False


def _carregar_dependencias():
    global Client, Transport, requests, _dependencias_carregadas
    if _dependencias_carregadas:
        return
    try:
        from zeep import Client as _Client
        from zeep.transports import Transport as _Transport
        import requests as _requests
    except ImportError:
        _dependencias_carregadas = True  # não tenta de novo a cada chamada — resultado não muda
        return
    Client, Transport, requests = _Client, _Transport, _requests
    _dependencias_carregadas = True


class ConectorMniBase(ConectorTribunalLocal):
    """
    Implementa a consulta MNI genérica. Uma subclasse por sistema de
    tribunal (PJe, Projudi, ...) só precisa: (1) definir `slug` e
    `nome_exibicao`, e (2) no `__init__`, descobrir/montar a `url_wsdl`
    daquele sistema e chamar `super().__init__(url_wsdl, ...)`.
    """

    nome_exibicao = "tribunal"  # subclasses sobrescrevem (ex: "PJe", "Projudi") — usado só nas mensagens de erro

    def __init__(self, url_wsdl, id_consultante=None, senha_consultante=None):
        _carregar_dependencias()
        if Client is None:
            raise ErroConectorTribunal(
                "Dependência 'zeep' (ou 'requests') não instalada — rode "
                "'pip install -r requirements.txt' na pasta agente_local_jc/."
            )
        if not url_wsdl:
            raise ErroConectorTribunal(
                f"URL do WSDL do {self.nome_exibicao} não configurada — preencha em "
                "\"Configurações avançadas\", no agente local (menu do ícone → Configurar...). "
                f"Essa URL varia por tribunal — confirme com quem administra o {self.nome_exibicao} "
                "no tribunal certo antes de preencher."
            )
        self.url_wsdl = url_wsdl
        self.id_consultante = id_consultante
        self.senha_consultante = senha_consultante

    def _montar_cliente_soap(self, certificado):
        """
        Autenticação mTLS: usa o certificado do advogado (já
        descriptografado em memória — ver certificado.py) para o
        handshake TLS da conexão HTTPS com o tribunal. Isso é
        INDEPENDENTE de idConsultante/senhaConsultante — segundo a
        documentação do CNJ, se a autenticação por certificado for aceita
        pelo tribunal, usuário/senha ficam dispensados; não confirmado
        empiricamente se todo tribunal/sistema aceita esse modo.
        """
        sessao = requests.Session()
        if certificado is not None:
            sessao.cert = certificado.caminho_pem_temporario()
        transporte = Transport(session=sessao, timeout=60)
        try:
            return Client(self.url_wsdl, transport=transporte)
        except Exception as e:
            raise ErroConectorTribunal(
                f"Não foi possível carregar o WSDL de {self.url_wsdl} — confira se a URL está "
                f"correta para este tribunal e se há conexão com a internet. Detalhe: {e}"
            ) from e

    def buscar_autos_completos(self, numero_processo_cnj, certificado):
        cliente = self._montar_cliente_soap(certificado)

        kwargs_consulta = dict(
            numeroProcesso=numero_processo_cnj,
            movimentos=True,
            incluirDocumentos=True,
        )
        if self.id_consultante:
            kwargs_consulta["idConsultante"] = self.id_consultante
            kwargs_consulta["senhaConsultante"] = self.senha_consultante

        try:
            resposta = cliente.service.consultarProcesso(**kwargs_consulta)
        except Exception as e:
            raise ErroConectorTribunal(
                f"Falha ao consultar o processo {numero_processo_cnj} no {self.nome_exibicao}: {e}"
            ) from e

        sucesso = getattr(resposta, "sucesso", None)
        if sucesso is False:
            mensagem = getattr(resposta, "mensagem", None) or "motivo não informado pelo tribunal."
            raise ErroConectorTribunal(f"O {self.nome_exibicao} recusou a consulta: {mensagem}")

        processo = getattr(resposta, "processo", None)
        if processo is None:
            raise ErroConectorTribunal(f"O {self.nome_exibicao} respondeu, mas sem os dados do processo — resposta inesperada.")

        documentos = getattr(processo, "documento", None) or []
        ids_documentos = [getattr(d, "idDocumento", None) for d in documentos]
        ids_documentos = [i for i in ids_documentos if i is not None]

        historico = self._extrair_historico(processo)

        if not ids_documentos:
            raise ErroConectorTribunal(
                f"O processo foi encontrado no {self.nome_exibicao}, mas não veio nenhum documento — "
                "pode ser que este processo não tenha documentos disponíveis para este usuário, "
                "ou que o nome do campo de documentos mudou neste tribunal (ver aviso no topo do arquivo "
                "deste conector)."
            )

        # 2ª chamada: pede o conteúdo binário dos documentos encontrados.
        try:
            resposta_docs = cliente.service.consultarProcesso(
                **{**kwargs_consulta, "documento": ids_documentos}
            )
        except Exception as e:
            raise ErroConectorTribunal(f"Falha ao baixar o conteúdo dos documentos: {e}") from e

        caminho_pdf = self._consolidar_pdf(getattr(resposta_docs.processo, "documento", None) or [])
        return ResultadoBuscaAutos(caminho_pdf=caminho_pdf, historico_movimentacoes=historico)

    @staticmethod
    def _extrair_historico(processo):
        """
        Formato de cada movimentação NÃO foi confirmado por fonte
        primária (o WSDL do CNJ define `movimento` com vários campos
        opcionais) — usa getattr defensivo pra não quebrar se algum
        campo não vier, e loga só o que existir.
        """
        historico = []
        for mov in (getattr(processo, "movimento", None) or []):
            historico.append({
                "data": str(getattr(mov, "dataHora", "") or ""),
                "descricao": str(getattr(mov, "descricao", "") or getattr(mov, "movimentoNacional", "") or ""),
            })
        return historico

    @staticmethod
    def _consolidar_pdf(documentos_com_conteudo):
        """
        Decodifica o `conteudo` (base64) de cada documento e junta os que
        forem PDF num único arquivo consolidado (usando pypdf, se
        instalado) — documentos em outro formato (imagem digitalizada
        sem OCR, por exemplo) são salvos à parte e ignorados na junção,
        já que o backend do JusControl só recebe um PDF por busca neste
        piloto (ver app/routes/agente_local_api.py::enviar_resultado).
        """
        pasta_temp = tempfile.mkdtemp(prefix="jc_agente_autos_")
        caminhos_pdf = []
        for i, doc in enumerate(documentos_com_conteudo):
            conteudo_b64 = getattr(doc, "conteudo", None)
            if not conteudo_b64:
                continue
            try:
                dados_binarios = base64.b64decode(conteudo_b64)
            except Exception:
                continue
            # mimetype não é um campo confirmado — checa pela assinatura
            # do próprio arquivo (mais confiável que confiar num nome de
            # campo não verificado).
            if dados_binarios[:4] != b"%PDF":
                continue
            caminho = os.path.join(pasta_temp, f"documento_{i}.pdf")
            with open(caminho, "wb") as f:
                f.write(dados_binarios)
            caminhos_pdf.append(caminho)

        if not caminhos_pdf:
            raise ErroConectorTribunal(
                "Nenhum dos documentos devolvidos pelo tribunal era um PDF reconhecível — "
                "confira manualmente se este processo tem autos em outro formato."
            )

        if len(caminhos_pdf) == 1:
            return caminhos_pdf[0]

        try:
            from pypdf import PdfWriter
        except ImportError:
            # Sem pypdf instalado, devolve só o primeiro documento — melhor
            # entregar algo do que travar o piloto por uma dependência opcional.
            return caminhos_pdf[0]

        writer = PdfWriter()
        for caminho in caminhos_pdf:
            writer.append(caminho)
        caminho_final = os.path.join(pasta_temp, "autos_completos_consolidado.pdf")
        with open(caminho_final, "wb") as f:
            writer.write(f)
        return caminho_final
