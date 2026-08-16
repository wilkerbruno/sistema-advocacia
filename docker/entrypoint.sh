#!/bin/sh
# Ponto de entrada do container: inicia o cron (recaptura diária via
# DataJud, ver docker/capturar-movimentacoes.cron) em segundo plano e
# depois entrega o processo principal (gunicorn) pro comando recebido —
# é ele quem fica em foreground e recebe os sinais do Docker (SIGTERM no
# stop/redeploy), não o cron.
set -e

# O cron, por padrão, roda os jobs com um ambiente quase vazio — não herda
# as variáveis de ambiente que o EasyPanel injeta no container (DATABASE_URL,
# DATAJUD_API_KEY, SECRET_KEY etc.). Sem isso, capturar_movimentacoes.py
# rodaria sem saber nem como conectar no banco. Por isso, gravamos as
# variáveis atuais em /etc/environment, que o cron lê antes de cada job.
printenv | grep -v "no_proxy" >> /etc/environment

cron

exec "$@"
