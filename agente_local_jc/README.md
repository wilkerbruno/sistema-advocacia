# Agente Local do JusControl

Programa pequeno que roda no **computador do próprio advogado** (nunca no
servidor/VPS do JusControl) para buscar os autos completos (PDF) e o
histórico integral de um processo — inclusive processos sigilosos, desde
que o advogado seja mesmo procurador dele — usando o certificado digital
ou token DELE. O certificado/token nunca é enviado para lugar nenhum,
nem mesmo para o servidor do JusControl: este programa autentica no
tribunal por conta própria, baixa o resultado, e manda só o PDF pronto
de volta.

## ⚠️ Status: piloto, não testado contra tribunal real

Os conectores do PJe, do Projudi e do e-SAJ (os três via protocolo
MNI/SOAP, mesma lógica compartilhada — ver `conectores/mni_soap.py`)
estão implementados, e foram escritos a partir de documentação técnica
pública (CNJ, STF, TJRJ) — **nenhuma chamada real foi feita contra
nenhum tribunal**. O e-SAJ é o MAIS incerto dos três: diferente do PJe
(protocolo nacional documentado pelo CNJ) e do Projudi (mesma
obrigação legal), **não há confirmação de que o e-SAJ (TJMS, TJSP e
outros) realmente exponha esse tipo de webservice** — ver aviso
específico em `conectores/esaj.py`. Antes de usar isto com um processo
de verdade:

