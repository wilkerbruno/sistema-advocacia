"""
Cliente HTTP simples pra falar com o backend do JusControl
(app/routes/agente_local_api.py) — autenticado por Bearer token (o
pareamento gerado em "Meu agente local"). Só troca RESULTADOS (PDF,
status), nunca o certificado nem qualquer credencial de tribunal.

Classe (não funções soltas) de propósito: tanto `main.py` (modo
desenvolvedor, lê config de variáveis de ambiente/.env) quanto
`tray_app.py` (modo instalado, lê config de `config_store.py`, editável
pela janela de configuração) precisam de um cliente apontando pro
MESMO servidor/token, mas vêm de fontes de configuração diferentes —
uma classe permite instanciar um `ClienteJusControl` a partir de
qualquer uma das duas sem duplicar a lógica HTTP.
"""
import requests


class ErroApiJusControl(Exception):
    pass


class ErroTokenInvalido(ErroApiJusControl):
    """Token de pareamento rejeitado (HTTP 401) — usado especificamente
    por `autenticador_local.py` pra distinguir "token inválido/revogado"
    (o único jeito de corrigir é reabrir esta mesma tela) de "sem
    conexão" (ErroApiJusControl genérico) — ver docstring lá."""
    pass


class ErroAutenticadorBloqueado(ErroApiJusControl):
    """Trava de tentativas do autenticador (HTTP 429) — ver
    AgenteLocalPareado.registrar_falha_totp no servidor."""
    pass


class ClienteJusControl:
    def __init__(self, url_base, token):
        self.url_base = (url_base or "").rstrip("/")
        self.token = token

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _url(self, caminho):
        return f"{self.url_base}/api/agente-local{caminho}"

    def ping(self):
        r = requests.get(self._url("/ping"), headers=self._headers(), timeout=20)
        if r.status_code != 200:
            raise ErroApiJusControl(
                f"Token de pareamento rejeitado pelo servidor (HTTP {r.status_code}) — "
                "confira o token e o endereço do JusControl na tela de configuração, ou "
                "gere um token novo em \"Meu agente local\"."
            )
        return r.json()

    def listar_tarefas_pendentes(self):
        r = requests.get(self._url("/tarefas"), headers=self._headers(), timeout=30)
        r.raise_for_status()
        return r.json()["tarefas"]

    def marcar_iniciada(self, tarefa_id):
        r = requests.post(self._url(f"/tarefas/{tarefa_id}/iniciar"), headers=self._headers(), timeout=20)
        r.raise_for_status()

    def enviar_resultado(self, tarefa_id, caminho_pdf):
        with open(caminho_pdf, "rb") as f:
            arquivos = {"arquivo": (f"autos_completos_{tarefa_id}.pdf", f, "application/pdf")}
            r = requests.post(self._url(f"/tarefas/{tarefa_id}/resultado"), headers=self._headers(),
                               files=arquivos, timeout=120)
        if r.status_code != 200:
            raise ErroApiJusControl(f"Servidor recusou o resultado (HTTP {r.status_code}): {r.text}")
        return r.json()

    def reportar_erro(self, tarefa_id, mensagem):
        r = requests.post(self._url(f"/tarefas/{tarefa_id}/erro"), headers=self._headers(),
                           json={"mensagem": mensagem}, timeout=20)
        r.raise_for_status()

    def status_autenticador(self):
        """Devolve {"exigido": bool} — se a Configuração deste agente
        precisa pedir um código do autenticador antes de abrir (ver
        autenticador_local.py)."""
        try:
            r = requests.get(self._url("/status-autenticador"), headers=self._headers(), timeout=20)
        except requests.exceptions.RequestException as e:
            raise ErroApiJusControl(f"Sem conexão com o JusControl: {e}") from e

        if r.status_code == 401:
            # Sempre 401 de `exige_agente` (o token em si é ruim) — este
            # endpoint não tem outro jeito de devolver 401.
            raise ErroTokenInvalido("Token de pareamento inválido ou revogado.")
        if r.status_code != 200:
            raise ErroApiJusControl(f"Não foi possível confirmar o autenticador (HTTP {r.status_code}).")
        return r.json()

    def verificar_autenticador(self, codigo):
        """Confere `codigo` contra o autenticador da conta JusControl
        deste usuário. Devolve True/False; levanta ErroTokenInvalido (401
        de `exige_agente` — o TOKEN DE PAREAMENTO em si é que é ruim, não
        o código digitado), ErroAutenticadorBloqueado (429, muitas
        tentativas erradas) ou ErroApiJusControl (qualquer outra falha,
        inclusive sem conexão). Um 401 de CÓDIGO errado (a rota
        `verificar_autenticador` no servidor) sempre vem com um corpo
        JSON contendo a chave "ok" — é assim que se distingue dos 401 de
        token ruim (corpo só com "erro", sem "ok"), sem depender do texto
        da mensagem."""
        try:
            r = requests.post(self._url("/verificar-autenticador"), headers=self._headers(),
                               json={"codigo": codigo}, timeout=20)
        except requests.exceptions.RequestException as e:
            raise ErroApiJusControl(f"Sem conexão com o JusControl: {e}") from e

        if r.status_code == 429:
            corpo = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
            raise ErroAutenticadorBloqueado(corpo.get("erro") or "Bloqueado temporariamente — tente mais tarde.")

        corpo = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
        if r.status_code == 401 and "ok" not in corpo:
            raise ErroTokenInvalido("Token de pareamento inválido ou revogado.")
        if r.status_code not in (200, 401):
            raise ErroApiJusControl(f"Servidor recusou a checagem do autenticador (HTTP {r.status_code}).")
        return bool(corpo.get("ok"))
