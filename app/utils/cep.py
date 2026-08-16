"""
Busca de endereço a partir do CEP, usando o ViaCEP (viacep.com.br) — API
pública brasileira, gratuita, sem necessidade de cadastro/chave.

A consulta é feita aqui no BACKEND (nunca direto do navegador do usuário)
por dois motivos: o ViaCEP não documenta oficialmente suporte a CORS para
chamada direta do navegador (ou seja, chamar direto do JS do cliente
poderia simplesmente não funcionar em alguns navegadores/situações), e
fazer a chamada no servidor permite aplicar timeout e tratamento de erro
consistentes com o resto do sistema, sem depender de nada externo além
desta única chamada HTTP simples. Ver app/routes/api.py (rota
GET /api/cep/<cep>) para quem consome isso.
"""
import requests

VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"
TIMEOUT_SEGUNDOS = 8


class CepInvalidoError(Exception):
    """CEP com formato inválido (precisa ter exatamente 8 dígitos)."""


class CepNaoEncontradoError(Exception):
    """CEP com formato válido, mas não encontrado pelo ViaCEP, ou falha
    de rede/serviço ao consultar."""


def consultar_cep(cep: str) -> dict:
    digitos = "".join(c for c in (cep or "") if c.isdigit())
    if len(digitos) != 8:
        raise CepInvalidoError("CEP deve ter 8 dígitos.")

    try:
        resposta = requests.get(VIACEP_URL.format(cep=digitos), timeout=TIMEOUT_SEGUNDOS)
    except requests.RequestException as e:
        raise CepNaoEncontradoError(f"Falha ao consultar o CEP: {e}") from e

    if resposta.status_code != 200:
        raise CepNaoEncontradoError(f"Serviço de CEP respondeu {resposta.status_code}.")

    try:
        dados = resposta.json()
    except ValueError as e:
        raise CepNaoEncontradoError("Resposta inesperada do serviço de CEP.") from e

    if dados.get("erro"):
        raise CepNaoEncontradoError("CEP não encontrado.")

    return {
        "cep": dados.get("cep") or digitos,
        "logradouro": dados.get("logradouro") or "",
        "bairro": dados.get("bairro") or "",
        "cidade": dados.get("localidade") or "",
        "estado": dados.get("uf") or "",
    }