1. Consiga uma credencial/certificado de **teste** junto ao tribunal que
   você vai usar primeiro (comece por só um — ver seção "Por que
   multi-tribunal" abaixo).
2. Confirme a URL real do WSDL do MNI daquele tribunal (a padrão em
   `conectores/pje_mni.py` é só uma observação, não uma garantia).
3. Rode o agente contra esse ambiente de teste e confira se os nomes de
   campo que o código assume (`idConsultante`, `numeroProcesso`,
   `idDocumento`, `conteudo`, entre outros) batem com a resposta real —
   se o tribunal usar nomes diferentes, é preciso ajustar
   `conectores/pje_mni.py`.
4. Só depois disso, use com processos reais.

Isso não é burocracia por burocracia: uma automação que fala com sistema
de tribunal errado pode gerar erro silencioso (dado incompleto sem
avisar) — o mesmo cuidado que já existe no restante do JusControl para
qualquer integração paga não implementada "no escuro" (ver
`app/utils/captura_conectores.py` no repositório principal).

## Por que multi-tribunal

Advogados diferentes do mesmo escritório trabalham com tribunais
diferentes. A arquitetura foi pensada para isso desde o início:

- O servidor (`app/models/agente_local.py`,
  `app/utils/tribunais_conectores.py`) só guarda um **slug** de qual
  conector usar por pedido — nunca sabe falar com nenhum tribunal
  diretamente.
- Este agente local escolhe, pelo slug recebido, qual conector chamar
  (`registro_conectores.py`) — cada conector novo é um arquivo novo em
  `conectores/`, implementando a interface `ConectorTribunalLocal`
  (`conector_base.py`).
- Hoje `pje_mni`, `projudi` e `esaj_sp` existem de verdade (os três
  pilotos, ainda não testados contra tribunal real — ver seção acima).
  Os três usam o mesmo protocolo MNI por baixo, então compartilham a
  lógica de consulta em `conectores/mni_soap.py` — cada um só monta a
  URL do próprio WSDL de um jeito diferente: o PJe tenta adivinhar a
  partir de tribunal+instância; Projudi e e-SAJ exigem a URL informada
  manualmente, porque não há um padrão de domínio conhecido entre os
  tribunais que usam esses sistemas (e, no caso do e-SAJ, nem
  confirmação de que o serviço exista). `eproc` ainda aparece como
  opção no menu do JusControl (desabilitado, "ainda não implementado")
  só para deixar visível que o desenho não é amarrado a um tribunal só.

## Para o advogado: instalar pelo instalador (jeito normal de usar)

1. Dentro do JusControl, abra **Meu agente local** (menu lateral, item
   "AL") e clique em **Baixar agente local (Windows)**.
2. Rode o `JusControlAgente-Setup.exe` baixado — é um instalador comum
   (Avançar / Avançar / Concluir), não pede permissão de administrador.
3. Na primeira abertura, uma janela pede o **endereço do JusControl** e
   o **token de pareamento** — gere o token na mesma tela "Meu agente
   local" (aparece uma única vez, copie assim que gerar) e cole os dois
   campos. Clique em "Testar conexão" pra confirmar antes de salvar.
4. Pronto — o agente fica com um ícone perto do relógio do Windows
   (verde = conectado, vermelho = com problema) e liga sozinho toda vez
   que o Windows liga. Não precisa deixar nenhum terminal aberto.

O ícone tem um menu (clique com o botão direito): **Configurar...**
(reabre a janela do passo 3 — útil pra trocar o token, ou preencher os
dados do certificado/tribunal quando o piloto de um tribunal estiver
pronto pra uso real), **Verificar agora** (não espera o próximo ciclo),
**Ver log** e **Sair**.

Certificado digital (A1) e os dados dos tribunais-piloto (PJe, Projudi
e e-SAJ, cada um em sua própria sub-seção) ficam numa seção "Avançado"
da mesma janela de configuração, recolhida por padrão — a maioria dos
advogados só precisa colar o token no primeiro passo; o certificado e
os dados do tribunal só são necessários quando for buscar autos de
verdade. Para Projudi e e-SAJ, a URL do WSDL não tem um padrão
adivinhável (diferente do PJe) — precisa ser obtida direto com o
tribunal antes de preencher; no caso do e-SAJ, pode ser que o tribunal
simplesmente não tenha esse serviço pra oferecer (ver aviso acima).

## Login local (OAB + senha) e autenticador na Configuração — opcional

Duas travas novas, pensadas pra quem tem outras pessoas com acesso físico
ao mesmo computador (ex.: notebook do escritório usado por mais de uma
pessoa):

- **Tela de cadeado ao abrir o agente** — na janela de Configuração, seção
  "Login local (opcional)", preencha OAB + senha (duas vezes, pra
  confirmar). A partir daí, toda vez que o Agente Local iniciar (ou o
  Windows ligar), ele pede essa senha antes de fazer qualquer coisa — 5
  tentativas erradas fecham o programa. Essa senha é conferida 100%
  localmente (nunca é mandada pro JusControl nem pra lugar nenhum — mesmo
  princípio do certificado A1, ver seção "Segurança" abaixo); deixe os
  três campos em branco pra não usar essa trava.
- **Autenticador (2FA) obrigatório pra abrir a Configuração** — depois do
  primeiro pareamento (token colado e testado), reabrir "Configurar..."
  pelo menu do ícone passa a pedir o código do MESMO app autenticador já
  usado pra logar no sistema web do JusControl. O agente manda esse
  código pro servidor conferir (usando o próprio token de pareamento pra
  saber de qual conta é) — por isso, diferente da tela de cadeado acima,
  isto PRECISA de internet no momento de abrir a Configuração. Se a conta
  ainda não tiver autenticador confirmado no sistema web (ou o sistema
  não tiver essa funcionalidade ligada), a Configuração abre direto, sem
  pedir código nenhum.

## Para quem administra o JusControl: publicar uma versão nova do instalador

O `.exe` é compilado sozinho pelo GitHub Actions (não precisa de um
Windows à mão) — ver `.github/workflows/build-agente-local.yml`. Pra
publicar:

```
git tag agente-v0.1.0
git push origin agente-v0.1.0
```

Em alguns minutos o instalador aparece nas "Releases" do repositório
como `JusControlAgente-Setup.exe`. Depois, configure o botão "Baixar
agente local" da tela `/agente-local` — tem duas formas, dependendo se
o repositório é público ou privado (ver `config.py` no repositório
principal para os detalhes completos):

**Repositório privado (recomendado — protege o código-fonte):**
defina, nas variáveis de ambiente do serviço no EasyPanel:
- `AGENTE_LOCAL_GITHUB_REPO` = `<seu-usuario>/<seu-repositorio>`
- `AGENTE_LOCAL_GITHUB_TOKEN` = um Personal Access Token "fine-grained"
  do GitHub (github.com/settings/personal-access-tokens → Generate new
  token → escolha SÓ este repositório → em "Permissions", "Contents" =
  "Read-only", todas as outras permissões deixe sem acesso), gerado
  numa conta com acesso ao repositório. O servidor usa esse token pra
  buscar o instalador pela API do GitHub e entregar os bytes direto —
  o advogado nunca acessa o GitHub, e o token nunca é exposto a ele.

**Repositório público (mais simples, mas expõe o código-fonte):**
defina só `AGENTE_LOCAL_INSTALADOR_URL` apontando para:
```
https://github.com/<seu-usuario>/<seu-repositorio>/releases/latest/download/JusControlAgente-Setup.exe
```

Sem NENHUMA dessas variáveis definidas, o botão fica escondido (nunca
aponta pra um link quebrado). Sem precisar de rebuild do container do
JusControl pra nenhuma das duas opções, é só variável de ambiente.

## Para desenvolver/testar o próprio código do agente (sem instalar nada)

```
cd agente_local_jc
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.exemplo .env
```

Edite o `.env` recém-criado (mesmos campos da janela de configuração do
instalador — `JUSCONTROL_URL`, `JUSCONTROL_AGENTE_TOKEN`, e os campos
de certificado/tribunal — `PJE_*`, `PROJUDI_*` e `ESAJ_*` — se for
testar algum conector de verdade) e rode:

```
python main.py
```

Esse é o modo "terminal" (`main.py`) — mostra tudo em texto, sem ícone
de bandeja, pensado pra depurar um conector novo rapidamente. O modo com
ícone/instalador (`tray_app.py`) é o que vira o `.exe` — pra testar ELE
localmente sem compilar (ainda no Windows, já que usa recursos só dele):

```
python tray_app.py
```

## Segurança — o que este programa NUNCA faz

- Nunca envia o certificado, o token do tribunal, nem a senha de
  nenhum dos dois para o servidor do JusControl ou para qualquer
  outro lugar.
- Nunca grava a senha do certificado em disco.
- A chave privada do certificado só existe descriptografada na memória
  deste processo, e só pelo tempo da chamada ao tribunal (ver
  `certificado.py`) — quando precisa virar arquivo temporário (exigência
  da biblioteca HTTP para autenticação mTLS), o arquivo é criado com
  permissão restrita ao seu usuário e apagado logo em seguida.
- O único dado que sai desta máquina em direção ao JusControl é o
  RESULTADO já pronto (PDF) — nunca a credencial usada para consegui-lo
  — com uma única exceção deliberada, de mão única: o CÓDIGO do
  autenticador digitado na Configuração é conferido pelo servidor (ver
  seção acima) — mas isso é o mesmo código de 6 dígitos que a conta já
  usa pra logar no sistema web, nunca o segredo TOTP em si, e nunca é
  guardado em lugar nenhum, só usado naquele instante.
- Nunca grava a senha do certificado em disco.
- A senha do login local (OAB + senha, seção acima) nunca é gravada em
  texto puro — só um hash (PBKDF2-HMAC-SHA256, com sal aleatório) fica em
  `config.json`, a mesma técnica usada pra senha de conta no sistema web
  (ver `login_local.py`).
- A chave privada do certificado só existe descriptografada na memória
  deste processo, e só pelo tempo da chamada ao tribunal (ver
  `certificado.py`) — quando precisa virar arquivo temporário (exigência
  da biblioteca HTTP para autenticação mTLS), o arquivo é criado com
  permissão restrita ao seu usuário e apagado logo em seguida.
- A configuração salva pelo instalador (token, certificado etc.) fica só
  em `%APPDATA%\JusControlAgente\config.json`, nesta máquina — nunca é
  sincronizada com nada.

## Estrutura dos arquivos

- `config.py` — modo desenvolvedor: lê `.env`/variáveis de ambiente (usado por `main.py`).
- `config_store.py` — modo instalado: lê/grava `%APPDATA%\JusControlAgente\config.json` (usado por `tray_app.py`).
- `config_gui.py` — janela de configuração (tkinter), usada pelo `tray_app.py`.
- `login_local.py` — hash/verificação da senha do login local (OAB + senha), 100% local.
- `tela_bloqueio.py` — tela de cadeado (OAB + senha) mostrada ao iniciar o agente, quando configurada.
- `autenticador_local.py` — trava por autenticador (2FA da conta web) pra abrir a Configuração.
- `certificado.py` — abre o `.pfx`/`.p12` em memória.
- `conector_base.py` — interface que todo conector de tribunal implementa.
- `conectores/mni_soap.py` — lógica compartilhada do protocolo MNI/SOAP (usada por PJe, Projudi e e-SAJ).
- `conectores/pje_mni.py` — conector do PJe via MNI/SOAP (piloto).
- `conectores/projudi.py` — conector do Projudi via MNI/SOAP (piloto).
- `conectores/esaj.py` — conector do e-SAJ via MNI/SOAP (piloto, o mais incerto dos três — ver aviso no próprio arquivo).
- `registro_conectores.py` — escolhe o conector certo pelo slug do pedido.
- `cliente_api.py` — fala com `/api/agente-local/*` no JusControl (classe `ClienteJusControl`).
- `motor.py` — lógica compartilhada de "buscar tarefa pendente → processar → enviar resultado", usada tanto por `main.py` quanto por `tray_app.py`.
- `main.py` — modo terminal (desenvolvedor).
- `tray_app.py` — modo ícone na bandeja (é isto que vira o `.exe` instalado).
- `autostart_windows.py` — liga/desliga o início automático com o Windows.
- `build/` — spec do PyInstaller, script do Inno Setup, e o gerador do ícone — ver `.github/workflows/build-agente-local.yml` para como tudo isso se encaixa no build automático.
