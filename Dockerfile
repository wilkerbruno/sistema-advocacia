FROM python:3.12-slim

WORKDIR /app

# g++ e cmake: ficam só como rede de segurança pro pip conseguir compilar
# o llama-cpp-python do zero SE um dia a versão travada em requirements.txt
# mudar e o wheel pré-compilado (ver linha do pip install abaixo) não
# acompanhar a mudança — não é mais o caminho principal (ver comentário
# abaixo). cron: roda a recaptura diária de movimentações via DataJud
# dentro do próprio container (ver docker/capturar-movimentacoes.cron e
# PENDENCIAS.md, seção -3) — não depende de nenhum recurso externo de
# agendamento.
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev pkg-config gcc g++ cmake cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --extra-index-url: o llama-cpp-python (motor do modelo de IA local, ver
# app/utils/ia_local.py) não tem wheel pré-compilada no PyPI normal pra
# esta combinação de SO/Python — sem isso, o pip compila o llama.cpp
# inteiro (C++) na hora do build, o que já travou o servidor de produção
# por esgotar toda a RAM disponível durante o build (ver PENDENCIAS.md,
# seção -29). Este índice extra (mantido pelo próprio autor do
# llama-cpp-python) publica uma wheel pré-compilada pra CPU — confirmei
# que existe uma build pra exatamente a versão presa em requirements.txt
# (llama_cpp_python-0.3.34-py3-none-manylinux2014_x86_64...whl) antes de
# aplicar esta mudança. Com isso, o pip só baixa o binário pronto, sem
# compilar nada — build muito mais rápido e sem risco de derrubar o
# servidor de novo. Se um dia atualizar a versão do llama-cpp-python no
# requirements.txt, confira antes se existe wheel pra essa versão nova em
# https://abetlen.github.io/llama-cpp-python/whl/cpu/llama-cpp-python/ —
# se não existir, o pip cai de volta pra compilar do zero (por isso
# gcc/g++/cmake continuam instalados acima, só como plano B).
RUN pip install --no-cache-dir --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu -r requirements.txt

# Agendamento da recaptura diária e dos lembretes de compromisso da Agenda
# (ver comentários nos próprios arquivos .cron). Copiados antes do resto do
# código-fonte de propósito, mesma lógica de cache do modelo de IA abaixo —
# só invalida esta camada se um destes arquivos específicos mudar.
COPY docker/capturar-movimentacoes.cron /etc/cron.d/capturar-movimentacoes
COPY docker/lembretes-compromissos.cron /etc/cron.d/lembretes-compromissos
RUN chmod 0644 /etc/cron.d/capturar-movimentacoes /etc/cron.d/lembretes-compromissos
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Baixa os pesos do modelo de IA local (~1,1 GB, o modelo "pequeno" — ver
# baixar_modelo_ia_local.py) ANTES de copiar o resto do código-fonte, de
# propósito: assim esta camada do Docker fica em cache e só baixa de novo
# se este script específico mudar — não a cada deploy de uma alteração
# qualquer no resto do sistema. Evita ~1,1 GB de download desnecessário em
# todo push. Se a rede cair durante o build, o deploy falha aqui (de
# propósito) em vez de subir um Agente de IA quebrado em silêncio.
#
# Existe um modelo "grande" (~2,5 GB, mais robusto) pronto no mesmo script,
# desligado por padrão aqui por falta de RAM sobrando no servidor de
# produção atual (checamos o painel do EasyPanel: ~74% de RAM já em uso
# antes de qualquer coisa da IA). Para ativar quando o servidor tiver mais
# RAM: troque a linha abaixo para
# "RUN python baixar_modelo_ia_local.py grande", defina
# IA_LOCAL_MODELO_PATH=/app/app/ia_local/modelos/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
# nas variáveis de ambiente do serviço, e veja PENDENCIAS.md (seção -6)
# para os ajustes de workers/contexto que também valem a pena nesse caso.
COPY baixar_modelo_ia_local.py .
RUN python baixar_modelo_ia_local.py

COPY . .

RUN mkdir -p /app/uploads

EXPOSE 5000

# -w 2 (em vez de 4): o Agente de IA carrega o modelo local (~1,1 GB de RAM)
# por worker do gunicorn, na primeira mensagem que cada um atender
# (carregamento tardio, não acontece se a IA não for usada). No pior caso,
# com 4 workers isso somaria ~4-6 GB só de modelo, fora o resto da app; com
# 2 workers o pior caso fica em ~2-3 GB. Se o plano do servidor tiver bastante
# RAM sobrando (8 GB+), pode voltar pra "-w", "4"; se aparecer erro de
# memória (worker killed / OOM) mesmo com 2, reduza para "-w", "1".
#
# --timeout 300 (era 120): o modelo local de IA roda por CPU (sem GPU) — um
# rascunho de petição (até 1400 tokens de resposta, ver
# app/utils/analise_processo_ia.py) com uma instrução longa e detalhada pode
# passar de 2 minutos pra gerar num servidor mais modesto. Com 120s, o
# gunicorn matava o worker NO MEIO da geração (WORKER TIMEOUT no log) antes
# de terminar, e o usuário via só "Internal Server Error" sem explicação —
# aconteceu de verdade num rascunho de petição real durante os testes desta
# rodada. 300s dá folga confortável pro pior caso (prompt grande + resposta
# no limite de tokens) sem deixar o worker preso indefinidamente se travar
# de verdade por outro motivo. Efeito colateral consciente: como só há 2
# workers (ver acima), enquanto um deles está gerando uma resposta de IA
# longa, sobra só 1 worker livre pra atender TODO o resto do sistema (outros
# usuários, outras telas) — aceitável pra hoje (funcionalidade sob demanda,
# não é o fluxo principal do sistema), mas se isso incomodar na prática com
# mais gente usando o Agente de IA ao mesmo tempo, a solução de verdade é
# tirar essa geração do ciclo de requisição/resposta (fila em segundo plano,
# ver PENDENCIAS.md) em vez de só aumentar o timeout de novo.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "300", "run:app"]
