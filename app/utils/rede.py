"""
Resolução de MAC address a partir do IP da requisição — best effort.

⚠️ Limitação fundamental (não é bug, é como a internet funciona): o
protocolo HTTP nunca transmite o MAC address do cliente para o servidor.
O único jeito de descobrir o MAC de quem fez a requisição é consultar a
tabela ARP do sistema operacional onde o Flask está rodando — e isso só
tem uma entrada válida quando o cliente está na MESMA rede local (mesmo
segmento L2) do servidor.

- No seu teste atual (servidor e usuários na rede 192.168.0.x), funciona.
- **No EasyPanel, não vai funcionar para nenhum usuário real.** O container
  roda em um datacenter, os usuários acessam pela internet (várias redes e
  roteadores no meio, sem falar que o tráfego ainda passa pelo proxy
  reverso do próprio EasyPanel antes de chegar na aplicação) — não existe
  tabela ARP com entrada para esses IPs. A coluna MAC na auditoria vai
  aparecer como "—" quase sempre em produção. Isso não é uma falha desta
  implementação: nenhum servidor na internet consegue descobrir o MAC de
  quem acessa de fora da própria rede local, de nenhuma forma. Se essa
  informação for realmente necessária, o único caminho é capturar o MAC no
  computador do usuário (ex: um agente instalado na máquina, ou script
  local) e ele mesmo enviar isso para o sistema — o que é uma mudança de
  abordagem bem maior, não um ajuste de configuração.

Por isso a auditoria também passou a registrar, junto com o MAC (mantido
como informação best-effort), dois dados que funcionam de verdade pela
internet: o User-Agent (via `resumir_user_agent` abaixo) e um
`dispositivo_id` — um identificador aleatório salvo em cookie de 1ª parte
no primeiro acesso de cada navegador (ver `app/__init__.py`), que permite
correlacionar as ações do mesmo dispositivo/navegador ao longo do tempo
mesmo quando o IP muda.
"""
import re
import subprocess


def obter_mac_por_ip(ip: str) -> str | None:
    """
    Consulta a tabela ARP do sistema operacional em busca do MAC associado
    ao IP informado. Devolve None se não encontrar (rede diferente, ARP
    ainda não populado, ou ambiente sem acesso à tabela ARP).
    """
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return None

    # Linux: tenta primeiro /proc/net/arp (não depende de nenhum binário externo)
    try:
        with open("/proc/net/arp") as f:
            linhas = f.readlines()[1:]
        for linha in linhas:
            campos = linha.split()
            if len(campos) >= 4 and campos[0] == ip:
                mac = campos[3]
                if mac and mac != "00:00:00:00:00:00":
                    return mac.upper()
    except (FileNotFoundError, PermissionError, OSError):
        pass

    # Fallback: comando "ip neigh" (mais comum em distros modernas) ou "arp -n"
    for comando in (["ip", "neigh", "show", ip], ["arp", "-n", ip]):
        try:
            resultado = subprocess.run(comando, capture_output=True, text=True, timeout=2)
            match = re.search(r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})", resultado.stdout)
            if match:
                return match.group(1).upper()
        except (FileNotFoundError, subprocess.SubprocessError):
            continue

    return None


def resumir_user_agent(user_agent: str) -> str | None:
    """
    Resumo legível de navegador/SO a partir do cabeçalho User-Agent — ao
    contrário do MAC, isso SEMPRE chega ao servidor, mesmo com o cliente
    acessando pela internet (é o próprio navegador que informa, não depende
    de rede local nem de proxy). Não é impressão digital de dispositivo,
    é só leitura amigável pra tabela de auditoria; combine com
    `dispositivo_id` (cookie de 1ª parte) pra correlacionar ações do mesmo
    navegador ao longo do tempo, mesmo com o IP variando.
    """
    if not user_agent:
        return None
    ua = user_agent

    if "Edg/" in ua:
        navegador = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        navegador = "Opera"
    elif "Firefox/" in ua:
        navegador = "Firefox"
    elif "Chrome/" in ua and "Chromium" not in ua:
        navegador = "Chrome"
    elif "Safari/" in ua and "Chrome/" not in ua:
        navegador = "Safari"
    else:
        navegador = "navegador não identificado"

    if "Windows" in ua:
        sistema = "Windows"
    elif "iPhone" in ua:
        sistema = "iPhone"
    elif "iPad" in ua:
        sistema = "iPad"
    elif "Mac OS X" in ua:
        sistema = "macOS"
    elif "Android" in ua:
        sistema = "Android"
    elif "Linux" in ua:
        sistema = "Linux"
    else:
        sistema = "SO não identificado"

    return f"{navegador} / {sistema}"
