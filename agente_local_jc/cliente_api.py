"""
Cliente HTTP simples para falar com o backend do JusControl
(app/routes/agente_local_api.py) — autenticado por Bearer token (o
pareamento gerado em "Meu agente local"). Só troca RESULTADOS (PDF,
status), nunca o certificado nem qualquer credencial de tribunal.
"""
import requests

import config


class ErroApiJusControl(Exception):
    pass


def _headers():
    return {"Authorization": f"Bearer {config.TOKEN_PAREAMENTO}"}


def _url(caminho):
    return f"{config.JUSCONTROL_URL}/api/agente-local{caminho}"


def ping():
    r = requests.get(_url("/ping"), headers=_headers(), timeout=20)
    if r.status_code != 200:
        raise ErroApiJusControl(f"Token de pareamento rejeitado pelo servidor (HTTP {r.status_code}) — "
                                 "confira JUSCONTROL_AGENTE_TOKEN no .env, ou gere um novo em 'Meu agente local'.")
    return r.json()


def listar_tarefas_pendentes():
    r = requests.get(_url("/tarefas"), headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["tarefas"]


def marcar_iniciada(tarefa_id):
    r = requests.post(_url(f"/tarefas/{tarefa_id}/iniciar"), headers=_headers(), timeout=20)
    r.raise_for_status()


def enviar_resultado(tarefa_id, caminho_pdf):
    with open(caminho_pdf, "rb") as f:
        arquivos = {"arquivo": (f"autos_completos_{tarefa_id}.pdf", f, "application/pdf")}
        r = requests.post(_url(f"/tarefas/{tarefa_id}/resultado"), headers=_headers(),
                           files=arquivos, timeout=120)
    if r.status_code != 200:
        raise ErroApiJusControl(f"Servidor recusou o resultado (HTTP {r.status_code}): {r.text}")
    return r.json()


def reportar_erro(tarefa_id, mensagem):
    r = requests.post(_url(f"/tarefas/{tarefa_id}/erro"), headers=_headers(),
                       json={"mensagem": mensagem}, timeout=20)
    r.raise_for_status()
