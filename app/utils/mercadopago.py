"""
Integração com o Mercado Pago (Checkout Pro / API de Preferências) para
cobrança das licenças (seção de licenciamento do SaaS).

Usa a API REST diretamente (sem SDK) via `requests`, autenticada com
MERCADOPAGO_ACCESS_TOKEN. Documentação oficial:
https://www.mercadopago.com.br/developers/pt/reference

⚠️ Não foi possível testar uma chamada real de dentro do ambiente de
geração de código (sem acesso de rede à API do Mercado Pago), mas o
formato de request/response segue exatamente a documentação oficial da
API de Preferências e de Pagamentos. Teste a primeira cobrança em modo
sandbox (com credenciais de teste do Mercado Pago) antes de usar em
produção, para confirmar que sua conta/integração está correta.
"""
import requests
from flask import current_app

API_BASE = "https://api.mercadopago.com"


def _headers():
    token = current_app.config.get("MERCADOPAGO_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN não configurado no ambiente.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def criar_preferencia_pagamento(*, titulo, valor, referencia_externa, email_pagador,
                                 url_sucesso, url_falha, url_pendente, url_notificacao):
    """
    Cria uma preferência de checkout (Checkout Pro). Devolve o dict de
    resposta da API do Mercado Pago (inclui `id` da preferência e
    `init_point`/`sandbox_init_point`, a URL para redirecionar o usuário).
    """
    payload = {
        "items": [{
            "title": titulo,
            "quantity": 1,
            "unit_price": float(valor),
            "currency_id": "BRL",
        }],
        "payer": {"email": email_pagador} if email_pagador else None,
        "external_reference": referencia_externa,
        "back_urls": {
            "success": url_sucesso,
            "failure": url_falha,
            "pending": url_pendente,
        },
        "auto_return": "approved",
        "notification_url": url_notificacao,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    resposta = requests.post(f"{API_BASE}/checkout/preferences", json=payload, headers=_headers(), timeout=15)
    resposta.raise_for_status()
    return resposta.json()


def consultar_pagamento(payment_id):
    """Consulta os detalhes de um pagamento pelo ID (usado ao receber o webhook)."""
    resposta = requests.get(f"{API_BASE}/v1/payments/{payment_id}", headers=_headers(), timeout=15)
    resposta.raise_for_status()
    return resposta.json()
