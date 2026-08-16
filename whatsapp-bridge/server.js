/**
 * Bridge de WhatsApp para o JusControl — serviço SEPARADO do app Flask
 * principal, de propósito: mantém uma sessão de WhatsApp Web logada via
 * Puppeteer (biblioteca whatsapp-web.js), coisa que não dá pra fazer
 * dentro de um worker gunicorn Python comum.
 *
 * ATENÇÃO — automação NÃO-OFICIAL: isso simula um WhatsApp Web comum, não
 * é a API oficial da Meta. Viola os Termos de Serviço do WhatsApp e o
 * número usado aqui corre risco real de ser banido por comportamento
 * automatizado, principalmente enviando muitas mensagens fora de
 * conversas iniciadas pelo destinatário (que é exatamente o caso de um
 * lembrete de compromisso). Decisão explícita do dono do sistema, ciente
 * do risco — ver PENDENCIAS.md do projeto principal. RECOMENDAÇÕES
 * PRÁTICAS para reduzir o risco (não eliminam o risco, só reduzem):
 *   - Use um número de WhatsApp dedicado a isso, NUNCA o WhatsApp pessoal
 *     de um advogado/funcionário nem o número principal de atendimento
 *     do escritório.
 *   - Não mande volume alto de mensagens de uma vez.
 *   - Espere alguns dias de uso normal (conversas reais) no número antes
 *     de começar a automatizar envios, o WhatsApp desconfia mais de
 *     números novos com comportamento 100% automatizado desde o início.
 *
 * Fluxo:
 *   1. Sobe este serviço (separado do app principal — ver
 *      whatsapp-bridge/Dockerfile e PENDENCIAS.md pra como publicar no
 *      EasyPanel como um segundo serviço do mesmo projeto).
 *   2. Abre a rota /qr no navegador UMA VEZ, escaneia com o WhatsApp do
 *      celular escolhido (Configurações > Aparelhos conectados > Conectar
 *      aparelho). A sessão fica salva em /data/sessao (precisa ser um
 *      volume PERSISTENTE — sem isso, todo redeploy pede escanear de
 *      novo).
 *   3. O app Flask principal chama POST /enviar (ver app/utils/whatsapp.py)
 *      pra mandar o lembrete.
 */
const express = require("express");
const qrcode = require("qrcode");
const { Client, LocalAuth } = require("whatsapp-web.js");

const PORTA = process.env.PORT || 3000;
const TOKEN = process.env.BRIDGE_TOKEN || "";

const app = express();
app.use(express.json());

let ultimoQrDataUrl = null;
let conectado = false;

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: "/data/sessao" }),
  puppeteer: {
    headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

client.on("qr", async (qr) => {
  conectado = false;
  try {
    ultimoQrDataUrl = await qrcode.toDataURL(qr);
  } catch (e) {
    console.error("Falha ao gerar imagem do QR:", e);
  }
  console.log("Novo QR code gerado — abra /qr no navegador para escanear.");
});

client.on("ready", () => {
  conectado = true;
  ultimoQrDataUrl = null;
  console.log("WhatsApp conectado e pronto para enviar mensagens.");
});

client.on("auth_failure", (msg) => {
  conectado = false;
  console.error("Falha de autenticação do WhatsApp:", msg);
});

client.on("disconnected", (motivo) => {
  conectado = false;
  console.log("WhatsApp desconectado:", motivo);
});

client.initialize();

function checarToken(req, res, next) {
  if (TOKEN && req.headers["x-bridge-token"] !== TOKEN) {
    return res.status(401).json({ erro: "token inválido (X-Bridge-Token)" });
  }
  next();
}

// Normaliza número brasileiro pro formato exigido pelo WhatsApp Web
// (<código do país><DDD><número>@c.us). Se já vier com "55" na frente,
// não duplica.
function paraChatId(numero) {
  let digitos = String(numero || "").replace(/\D/g, "");
  if (digitos.length === 10 || digitos.length === 11) {
    digitos = "55" + digitos;
  }
  return `${digitos}@c.us`;
}

app.get("/status", (req, res) => {
  res.json({ conectado });
});

app.get("/qr", (req, res) => {
  if (conectado) {
    return res.send("<h1>WhatsApp já conectado — nada a escanear.</h1>");
  }
  if (!ultimoQrDataUrl) {
    return res.send("<h1>Gerando QR code...</h1><p>Atualize a página em alguns segundos.</p>");
  }
  res.send(`
    <h1>Escaneie com o WhatsApp do número escolhido para o escritório</h1>
    <p>No celular: Configurações &gt; Aparelhos conectados &gt; Conectar aparelho.</p>
    <img src="${ultimoQrDataUrl}" alt="QR code de conexão do WhatsApp" />
  `);
});

app.post("/enviar", checarToken, async (req, res) => {
  if (!conectado) {
    return res.status(503).json({ erro: "WhatsApp não conectado — abra /qr e escaneie primeiro." });
  }
  const { numero, mensagem } = req.body || {};
  if (!numero || !mensagem) {
    return res.status(400).json({ erro: "Campos obrigatórios: numero, mensagem." });
  }

  try {
    await client.sendMessage(paraChatId(numero), String(mensagem));
    res.json({ ok: true });
  } catch (e) {
    console.error("Falha ao enviar mensagem:", e);
    res.status(500).json({ erro: String(e) });
  }
});

app.listen(PORTA, () => {
  console.log(`Bridge de WhatsApp ouvindo na porta ${PORTA}.`);
});
