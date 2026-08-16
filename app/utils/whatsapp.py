"""
Envio de mensagem via WhatsApp usando automação NÃO-OFICIAL — decisão
explícita do dono do sistema (não é a API oficial da Meta), ciente do
risco real de o número usado ser banido por comportamento automatizado
(viola os Termos de Serviço do WhatsApp). Ver PENDENCIAS.md, seção -4,
para o comparativo das 3 opções e o motivo da escolha, e
whatsapp-bridge/server.js para as recomendações práticas de uso (número
dedicado, não usar o WhatsApp pessoal de ninguém, não mandar volume alto).

Funciona chamando um serviço SEPARADO (`whatsapp-bridge/`, Node.js +
whatsapp-web.js) que mantém uma sessão comum de WhatsApp Web logada (via
QR code, escaneado uma vez no celular do número escolhido pelo
escritório). Este módulo só faz uma chamada HTTP simples pra esse
serviço — nenhuma lógica de WhatsApp em si vive no lado Python.

Como todo canal opcional deste sistema (mesmo padrão de DATAJUD_API_KEY,
SMTP_* etc.): sem `WHATSAPP_BRIDGE_URL` configurada, `enviar_whatsapp()`
só devolve False — nunca derruba o job de lembretes por causa disso, e o
lembrete continua saindo normalmente por notificação no sistema e e-mail.
"""
import requests
from flask import current_app


def whatsapp_configurado():
    return bool(current_app.config.get("WHATSAPP_BRIDGE_URL"))


def enviar_whatsapp(numero: str, mensagem: str) -> bool:
    if not whatsapp_configurado() or not numero:
        return False

    url = current_app.config["WHATSAPP_BRIDGE_URL"].rstrip("/") + "/enviar"
    token = current_app.config.get("WHATSAPP_BRIDGE_TOKEN")
    headers = {"X-Bridge-Token": token} if token else {}

    try:
        resposta = requests.post(
            url, json={"numero": numero, "mensagem": mensagem}, headers=headers, timeout=15
        )
    except requests.RequestException as e:
        current_app.logger.warning(f"Falha de conexão com o bridge de WhatsApp: {e}")
        return False

    if resposta.status_code != 200:
        current_app.logger.warning(
            f"Bridge de WhatsApp respondeu {resposta.status_code}: {resposta.text[:200]}"
        )
        return False

    return True
