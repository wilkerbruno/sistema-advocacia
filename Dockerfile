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
# llama-cpp-python está comentado em requirements.txt por enquanto (motor
# da IA local desativado temporariamente — ver PENDENCIAS.md, seção -30),
# então este install voltou a ser um pip install simples, sem
# --extra-index-url. Se um dia reativar o llama-cpp-python (descomentando
# a linha em requirements.txt), lembre de voltar a apontar pra wheel
# pré-compilada, senão o pip cai em compilar o llama.cpp inteiro (C++) na
# hora do build — isso já derrubou o servidor de produção inteiro uma vez
# por esgotar toda a RAM disponível (ver PENDENCIAS.md, seção -29):
#   RUN pip install --no-cache-dir --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu -r requirements.txt
# confira antes se existe wheel pra versão travada em
# https://abetlen.github.io/llama-cpp-python/whl/cpu/llama-cpp-python/ —
# se não existir, compila do zero (por isso gcc/g++/cmake continuam
# instalados acima, só como plano B / rede de segurança).
RUN pip install --no-cache-dir -r requirements.txt

# Agendamento da recaptura diária e dos lembretes de compromisso da Agenda
# (ver comentários nos próprios arquivos .cron). Copiados antes do resto do
# código-fonte de propósito, mesma lógica de cache do modelo de IA abaixo —
# só invalida esta camada se um destes arquivos específicos mudar.
COPY docker/capturar-movimentacoes.cron /etc/cron.d/capturar-movimentacoes
COPY docker/lembretes-compromissos.cron /etc/cron.d/lembretes-compromissos
RUN chmod 0644 /etc/cron.d/capturar-movimentacoes /etc/cron.d/lembretes-compromissos
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Download dos pesos do modelo de IA local DESATIVADO temporariamente — ver
# PENDENCIAS.md, seção -30. O motor (llama-cpp-python, comentado em
# requirements.txt) e este download (~1,1 GB) juntos eram pesados demais
# pro servidor atual (2 núcleos / ~7,8 GB de RAM), tanto pra construir a
# imagem quanto pra rodar depois. app/utils/ia_local.py já tolera a
# ausência do modelo com uma mensagem amigável ("indisponível no
# momento") em vez de quebrar — nenhuma tela do sistema depende deste
# arquivo existir. Empresas configuradas para usar a API do Claude com
# chave própria (Empresa.PROVEDOR_IA_CLAUDE_BYOK, ver "Minhas
# Integrações") não são afetadas por esta mudança.
#
# Para reativar quando migrar pra uma VPS com mais RAM: descomente
# llama-cpp-python em requirements.txt, volte o pip install pra usar
# --extra-index-url (ver comentário acima), e descomente as duas linhas
# abaixo.
# COPY baixar_modelo_ia_local.py .
# RUN python baixar_modelo_ia_local.py

COPY . .

RUN mkdir -p /app/uploads

EXPOSE 5000

# -w 2 (em vez de 4): mantido conservador de propósito mesmo com a IA local
# desativada agora (ver PENDENCIAS.md, seção -30) — o motivo original era o
# modelo de IA local, mas o servidor já mostrou duas vezes nesta rodada que
# fica sem folga de RAM sobrando mesmo fora disso. Não mexi neste número
# agora pra não misturar mudanças; se quiser, dá pra testar "-w", "3" depois
# que o deploy estabilizar sem a IA local, acompanhando o painel de RAM do
# EasyPanel com calma.
#
# Comentário original (a razão de existir o -w 2, hoje desativada):
# o Agente de IA carrega o modelo local (~1,1 GB de RAM)
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
