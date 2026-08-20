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

# -w 1 (era 2, que por sua vez já tinha sido reduzido de 4): o Agente de
# IA carrega o modelo local (~1,1 GB de RAM) por worker do gunicorn, na
# primeira mensagem que cada um atender (carregamento tardio, não
# acontece se a IA não for usada) — e cada worker do gunicorn é um
# processo Python separado, então cada um carrega sua PRÓPRIA cópia do
# modelo na memória (não é compartilhado entre eles). Com 2 workers, no
# pior caso (os dois atenderam pelo menos uma mensagem de IA) isso já
# somava ~2,2-2,5 GB só do modelo — mesmo sendo sempre o modelo pequeno de
# 1,1 GB, nunca o grande de 2,5 GB (ver comentário sobre o download do
# modelo, mais acima). Foi esse número (~2,5 GB de RAM ocupada) que gerou
# a confusão de parecer "um modelo maior foi instalado" (ver PENDENCIAS.md,
# seção -31) — na real o modelo é o mesmo de sempre, só estava carregado
# em dobro. Com 1 worker só, o teto fica garantido em ~1,1 GB pra IA,
# não importa quantas mensagens diferentes cheguem.
#
# Efeito colateral consciente (o mesmo já estava documentado como a
# próxima opção se precisasse economizar mais RAM): com 1 worker só, TODO
# o sistema (não só o Agente de IA) atende só uma requisição por vez — se
# dois usuários acessarem ao mesmo tempo, um espera o outro terminar. Pra
# telas normais isso é rápido e quase imperceptível; mas enquanto o Agente
# de IA está gerando uma resposta (pode levar até alguns minutos, modelo
# rodando por CPU), o sistema INTEIRO fica bloqueado pra todo mundo até
# terminar — não só pra quem está usando a IA. Aceitável como solução
# temporária pra não estourar a RAM do servidor atual, mas é um tradeoff
# real pra um escritório com vários usuários simultâneos; a solução
# definitiva (tirar a geração de IA do ciclo de requisição/resposta, fila
# em segundo plano) segue pendente. Se o plano do servidor crescer (mais
# RAM), pode voltar pra "-w", "2" ou "-w", "4".
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
# de verdade por outro motivo. Com -w 1 (ver acima), esse efeito colateral
# fica ainda mais forte do que quando havia 2 workers: enquanto o único
# worker está gerando uma resposta de IA longa, NINGUÉM MAIS consegue usar
# o sistema até terminar (não sobra nenhum worker livre) — aceitável como
# solução temporária, mas se isso incomodar na prática, a solução de
# verdade é tirar essa geração do ciclo de requisição/resposta (fila em
# segundo plano, ver PENDENCIAS.md) em vez de só aumentar o timeout de novo.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5000", "--timeout", "300", "run:app"]
