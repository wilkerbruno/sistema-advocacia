"""
Envio de mensagem via WhatsApp usando o WAHA (WhatsApp HTTP API,
https://waha.devlike.pro) — automação NÃO-OFICIAL (não é a API oficial da
Meta), decisão explícita do dono do sistema, ciente do risco real de o
número usado ser banido por comportamento automatizado (viola os Termos
de Serviço do WhatsApp). Ver PENDENCIAS.md, seção -4, para o comparativo
das 3 opções e o passo a passo completo de como subir o WAHA no EasyPanel.

Por que WAHA em vez de um serviço próprio: a primeira versão deste módulo
chamava um serviço Node.js escrito do zero (pasta whatsapp-bridge/, hoje
descontinuada — ver whatsapp-bridge/DEPRECATED.md). Troquei pelo WAHA
porque é um projeto open-source mantido especificamente para isso (painel
de conexão via QR code já pronto, chave de API já pronta, reconexão já
tratada) — mais confiável do que manter na mão a mesma coisa que esse
projeto já resolve.

Como todo canal opcional deste sistema (mesmo padrão de DATAJUD_API_KEY,
SMTP_* etc.): sem `WHATSAPP_BRIDGE_URL` configurada, `enviar_whatsapp()`
só devolve False — nunca derruba o job de lembretes por causa disso, e o
lembrete continua saindo normalmente por notificação no sistema e e-mail.
"""
import requests
from flask import current_app

NOME_SESSAO = "default"  # nome da sessão do WAHA — ver PENDENCIAS.md (é o mesmo nome usado ao escanear o QR no dashboard do WAHA)


def whatsapp_configurado():
    return bool(current_app.config.get("WHATSAPP_BRIDGE_URL"))


def _numero_para_chat_id(numero: str) -> str:
    """
    Normaliza um número de telefone brasileiro (como vem cadastrado em
    Cliente.whatsapp, ex: "(11) 98888-7777") para o formato exigido pelo
    WAHA: dígitos internacionais sem "+", seguido de "@c.us". Se o número
    já tiver o código do país (55), não duplica.
    """
    digitos = "".join(c for c in (numero or "") if c.isdigit())
    if len(digitos) in (10, 11):  # DDD + número, sem código do país
        digitos = "55" + digitos
    return f"{digitos}@c.us"


def enviar_whatsapp(numero: str, mensagem: str) -> bool:
    if not whatsapp_configurado() or not numero:
        return False

    url = current_app.config["WHATSAPP_BRIDGE_URL"].rstrip("/") + "/api/sendText"
    token = current_app.config.get("WHATSAPP_BRIDGE_TOKEN")
    headers = {"X-Api-Key": token} if token else {}

    corpo = {
        "session": NOME_SESSAO,
        "chatId": _numero_para_chat_id(numero),
        "text": mensagem,
    }

    try:
        resposta = requests.post(url, json=corpo, headers=headers, timeout=15)
    except requests.RequestException as e:
        current_app.logger.warning(f"Falha de conexão com o WAHA (WhatsApp): {e}")
        return False

    if resposta.status_code not in (200, 201):
        current_app.logger.warning(
            f"WAHA respondeu {resposta.status_code} ao enviar WhatsApp: {resposta.text[:300]}"
        )
        return False

    return True
