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

⚠️ MULTI-SESSÃO (uma empresa, um número): a pedido explícito ("cada
empresa cadastrasse um whatsapp para enviar essas mensagens [...] as
empresas não vão ter acesso a esse whatsapp pra responder dúvidas dos
clientes"), o WAHA continua sendo UM ÚNICO servidor compartilhado (mesma
WHATSAPP_BRIDGE_URL/TOKEN de sempre no .env), mas cada empresa conecta o
PRÓPRIO número numa SESSÃO própria (WAHA "Sessions API") em vez de todo
mundo usar a sessão "default". Confirmei antes de implementar: desde a
versão 2026.6.1 o WAHA Core (grátis) já suporta sessões ilimitadas — não
precisa de licença paga nem de servidor extra por empresa. Cada empresa
cliente conecta o próprio número escaneando um QR code em "Minhas
Integrações" (app/routes/integracoes.py); a plataforma (empresa
`dono_da_plataforma`) continua usando a sessão "default", a mesma
conectada manualmente antes desta funcionalidade existir.

Funções de ENVIO (`enviar_whatsapp`, `_resolver_chat_id`) recebem a
sessão como parâmetro — quem chama decide qual número usar (ver
`Empresa.whatsapp_sessao_efetiva`, em app/models/empresa.py). Funções de
GERENCIAMENTO DE SESSÃO (`conectar_sessao`, `status_sessao`,
`qr_sessao_png`, `desconectar_sessao`) são as usadas pela tela de
"Minhas Integrações" pra criar/mostrar/derrubar a sessão de cada empresa.

