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

Só o conector do PJe (via protocolo MNI/SOAP) está implementado, e ele
foi escrito a partir de documentação técnica pública (CNJ, STF, TJRJ) —
**nenhuma chamada real foi feita contra nenhum tribunal**. Antes de usar
isto com um processo de verdade:

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
- Hoje só `pje_mni` existe de verdade. `esaj_sp`, `eproc` e `projudi`
  já aparecem como opção no menu do JusControl (desabilitados, "ainda
  não implementado") só para deixar visível que o desenho não é
  amarrado a um tribunal só.

## Instalação

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

Edite o `.env` recém-criado:

- `JUSCONTROL_URL`: endereço do seu JusControl.
- `JUSCONTROL_AGENTE_TOKEN`: gerado na tela **Meu agente local**, dentro
  do JusControl (menu lateral, item "AL"). Esse token só aparece uma
  vez, na hora de gerar — copie assim que gerar.
- `CERTIFICADO_PFX_CAMINHO` / `CERTIFICADO_PFX_SENHA`: seu certificado
  A1 (arquivo `.pfx`/`.p12`) e a senha dele. **Certificado A3 (token
  físico) ainda não é suportado** — a chave de um A3 não pode ser
  exportada por desenho do próprio hardware; suportar A3 exigiria um
  driver PKCS#11 específico do fabricante do token, deixado como
  próximo passo.
- `PJE_TRIBUNAL` / `PJE_INSTANCIA` / `PJE_ID_CONSULTANTE` /
  `PJE_SENHA_CONSULTANTE` / `PJE_URL_WSDL`: dados do tribunal-piloto que
  você vai testar primeiro (ver seção acima).

## Rodando

```
python main.py
```

O agente conecta no JusControl, confirma o token (`/api/agente-local/ping`),
e a partir daí fica checando a cada `INTERVALO_POLLING_SEGUNDOS` (padrão
60s) se há alguma busca pendente para o seu usuário. Quando alguém pede
"Buscar autos completos" num processo (na tela do processo, dentro do
JusControl), o agente pega esse pedido no próximo ciclo, faz a busca, e
envia o PDF de volta — sem precisar de nenhuma ação manual além de
deixar o `python main.py` aberto.

Para rodar em segundo plano continuamente (produção), configure isto
como um serviço do Windows (ex: com NSSM) ou uma tarefa agendada que
reinicia se cair — isso ainda não está automatizado neste piloto.

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
  RESULTADO já pronto (PDF) — nunca a credencial usada para consegui-lo.

## Estrutura dos arquivos

- `config.py` — lê `.env`.
- `certificado.py` — abre o `.pfx`/`.p12` em memória.
- `conector_base.py` — interface que todo conector de tribunal implementa.
- `conectores/pje_mni.py` — conector do PJe via MNI/SOAP (piloto).
- `registro_conectores.py` — escolhe o conector certo pelo slug do pedido.
- `cliente_api.py` — fala com `/api/agente-local/*` no JusControl.
- `main.py` — laço principal (polling).
