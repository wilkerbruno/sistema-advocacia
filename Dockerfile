FROM python:3.12-slim

WORKDIR /app

# g++ e cmake: ficam só como rede de segurança pro pip conseguir compilar
# o llama-cpp-python do zero SE um dia a versão travada em requirements.txt
# mudar e o wheel pré-compilado (ver linha do pip install abaixo) não
# acompanhar a mudança — não é mais o caminho principal (ver comentário
# abaixo). cron: roda a recaptura diária de movimentações via DataJud
# dentro do próprio container (ver docker/capturar-movimentacoes.cron e
# PENDENCIAS.md, seção -3) — não depende de nenhum recurso externo de
# agendamento. redis-server: fila de processamento em segundo plano da
# geração de IA (ver app/utils/fila.py, docker/entrypoint.sh e
# PENDENCIAS.md, seção -32) — roda dentro deste mesmo container, de
# propósito, pra não exigir configurar um serviço novo no EasyPanel.
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev pkg-config gcc g++ cmake cron redis-server redis-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --extra-index-url: o llama-cpp-python (motor do modelo de IA local, ver
# app/utils/ia_local.py) não tem wheel pré-compilada no PyPI normal pra
# esta combinação de SO/Python — sem isso, o pip compila o llama.cpp
# inteiro (C++) na hora do build, o que já travou o servidor de produção
# inteiro uma vez por esgotar toda a RAM disponível durante o build (ver
# PENDENCIAS.md, seção -29). Este índice extra (mantido pelo próprio autor
# do llama-cpp-python) publica uma wheel pré-compilada pra CPU — existe
# build pronta pra exatamente a versão presa em requirements.txt (0.3.34).
# Com isso o pip só baixa o binário pronto, sem compilar nada. Se um dia
# atualizar a versão do llama-cpp-python, confira antes se existe wheel
# pra essa versão nova em
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
# Este é (e sempre foi) o modelo "pequeno" — SEM o argumento "grande".
# Existe um modelo "grande" (~2,5 GB, mais robusto) pronto no mesmo script,
# mas ele continua desligado por padrão aqui, de propósito, por falta de
# RAM sobrando no servidor atual (ver PENDENCIAS.md, seções -30 e -31: o
# número "2,5 GB" que apareceu nas conversas é do modelo GRANDE, que nunca
# chegou a ser ativado — o que rodava era sempre este modelo pequeno de
# 1,1 GB; o que se aproximava de ~2,5 GB era a SOMA de até 2 workers do
# gunicorn carregando esse mesmo modelo pequeno cada um, não um modelo
# maior — por isso reduzi pra 1 worker só logo abaixo, ver comentário no
# CMD no fim do arquivo).
COPY baixar_modelo_ia_local.py .
RUN python baixar_modelo_ia_local.py

COPY . .

RUN mkdir -p /app/uploads

EXPOSE 5000

# -w 2 (voltou de 1 — ver PENDENCIAS.md, seções -31 e -32): o -w 1 tinha
# sido usado como paliativo porque, na época, o worker do gunicorn que
# atendia o Agente de IA carregava o modelo local (~1,1 GB de RAM) na
# própria memória do processo web, e enquanto gerava uma resposta (minutos,
# rodando por CPU) esse worker ficava ocupado — com só 1 worker, ISSO
# travava o sistema inteiro pra todo mundo, não só pra quem usava a IA.
#
# Agora (seção -32) a geração de IA roda inteira num processo separado (o
# worker do RQ, iniciado pelo entrypoint.sh — não confundir com "worker do
# gunicorn") — os workers do gunicorn NUNCA MAIS carregam o modelo nem
# chamam o motor de IA diretamente, só leem/gravam o pedido no banco e
# devolvem a resposta na hora. Ou seja: o motivo original do -w 1 deixou de
# existir — o teto de RAM da IA já está garantido de outro jeito (só existe
# 1 worker do RQ, ver docker/entrypoint.sh, então só uma cópia do modelo
# fica carregada por vez, não importa quantos workers do gunicorn existam).
# Por isso voltou pra "-w", "2" — se o plano do servidor crescer, dá pra
# subir mais ainda.
#
# --timeout 300: sem a IA rodando dentro do ciclo de requisição/resposta,
# esse valor alto deixou de ser essencial (era pra cobrir o tempo de
# geração do modelo, que passava fácil de 2 minutos) — mas mantive por
# segurança, como folga geral pra qualquer outra operação lenta do sistema
# (upload grande, chamada à API do DataJud etc.); não custa nada ficar alto
# quando não está em uso.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "300", "run:app"]
