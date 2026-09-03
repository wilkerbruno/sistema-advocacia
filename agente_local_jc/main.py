"""
Agente Local do JusControl — modo DESENVOLVEDOR (terminal, lê config de
`.env`/variáveis de ambiente via `config.py`).

Pra rodar o agente com ícone na bandeja e início automático com o
Windows (o jeito pensado pro advogado final usar, distribuído como
instalador — ver README.md), use `tray_app.py`, não este arquivo. Este
aqui é o jeito rápido de testar um conector novo ou depurar algo sem
precisar montar o instalador inteiro de novo a cada mudança.

Uso:
  1. cd agente_local_jc
  2. python -m venv .venv && (.venv\\Scripts\\activate  OU  source .venv/bin/activate)
  3. pip install -r requirements.txt
  4. cp .env.exemplo .env   (e preencher com os valores reais)
  5. python main.py

⚠️ Ver README.md antes de rodar isto contra um processo real — o
conector "pje_mni" é um piloto NÃO testado contra nenhum tribunal.
"""
import sys
import time

import config
import motor
from cliente_api import ClienteJusControl, ErroApiJusControl


def _log(mensagem):
    print(f"[agente-local] {mensagem}", flush=True)


def loop_principal():
    config.validar_configuracao_minima()
    cliente = ClienteJusControl(config.JUSCONTROL_URL, config.TOKEN_PAREAMENTO)

    try:
        info = cliente.ping()
    except ErroApiJusControl as e:
        _log(str(e))
        sys.exit(1)
    _log(f"Pareado como {info.get('usuario')} — agente {info.get('apelido')!r}. "
         f"Verificando tarefas a cada {config.INTERVALO_POLLING_SEGUNDOS}s.")

    while True:
        motor.verificar_uma_vez(
            cliente, config.CERTIFICADO_PFX_CAMINHO, config.CERTIFICADO_PFX_SENHA,
            config.CONFIG_CONECTORES, _log,
        )
        time.sleep(config.INTERVALO_POLLING_SEGUNDOS)


if __name__ == "__main__":
    try:
        loop_principal()
    except KeyboardInterrupt:
        _log("Encerrado pelo usuário.")
