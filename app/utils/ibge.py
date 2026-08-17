"""
Consulta ao nome de um município a partir do código IBGE — usado pra
transformar o `codigoMunicipioIBGE` que a API do DataJud devolve dentro de
`orgaoJulgador` (ver app/utils/conector_datajud.py) numa "Comarca"
legível (ex: "Campo Grande - MS"), pra autopreencher o cadastro de
processo (app/routes/processos.py, app/routes/governanca.py).

API pública, gratuita, sem chave, do próprio IBGE (Instituto Brasileiro de
Geografia e Estatística — API de Localidades):
https://servicodados.ibge.gov.br/api/docs/localidades

⚠️ Melhor esforço, nunca trava nada: se a API do IBGE estiver fora do ar,
devolver algo inesperado, ou o código não existir, `nome_municipio` só
devolve `None` — quem chama trata isso como "não deu pra descobrir a
comarca", nunca como erro. Documentado como não testado contra uma chamada
real (mesma limitação de rede do ambiente onde este código foi gerado, ver
app/utils/conector_datajud.py) — o formato da URL e da resposta é o
documentado publicamente pelo IBGE, mas vale confirmar com um caso real
depois do deploy.
"""
import requests

BASE_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"

_cache = {}  # codigo (int|str) -> "Cidade - UF" | None — evita repetir a
             # mesma chamada de rede várias vezes na mesma execução do processo
             # (ex: preview + salvar, ou vários processos da mesma comarca).


def nome_municipio(codigo):
    """Devolve "Cidade - UF" (ex: "Campo Grande - MS") a partir do código
    IBGE do município, ou None se não for possível descobrir (sem código,
    erro de rede, resposta inesperada)."""
    if not codigo:
        return None
    codigo = str(codigo)
    if codigo in _cache:
        return _cache[codigo]

    resultado = None
    try:
        resposta = requests.get(f"{BASE_URL}/{codigo}", timeout=8)
        if resposta.status_code == 200:
            dados = resposta.json()
            nome = dados.get("nome")
            uf = (
                dados.get("microrregiao", {})
                .get("mesorregiao", {})
                .get("UF", {})
                .get("sigla")
            ) or (
                dados.get("regiao-imediata", {})
                .get("regiao-intermediaria", {})
                .get("UF", {})
                .get("sigla")
            )
            if nome:
                resultado = f"{nome} - {uf}" if uf else nome
    except (requests.RequestException, ValueError):
        resultado = None  # melhor esforço — nunca propaga erro por causa de um campo secundário

    _cache[codigo] = resultado
    return resultado
