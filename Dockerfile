FROM python:3.12-slim

WORKDIR /app

# g++ e cmake: necessários pra compilar o llama-cpp-python (motor do modelo
# de IA local do Agente de IA, ver app/utils/ia_local.py) caso não exista um
# wheel pré-compilado pra esta combinação exata de SO/Python/arquitetura.
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev pkg-config gcc g++ cmake \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Baixa os pesos do modelo de IA local (~1,1 GB) ANTES de copiar o resto do
# código-fonte, de propósito: assim esta camada do Docker fica em cache e só
# baixa de novo se este script específico mudar — não a cada deploy de uma
# alteração qualquer no resto do sistema. Evita ~1 GB de download
# desnecessário em todo push. Se a rede cair durante o build, o deploy falha
# aqui (de propósito) em vez de subir um Agente de IA quebrado em silêncio.
COPY baixar_modelo_ia_local.py .
RUN python baixar_modelo_ia_local.py

COPY . .

RUN mkdir -p /app/uploads

EXPOSE 5000

# -w 2 (em vez de 4): o Agente de IA agora carrega o modelo local (~1,1 GB
# de RAM) por worker do gunicorn, na primeira mensagem que cada um atender
# (carregamento tardio, não acontece se a IA não for usada). No pior caso,
# com 4 workers isso somaria ~4-6 GB só de modelo, fora o resto da app; com
# 2 workers o pior caso fica em ~2-3 GB. Se o plano do servidor tiver bastante
# RAM sobrando (8 GB+), pode voltar pra "-w", "4"; se aparecer erro de
# memória (worker killed / OOM) mesmo com 2, reduza para "-w", "1".
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:5000", "--timeout", "120", "run:app"]
