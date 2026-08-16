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


def _numero_para_digitos(numero: str) -> str:
    """
    Normaliza um número de telefone brasileiro (como vem cadastrado em
    Cliente.whatsapp/Usuario.whatsapp, ex: "(11) 98888-7777") para dígitos
    internacionais sem "+" (ex: "5511988887777"). Se o número já tiver o
    código do país (55), não duplica.

    Isso NÃO é o chatId final — ver _resolver_chat_id() logo abaixo.
    """
    digitos = "".join(c for c in (numero or "") if c.isdigit())
    if len(digitos) in (10, 11):  # DDD + número, sem código do país
        digitos = "55" + digitos
    return digitos


def _resolver_chat_id(digitos: str) -> str | None:
    """
    Consulta o WAHA (GET /api/contacts/check-exists) para descobrir o
    chatId REAL de um número, em vez de montar "<dígitos>@c.us" na mão.

    Isso é necessário por causa de uma inconsistência conhecida de números
    brasileiros: contas do WhatsApp criadas antes de 2012 fora de SP/RJ/ES
    mantêm o identificador interno SEM o 9º dígito, mesmo o número de
    telefone atual do cliente tendo o 9. Montar o chatId "no chute" nesses
    casos faz o WAHA responder com o erro "no LID found for ... from
    server" (foi exatamente o que aconteceu nos testes — ver
    PENDENCIAS.md, seção -4). Consultando o WAHA primeiro, ele devolve o
    chatId certo (às vezes no formato "@lid", às vezes "@c.us").

    Devolve None se o número não existir no WhatsApp ou se a consulta
    falhar — nesses casos o envio é cancelado (melhor não enviar do que
    enviar para o identificador errado).
    """
    url = current_app.config["WHATSAPP_BRIDGE_URL"].rstrip("/") + "/api/contacts/check-exists"
    token = current_app.config.get("WHATSAPP_BRIDGE_TOKEN")
    headers = {"X-Api-Key": token} if token else {}

    try:
        resposta = requests.get(url, params={"phone": digitos, "session": NOME_SESSAO},
                                 headers=headers, timeout=15)
        resposta.raise_for_status()
        dados = resposta.json()
    except (requests.RequestException, ValueError) as e:
        current_app.logger.warning(f"Falha ao consultar número {digitos} no WAHA (WhatsApp): {e}")
        return None

    if not dados.get("numberExists"):
        current_app.logger.info(f"Número {digitos} não está registrado no WhatsApp (segundo o WAHA).")
        return None

    return dados.get("chatId")


def enviar_whatsapp(numero: str, mensagem: str) -> bool:
    if not whatsapp_configurado() or not numero:
        return False

    chat_id = _resolver_chat_id(_numero_para_digitos(numero))
    if not chat_id:
        return False

    url = current_app.config["WHATSAPP_BRIDGE_URL"].rstrip("/") + "/api/sendText"
    token = current_app.config.get("WHATSAPP_BRIDGE_TOKEN")
    headers = {"X-Api-Key": token} if token else {}

    corpo = {
        "session": NOME_SESSAO,
        "chatId": chat_id,
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
