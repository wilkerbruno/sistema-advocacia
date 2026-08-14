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
