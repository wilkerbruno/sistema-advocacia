"""
Onde o Agente Local guarda a configuração quando instalado pelo
instalador (.exe) — diferente de `config.py` (o jeito "modo
desenvolvedor", lendo de variáveis de ambiente/`.env`, usado por quem
roda `python main.py` direto do código-fonte, sem instalar nada).

Fica em `%APPDATA%\\JusControlAgente\\config.json` no Windows (ou
`~/.jus_control_agente/config.json` em outro sistema, só pra permitir
testar isso fora do Windows) — nunca do lado do próprio `.exe`, porque a
pasta de instalação pode não ter permissão de escrita pra um usuário
comum (nem deveria: são dados de UM usuário, não do programa).

Nada aqui é enviado pra lugar nenhum — fica só nesta máquina.
"""
import json
import os
import sys
import stat


def _pasta_config():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        pasta = os.path.join(base, "JusControlAgente")
    else:
        pasta = os.path.join(os.path.expanduser("~"), ".jus_control_agente")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def caminho_config():
    return os.path.join(_pasta_config(), "config.json")


def caminho_log():
    """Arquivo de log do agente — útil pro advogado mandar print/conteúdo
    pra quem administra o sistema quando algo der errado, sem precisar
    abrir terminal nenhum."""
    return os.path.join(_pasta_config(), "agente.log")


CAMPOS_PADRAO = {
    "juscontrol_url": "",
    "token_pareamento": "",
    "intervalo_polling_segundos": 60,
    "certificado_pfx_caminho": "",
    "certificado_pfx_senha": "",
    "pje_tribunal": "",
    "pje_instancia": "1g",
    "pje_id_consultante": "",
    "pje_senha_consultante": "",
    "pje_url_wsdl": "",
    "projudi_id_consultante": "",
    "projudi_senha_consultante": "",
    "projudi_url_wsdl": "",
    "esaj_id_consultante": "",
    "esaj_senha_consultante": "",
    "esaj_url_wsdl": "",
    "iniciar_com_windows": True,
}


def carregar():
    """Devolve o dict salvo, com qualquer campo faltando preenchido pelo
    padrão (pra nunca quebrar se uma versão nova adicionar um campo)."""
    caminho = caminho_config()
    dados = dict(CAMPOS_PADRAO)
    if os.path.exists(caminho):
        try:
            with open(caminho, encoding="utf-8") as f:
                dados.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return dados


def salvar(dados):
    completo = dict(CAMPOS_PADRAO)
    completo.update(dados)
    caminho = caminho_config()
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(completo, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(caminho, stat.S_IRUSR | stat.S_IWUSR)  # só o próprio usuário lê/escreve
    except OSError:
        pass
    return completo


def configuracao_minima_completa(dados):
    return bool(dados.get("juscontrol_url")) and bool(dados.get("token_pareamento"))
