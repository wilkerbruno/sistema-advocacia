#!/bin/sh
# Ponto de entrada do container: inicia o cron (recaptura diária via
# DataJud, ver docker/capturar-movimentacoes.cron), o Redis e o worker da
# fila de IA em segundo plano (ver app/utils/fila.py, app/jobs/ia_jobs.py e
# PENDENCIAS.md, seção -32) e depois entrega o processo principal
# (gunicorn) pro comando recebido — é ele quem fica em foreground e recebe
# os sinais do Docker (SIGTERM no stop/redeploy), não os processos de
# segundo plano abaixo.
set -e

# O cron, por padrão, roda os jobs com um ambiente quase vazio — não herda
# as variáveis de ambiente que o EasyPanel injeta no container (DATABASE_URL,
# DATAJUD_API_KEY, SECRET_KEY etc.). Sem isso, capturar_movimentacoes.py
# rodaria sem saber nem como conectar no banco. Por isso, gravamos as
# variáveis atuais em /etc/environment, que o cron lê antes de cada job.
printenv | grep -v "no_proxy" >> /etc/environment

cron

# Redis só escuta em localhost (127.0.0.1) — não precisa e não deve ficar
# acessível fora do container, é só a fila interna de IA deste app (ver
# app/utils/fila.py). Roda dentro do mesmo container de propósito, pra não
# exigir configurar um serviço novo no EasyPanel (ver PENDENCIAS.md, seção
# -32) — limitação consciente: se o container reiniciar, um job que
# estivesse na fila/em processamento naquele momento se perde (a mensagem
# ou análise fica "processando" pra sempre, sem re-tentar sozinha), mas
# gerar de novo é só clicar no botão outra vez.
redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --save "" --appendonly no

# Espera o Redis responder antes de subir o worker (evita o worker morrer
# de cara tentando conectar num Redis que ainda não terminou de subir).
tentativas=0
until redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; do
    tentativas=$((tentativas + 1))
    if [ "$tentativas" -ge 20 ]; then
        echo "AVISO: Redis não respondeu após 10s — a fila de IA pode não funcionar." >&2
        break
    fi
    sleep 0.5
done

# Worker que consome a fila de IA (ver app/jobs/ia_jobs.py) — processo
# Python separado do gunicorn, é ele quem de fato carrega o modelo de IA
# local na memória (não os workers do gunicorn, que nunca chamam o modelo
# diretamente mais — ver PENDENCIAS.md, seção -32).
#
# --worker-class rq.worker.SimpleWorker: por padrão, o RQ cria um processo
# FILHO NOVO ("work horse") pra cada job e descarta esse processo assim que
# o job termina — isso isolaria bem um job travado do resto, mas também
# jogaria fora, a cada mensagem, o modelo de IA que tinha acabado de ser
# carregado na memória (ver app/utils/ia_local.py: o cache do modelo é uma
# variável do processo Python, só sobrevive enquanto o processo continua de
# pé). Sem SimpleWorker, TODA mensagem pagaria o custo de recarregar ~1,1 GB
# do disco pra RAM de novo — o carregamento "só na primeira mensagem" que
# app/utils/ia_local.py foi desenhado pra fazer deixaria de existir na
# prática. Com SimpleWorker, os jobs rodam dentro do próprio processo do
# worker (sem fork), então o modelo carrega uma vez e fica na memória
# enquanto este worker continuar de pé — exatamente o comportamento
# pretendido.
#
# Roda em segundo plano, como o cron acima; se cair por algum motivo,
# reinicia sozinho até 3 vezes seguidas (proteção simples contra um
# travamento pontual, sem precisar de um supervisor de processos completo).
(
    tentativas_worker=0
    while [ "$tentativas_worker" -lt 3 ]; do
        rq worker jus_control_ia --worker-class rq.worker.SimpleWorker \
            --url "${REDIS_URL:-redis://127.0.0.1:6379/0}" || true
        tentativas_worker=$((tentativas_worker + 1))
        echo "AVISO: worker da fila de IA parou (tentativa $tentativas_worker/3) — reiniciando em 3s." >&2
        sleep 3
    done
    echo "ERRO: worker da fila de IA parou 3 vezes seguidas — desistindo de reiniciar. Geração de IA vai ficar presa em 'processando' até o próximo redeploy/restart do serviço." >&2
) &

exec "$@"
