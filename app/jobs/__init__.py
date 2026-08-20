"""
Funções que rodam no processo separado do worker do RQ (ver
app/utils/fila.py e docker/entrypoint.sh) — NUNCA no processo do gunicorn
que atende requisições web. Por isso cada função aqui monta sua própria
`app` e seu próprio `app.app_context()` (mesmo padrão já usado em scripts
como capturar_movimentacoes.py) em vez de depender de current_user ou de
qualquer estado deixado pela requisição que enfileirou o job — o worker é
um processo Python totalmente separado, sem login de sessão, sem `g`, sem
nada disso.
"""
