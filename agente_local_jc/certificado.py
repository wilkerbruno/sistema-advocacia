"""
Carrega um certificado digital A1 (arquivo .pfx/.p12 + senha) da própria
máquina do advogado, de forma TRANSIENTE — só na memória do processo,
nunca gravado em disco descriptografado, nunca enviado para lugar
nenhum, nem mesmo para o servidor do JusControl.

⚠️ Isto cobre só A1 (arquivo portátil). Certificado A3 (token físico/
smartcard) tem a chave privada NÃO EXPORTÁVEL por desenho do próprio
hardware — não dá pra "carregar" ele aqui; um conector A3 precisaria de
um driver PKCS#11 específico do fabricante do token (ex: SafeNet,
Watchdata) e fica como fast-follow, fora do escopo deste piloto.

Usa a biblioteca `cryptography` (já madura, mantida pela Python
Cryptographic Authority) para abrir o .pfx e extrair a chave privada + o
certificado — depois disso, o par (chave, certificado) é usado só para
a autenticação mTLS da chamada SOAP ao tribunal (ver
conectores/pje_mni.py), nunca persistido nem logado.
"""
import tempfile
import os

from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption


class CertificadoInvalidoError(Exception):
    pass


class CertificadoCarregado:
    """
    Mantém o par chave-privada/certificado só em memória (e, quando
    `requests` exige um caminho de arquivo para `cert=`, num arquivo
    temporário criado com permissão restrita e apagado ao sair do
    `with`) — nunca escreve a senha nem a chave em log.
    """

    def __init__(self, chave_privada, certificado, cadeia_adicional):
        self._chave_privada = chave_privada
        self._certificado = certificado
        self._cadeia_adicional = cadeia_adicional or []
        self._arquivo_temp_pem = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._limpar_arquivo_temp()

    def _limpar_arquivo_temp(self):
        if self._arquivo_temp_pem and os.path.exists(self._arquivo_temp_pem):
            try:
                os.remove(self._arquivo_temp_pem)
            finally:
                self._arquivo_temp_pem = None

    def caminho_pem_temporario(self):
        """
        `requests` (usado por zeep) espera um caminho de arquivo PEM para
        autenticação mTLS (`cert=(caminho_cert, caminho_chave)` ou um PEM
        combinado) — como a chave só existe descriptografada em memória
        aqui, grava um arquivo temporário SÓ enquanto a chamada dura, com
        permissão restrita a este usuário (0600), e apaga logo depois
        (ver __exit__/limpar). Devolve o caminho do arquivo combinado
        (certificado + chave, sem senha — já foi descriptografada).
        """
        if self._arquivo_temp_pem is not None:
            return self._arquivo_temp_pem

        fd, caminho = tempfile.mkstemp(suffix=".pem", prefix="jc_agente_cert_")
        try:
            os.chmod(caminho, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(self._certificado.public_bytes(Encoding.PEM))
                f.write(self._chave_privada.private_bytes(
                    Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption(),
                ))
                for cert_extra in self._cadeia_adicional:
                    f.write(cert_extra.public_bytes(Encoding.PEM))
        except Exception:
            os.remove(caminho)
            raise
        self._arquivo_temp_pem = caminho
        return caminho


def carregar_pfx(caminho_pfx, senha):
    """
    Abre o .pfx/.p12 e devolve um CertificadoCarregado. Levanta
    CertificadoInvalidoError com uma mensagem legível se o arquivo não
    existir, a senha estiver errada, ou o arquivo não for um PFX válido
    — nunca deixa a exceção crua da lib vazar (evita, sem querer, expor
    a senha em algum traceback).
    """
    if not os.path.exists(caminho_pfx):
        raise CertificadoInvalidoError(f"Arquivo de certificado não encontrado: {caminho_pfx}")

    try:
        with open(caminho_pfx, "rb") as f:
            dados_pfx = f.read()
        chave_privada, certificado, cadeia_adicional = pkcs12.load_key_and_certificates(
            dados_pfx, senha.encode("utf-8") if senha else None,
        )
    except Exception as e:
        raise CertificadoInvalidoError(
            "Não foi possível abrir o certificado — confira o caminho e a senha. "
            f"Detalhe técnico: {type(e).__name__}"
        ) from e

    if chave_privada is None or certificado is None:
        raise CertificadoInvalidoError("O arquivo foi lido, mas não contém uma chave privada e certificado válidos.")

    return CertificadoCarregado(chave_privada, certificado, cadeia_adicional)
