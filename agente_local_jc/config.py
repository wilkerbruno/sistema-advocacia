"""
Configuração do Agente Local — lida de variáveis de ambiente (ou de um
arquivo `.env` na mesma pasta, se existir) para não obrigar ninguém a
editar código Python pra rodar o agente.

Nada aqui é secreto POR NATUREZA além do token de pareamento e da senha
do certificado — mesmo assim, nunca commite um `.env` de verdade no
Git (o `.env.exemplo` ao lado serve só de modelo).
"""
import os

_AQUI = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_AQUI, ".env")

if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            os.environ.setdefault(chave.strip(), valor.strip())


# URL base do servidor JusControl (sem barra no final), ex:
# "https://minhaempresa.jus-control.com.br"
JUSCONTROL_URL = os.environ.get("JUSCONTROL_URL", "").rstrip("/")

# Token de pareamento gerado em "Meu agente local" dentro do JusControl
# (ver app/routes/agente_local.py) — só aparece uma vez na tela, cole
# aqui ou no .env, nunca em outro lugar.
TOKEN_PAREAMENTO = os.environ.get("JUSCONTROL_AGENTE_TOKEN", "")

# Segundos entre cada verificação de tarefas pendentes.
INTERVALO_POLLING_SEGUNDOS = int(os.environ.get("INTERVALO_POLLING_SEGUNDOS", "60"))

# Caminho do arquivo do certificado A1 (.pfx/.p12) e a senha dele — ver
# certificado.py. NUNCA são enviados pra lugar nenhum, só usados aqui,
# na própria máquina, para autenticar direto no tribunal.
CERTIFICADO_PFX_CAMINHO = os.environ.get("CERTIFICADO_PFX_CAMINHO", "")
CERTIFICADO_PFX_SENHA = os.environ.get("CERTIFICADO_PFX_SENHA", "")

# Configuração do tribunal para o conector "pje_mni" (piloto — ver
# conectores/pje_mni.py). Como é só um piloto de UM tribunal por vez,
# fica em variáveis simples; quando mais de um tribunal PJe entrar em
# produção de verdade, isso vira uma lista (ver registro_conectores.py).
PJE_TRIBUNAL = os.environ.get("PJE_TRIBUNAL", "")          # ex: "trt2", "tjrj"
PJE_INSTANCIA = os.environ.get("PJE_INSTANCIA", "1g")
PJE_ID_CONSULTANTE = os.environ.get("PJE_ID_CONSULTANTE", "")
PJE_SENHA_CONSULTANTE = os.environ.get("PJE_SENHA_CONSULTANTE", "")
# Se preenchido, tem prioridade sobre PJE_TRIBUNAL/PJE_INSTANCIA — use
# depois de CONFIRMAR o WSDL de verdade do tribunal (ver aviso no topo
# de conectores/pje_mni.py).
PJE_URL_WSDL = os.environ.get("PJE_URL_WSDL", "")

CONFIG_CONECTORES = {
    "pje_mni": {
        "tribunal": PJE_TRIBUNAL,
        "instancia": PJE_INSTANCIA,
        "id_consultante": PJE_ID_CONSULTANTE,
        "senha_consultante": PJE_SENHA_CONSULTANTE,
        "url_wsdl": PJE_URL_WSDL or None,
    },
}


def validar_configuracao_minima():
    faltando = []
    if not JUSCONTROL_URL:
        faltando.append("JUSCONTROL_URL")
    if not TOKEN_PAREAMENTO:
        faltando.append("JUSCONTROL_AGENTE_TOKEN")
    if faltando:
        raise RuntimeError(
            "Configuração incompleta — defina no .env (ou como variável de ambiente): "
            + ", ".join(faltando)
        )
