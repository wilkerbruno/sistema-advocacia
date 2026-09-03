"""
Busca o instalador do Agente Local (.exe) numa Release do GitHub via API
AUTENTICADA — usado quando o repositório do JusControl é PRIVADO (ver
config.py, AGENTE_LOCAL_GITHUB_REPO/AGENTE_LOCAL_GITHUB_TOKEN, e
PENDENCIAS.md seção -58). Assim o advogado baixa o instalador sem nunca
acessar o GitHub, e o token (só leitura, escopado a este repositório)
nunca é exposto ao navegador dele — ver app/routes/agente_local.py::
baixar_instalador, que é quem chama isto e entrega os bytes.

Se o repositório for PÚBLICO, isto nem é usado — o link direto de
AGENTE_LOCAL_INSTALADOR_URL (config.py) já funciona sozinho, sem
precisar de token nenhum.
"""
import requests

NOME_ASSET_PADRAO = "JusControlAgente-Setup.exe"


class InstaladorIndisponivelError(Exception):
    """Mensagem já pensada pra aparecer direto num flash() pro usuário —
    nunca deixa um erro técnico cru (traceback, JSON da API) vazar pra
    tela."""
    pass


def _cabecalhos(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def localizar_asset_da_ultima_release(repo, token, nome_asset=NOME_ASSET_PADRAO):
    """Devolve (asset_id, nome_arquivo) do instalador na Release mais
    recente do repositório ("dono/repositorio"), ou levanta
    InstaladorIndisponivelError com uma mensagem legível."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        r = requests.get(url, headers=_cabecalhos(token), timeout=20)
    except requests.RequestException as e:
        raise InstaladorIndisponivelError(f"Não foi possível falar com o GitHub: {e}") from e

    if r.status_code == 404:
        raise InstaladorIndisponivelError(
            "Nenhuma Release publicada ainda nesse repositório (ou o repositório/token está "
            "errado) — publique uma tag \"agente-vX.Y.Z\" primeiro, ver agente_local_jc/README.md."
        )
    if r.status_code == 401:
        raise InstaladorIndisponivelError(
            "O token em AGENTE_LOCAL_GITHUB_TOKEN foi rejeitado pelo GitHub (expirado ou inválido)."
        )
    if r.status_code == 403:
        raise InstaladorIndisponivelError(
            "O GitHub recusou o acesso — confira se o token tem permissão de leitura em "
            "\"Contents\" para este repositório."
        )
    if r.status_code != 200:
        raise InstaladorIndisponivelError(f"O GitHub respondeu de forma inesperada (HTTP {r.status_code}).")

    dados = r.json()
    for asset in dados.get("assets", []):
        if asset.get("name") == nome_asset:
            return asset["id"], asset["name"]

    raise InstaladorIndisponivelError(
        f"A Release mais recente não tem um arquivo chamado \"{nome_asset}\" anexado — "
        "confira se o build do GitHub Actions terminou com sucesso (aba Actions do repositório)."
    )


def baixar_bytes_do_asset(repo, token, asset_id):
    """
    Devolve (conteudo_bytes, content_type) do asset.

    O GitHub redireciona essa chamada pro armazenamento real dos
    arquivos (objects.githubusercontent.com), com a autorização já
    embutida na própria URL assinada — a 2ª requisição (a que baixa o
    arquivo de verdade) é feita SEM o cabeçalho Authorization do GitHub,
    de propósito: mesmo o `requests` já cortando esse cabeçalho sozinho
    ao trocar de domínio num redirecionamento, aqui isso é feito à mão,
    pra não depender desse comportamento por baixo — o token nunca deve
    viajar pra um host que não seja api.github.com.
    """
    url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    cabecalhos = _cabecalhos(token)
    cabecalhos["Accept"] = "application/octet-stream"

    try:
        r1 = requests.get(url, headers=cabecalhos, timeout=20, allow_redirects=False)
    except requests.RequestException as e:
        raise InstaladorIndisponivelError(f"Não foi possível baixar do GitHub: {e}") from e

    if r1.status_code in (302, 303, 307, 308):
        localizacao = r1.headers.get("Location")
        if not localizacao:
            raise InstaladorIndisponivelError("O GitHub redirecionou sem indicar pra onde baixar.")
        try:
            r2 = requests.get(localizacao, timeout=60)  # sem Authorization aqui, de propósito — ver docstring
        except requests.RequestException as e:
            raise InstaladorIndisponivelError(f"Falha ao baixar o arquivo: {e}") from e
        if r2.status_code != 200:
            raise InstaladorIndisponivelError(f"Download falhou (HTTP {r2.status_code}).")
        return r2.content, r2.headers.get("Content-Type", "application/octet-stream")

    if r1.status_code == 200:
        return r1.content, r1.headers.get("Content-Type", "application/octet-stream")

    raise InstaladorIndisponivelError(f"O GitHub respondeu de forma inesperada ao baixar (HTTP {r1.status_code}).")