⚠️ Não foi possível testar uma chamada real de gerenciamento de sessão
(criar sessão, pegar QR) contra um servidor WAHA de verdade a partir do
ambiente onde gerei este código (sem acesso de rede a partir daqui) — só
testei a lógica de rotas/estado com chamadas simuladas. Teste o fluxo
completo (conectar uma empresa de teste, escanear o QR, confirmar que a
mensagem sai do número certo) depois do deploy.
"""
import requests
from flask import current_app

NOME_SESSAO_PADRAO = "default"  # sessão histórica da própria plataforma — ver Empresa.whatsapp_sessao_efetiva


class SessaoWhatsAppError(Exception):
    """Erro amigável de qualquer operação de gerenciamento de sessão do
    WAHA (criar, consultar status, obter QR, desconectar) — nunca deixa o
    erro cru da API vazar pra tela do usuário."""


def whatsapp_configurado():
    return bool(current_app.config.get("WHATSAPP_BRIDGE_URL"))


def _base_url():
    return current_app.config["WHATSAPP_BRIDGE_URL"].rstrip("/")


def _headers():
    token = current_app.config.get("WHATSAPP_BRIDGE_TOKEN")
    return {"X-Api-Key": token} if token else {}


# ==================== Envio de mensagem ====================

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


def _resolver_chat_id(digitos: str, sessao: str) -> str | None:
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
    url = _base_url() + "/api/contacts/check-exists"

    try:
        resposta = requests.get(url, params={"phone": digitos, "session": sessao},
                                 headers=_headers(), timeout=15)
        resposta.raise_for_status()
        dados = resposta.json()
    except (requests.RequestException, ValueError) as e:
        current_app.logger.warning(f"Falha ao consultar número {digitos} no WAHA (WhatsApp, sessão {sessao}): {e}")
        return None

    if not dados.get("numberExists"):
        current_app.logger.info(f"Número {digitos} não está registrado no WhatsApp (segundo o WAHA).")
        return None

    return dados.get("chatId")


def enviar_whatsapp(numero: str, mensagem: str, sessao: str = NOME_SESSAO_PADRAO) -> bool:
    """
    `sessao`: nome da sessão do WAHA a usar pra enviar — cada empresa tem a
    própria (ver Empresa.whatsapp_sessao_efetiva). Sem sessão (None/vazio,
    ex: empresa que ainda não conectou nenhum número), devolve False sem
    tentar nada — nunca cai pra outra sessão "por padrão" (isso seria
    mandar a mensagem pelo número de OUTRA empresa).
    """
    if not whatsapp_configurado() or not numero or not sessao:
        return False

    chat_id = _resolver_chat_id(_numero_para_digitos(numero), sessao)
    if not chat_id:
        return False

    url = _base_url() + "/api/sendText"
    corpo = {
        "session": sessao,
        "chatId": chat_id,
        "text": mensagem,
    }

    try:
        resposta = requests.post(url, json=corpo, headers=_headers(), timeout=15)
    except requests.RequestException as e:
        current_app.logger.warning(f"Falha de conexão com o WAHA (WhatsApp, sessão {sessao}): {e}")
        return False

    if resposta.status_code not in (200, 201):
        current_app.logger.warning(
            f"WAHA respondeu {resposta.status_code} ao enviar WhatsApp (sessão {sessao}): {resposta.text[:300]}"
        )
        return False

    return True


# ==================== Gerenciamento de sessão (uma por empresa) ====================

def status_sessao(nome_sessao: str):
    """
    Devolve (status, dados) — `status` é um dos valores do WAHA (STOPPED,
    STARTING, SCAN_QR_CODE, WORKING, FAILED, ...), "NAO_ENCONTRADA" se essa
    sessão não existe (mais) no servidor WAHA, ou "NAO_CONFIGURADA" se
    `nome_sessao` for vazio ou `WHATSAPP_BRIDGE_URL` não estiver definida.
    `dados` é o corpo JSON completo devolvido pelo WAHA (pode ter o campo
    "me" com o número conectado quando status == WORKING).
    """
    if not nome_sessao or not whatsapp_configurado():
        return "NAO_CONFIGURADA", {}

    url = f"{_base_url()}/api/sessions/{nome_sessao}"
    try:
        resposta = requests.get(url, headers=_headers(), timeout=15)
    except requests.RequestException as e:
        raise SessaoWhatsAppError(f"Falha de conexão com o WAHA: {e}") from e

    if resposta.status_code == 404:
        return "NAO_ENCONTRADA", {}
    if resposta.status_code != 200:
        raise SessaoWhatsAppError(f"WAHA respondeu {resposta.status_code} ao consultar a sessão: {resposta.text[:300]}")

    dados = resposta.json()
    return dados.get("status", "DESCONHECIDO"), dados


def conectar_sessao(nome_sessao: str):
    """
    Garante que a sessão existe e está iniciada — cria do zero se o WAHA
    não a conhece (primeira conexão dessa empresa, ou sessão que foi
    apagada manualmente no servidor), ou só reinicia se já existir mas
    estiver parada/com erro. Idempotente: pode chamar de novo sem problema
    (ex: usuário atualizou a página e clicou "Conectar" outra vez) — nesse
    caso o WAHA simplesmente continua mostrando o QR code atual.
    """
    if not whatsapp_configurado():
        raise SessaoWhatsAppError("WHATSAPP_BRIDGE_URL não configurada no servidor.")

    status, _ = status_sessao(nome_sessao)

    if status == "NAO_ENCONTRADA":
        url = f"{_base_url()}/api/sessions"
        try:
            resposta = requests.post(url, json={"name": nome_sessao, "start": True},
                                      headers=_headers(), timeout=20)
        except requests.RequestException as e:
            raise SessaoWhatsAppError(f"Falha de conexão com o WAHA: {e}") from e
        if resposta.status_code not in (200, 201):
            raise SessaoWhatsAppError(f"WAHA respondeu {resposta.status_code} ao criar a sessão: {resposta.text[:300]}")
        return

    if status in ("STOPPED", "FAILED"):
        url = f"{_base_url()}/api/sessions/{nome_sessao}/start"
        try:
            resposta = requests.post(url, headers=_headers(), timeout=20)
        except requests.RequestException as e:
            raise SessaoWhatsAppError(f"Falha de conexão com o WAHA: {e}") from e
        if resposta.status_code not in (200, 201):
            raise SessaoWhatsAppError(f"WAHA respondeu {resposta.status_code} ao iniciar a sessão: {resposta.text[:300]}")
        return

    # STARTING / SCAN_QR_CODE / WORKING: já está a caminho ou pronta, nada a fazer.


def qr_sessao_png(nome_sessao: str):
    """Devolve os bytes PNG do QR code atual da sessão, ou None se a
    sessão não estiver esperando QR (ou se a chamada falhar) — quem chama
    (app/routes/integracoes.py) decide o que mostrar nesse caso."""
    if not nome_sessao or not whatsapp_configurado():
        return None
    url = f"{_base_url()}/api/{nome_sessao}/auth/qr"
    try:
        resposta = requests.get(url, headers=_headers(), timeout=15)
    except requests.RequestException:
        return None
    if resposta.status_code != 200:
        return None
    return resposta.content


def desconectar_sessao(nome_sessao: str):
    """Desconecta e apaga a sessão no WAHA (logout do número + remove a
    configuração) — best-effort: se o WAHA já não tiver essa sessão (404)
    ou a chamada falhar, não levanta erro (o objetivo — a empresa não usar
    mais esse número — já está satisfeito de qualquer forma do lado do
    JusControl, que limpa `Empresa.whatsapp_sessao` de qualquer jeito)."""
    if not nome_sessao or not whatsapp_configurado():
        return
    url = f"{_base_url()}/api/sessions/{nome_sessao}"
    try:
        requests.delete(url, headers=_headers(), timeout=20)
    except requests.RequestException as e:
        current_app.logger.warning(f"Falha ao desconectar sessão '{nome_sessao}' no WAHA: {e}")
