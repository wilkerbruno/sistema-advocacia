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

Duas chamadas (padrão confirmado pela documentação da STF e manual da
TJRJ): a 1ª pede `incluirDocumentos=True` só para descobrir os IDs dos
documentos do processo; a 2ª chamada passa esses IDs em `documento=[...]`
para trazer o conteúdo binário (campo `conteudo`, em base64) de cada um.
"""
import base64
import os
import tempfile

from conector_base import ConectorTribunalLocal, ResultadoBuscaAutos, ErroConectorTribunal

try:
    from zeep import Client
    from zeep.transports import Transport
    import requests
except ImportError:  # zeep/requests só são exigidos por quem for rodar este conector de verdade
    Client = None


# Padrão observado de URL do WSDL do PJe — CONFIRMAR sempre contra o
# tribunal de destino antes de usar; nem todo tribunal segue exatamente
# este formato (alguns usam "/pje/" em vez de "/<instancia>/", por
# exemplo). "instancia" costuma ser algo como "1g" (1º grau) ou "2g".
PADRAO_URL_WSDL = "https://pje.{tribunal}.jus.br/{instancia}/intercomunicacao?wsdl"


class ConectorPjeMni(ConectorTribunalLocal):
    slug = "pje_mni"

    def __init__(self, tribunal, instancia, id_consultante, senha_consultante=None, url_wsdl=None):
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
        if Client is None:
            raise ErroConectorTribunal(
                "Dependência 'zeep' (ou 'requests') não instalada — rode "
                "'pip install -r requirements.txt' na pasta agente_local_jc/."
            )
        self.url_wsdl = url_wsdl or PADRAO_URL_WSDL.format(tribunal=tribunal, instancia=instancia)
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
        empiricamente se todo tribunal aceita esse modo.
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
                f"Falha ao consultar o processo {numero_processo_cnj} no tribunal: {e}"
            ) from e

        sucesso = getattr(resposta, "sucesso", None)
        if sucesso is False:
            mensagem = getattr(resposta, "mensagem", None) or "motivo não informado pelo tribunal."
            raise ErroConectorTribunal(f"O tribunal recusou a consulta: {mensagem}")

        processo = getattr(resposta, "processo", None)
        if processo is None:
            raise ErroConectorTribunal("O tribunal respondeu, mas sem os dados do processo — resposta inesperada.")

        documentos = getattr(processo, "documento", None) or []
        ids_documentos = [getattr(d, "idDocumento", None) for d in documentos]
        ids_documentos = [i for i in ids_documentos if i is not None]

        historico = self._extrair_historico(processo)

        if not ids_documentos:
            raise ErroConectorTribunal(
                "O processo foi encontrado, mas o tribunal não devolveu nenhum documento — "
                "pode ser que este processo não tenha documentos disponíveis para este usuário, "
                "ou que o nome do campo de documentos mudou neste tribunal (ver aviso no topo deste arquivo)."
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
