FROM node:20-slim

# Dependências do Chromium (usado via Puppeteer pelo whatsapp-web.js para
# manter uma sessão real de WhatsApp Web rodando headless dentro do
# container). Instala o Chromium do repositório do Debian em vez de deixar
# o Puppeteer baixar o dele (mais leve e mais confiável em build).
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium ca-certificates fonts-liberation \
    libnss3 libatk-bridge2.0-0 libgtk-3-0 libgbm1 libasound2 libxss1 \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

COPY server.js .

# Sessão do WhatsApp Web (login) fica aqui — PRECISA ser um volume
# persistente no EasyPanel, senão todo redeploy exige escanear o QR de
# novo (ver PENDENCIAS.md).
RUN mkdir -p /data/sessao
VOLUME ["/data/sessao"]

EXPOSE 3000
CMD ["node", "server.js"]
