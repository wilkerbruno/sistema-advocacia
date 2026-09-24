"""
Agente Local do JusControl (PENDENCIAS.md, seção -56) — permite que o
próprio advogado busque, usando o certificado digital DELE, os autos
completos (PDF) e o histórico integral de um processo, inclusive
processos NÃO PÚBLICOS, rodando um pequeno programa na própria máquina
(pasta agente_local_jc/ na raiz do repositório) — nunca no servidor da
JusControl.

Por que isso existe (resumo do que já foi decidido com o cliente, ver
histórico de conversa):
  - O DataJud (API pública gratuita do CNJ, já integrada — ver
    app/utils/conector_datajud.py) só devolve METADADOS — nunca o PDF
    do processo. Isso é um desenho deliberado do próprio CNJ (proteção
    de sigilo/dados de parte), não uma limitação técnica nossa.
  - Pra buscar o processo COMPLETO (PDF + histórico integral), inclusive
    de processos não-públicos, é preciso autenticar no sistema do
    tribunal com o certificado/token do próprio advogado que é
    procurador naquele processo — e um certificado A3 (token físico)
    NUNCA pode ser exportado pra um servidor, por desenho do próprio
    hardware (a chave privada não sai do token).
  - A solução: o certificado/token NUNCA sai da máquina do advogado. Um
    agente local roda lá, autentica no tribunal, baixa o PDF e o
    histórico, e manda só o RESULTADO (não a credencial) pra cá. Este
    módulo é o lado SERVIDOR dessa troca: pareamento do agente (um
    token de acesso próprio, por advogado/dispositivo — nunca a
    credencial do tribunal) e a fila de solicitações de busca.

Dois modelos:
  - AgenteLocalPareado: "este token de acesso pertence ao agente local
    que o advogado X instalou na máquina Y" — mesmo padrão de
    TokenIntegracao (app/models/token_integracao.py: só o HASH do
    token fica salvo, o valor puro só aparece uma vez, na hora de
    gerar), mas por USUÁRIO, não por empresa — cada advogado usa o
    PRÓPRIO certificado, então o pareamento é pessoal, não do escritório
    inteiro.
  - SolicitacaoBuscaAutos: a fila de pedidos — "busque os autos
    completos do processo P usando o conector do tribunal T" (ver
    app/utils/tribunais_conectores.py para os slugs disponíveis), com
    status pendente → em_andamento → concluída/erro/cancelada. O agente
    local faz polling em GET /api/agente-local/tarefas (ver
    app/routes/agente_local_api.py) e casa cada solicitação com o
    Documento resultante quando termina.

⚠️ IMPORTANTE (mesma honestidade de escopo do resto do projeto): estes
dois modelos são só a INFRAESTRUTURA de pareamento/fila — nenhuma
chamada de verdade a nenhum tribunal acontece no servidor, em nenhum
momento. A parte que fala de verdade com o tribunal (SOAP/MNI) mora
inteiramente em agente_local_jc/, roda na máquina do advogado, e ainda
NÃO foi testada contra nenhum tribunal real — ver
agente_local_jc/README.md antes de usar isso em produção.

Três camadas de credencial, cada uma protegendo uma coisa diferente (item
novo, pedido do usuário — ver PENDENCIAS.md, seção mais recente):
  1. Certificado A1 + senha do tribunal: autentica no TRIBUNAL. Nunca sai
     da máquina do advogado (agente_local_jc/certificado.py).
  2. Token de pareamento (este arquivo, `AgenteLocalPareado`): autentica o
     AGENTE LOCAL no servidor JusControl — decide de qual usuário é cada
     tarefa (`SolicitacaoBuscaAutos`), nunca entrega tarefa de um advogado
     pro agente de outro (ver `app/routes/agente_local_api.py::exige_agente`
     e `_tarefa_do_agente_ou_404`).
  3. Login local (OAB + senha, só dentro de `agente_local_jc/`, nunca
     chega aqui no servidor) + autenticador (2FA) pra abrir a tela de
     Configuração — duas travas NOVAS, só do lado do agente:
     - OAB+senha: bloqueia o PROGRAMA em si (tela de cadeado ao abrir),
       conferido 100% localmente (hash+salt em config.json, nunca sai da
       máquina) — protege quem tem acesso físico ao notebook.
     - Autenticador: bloqueia a tela de CONFIGURAÇÃO (onde ficam a senha
       do certificado, o próprio OAB+senha local e o token de
       pareamento) — usa o MESMO autenticador (TOTP) já configurado na
       conta JusControl do advogado (`app/utils/totp.py`), conferido por
       este servidor via `POST /api/agente-local/verificar-autenticador`
       (o servidor já sabe de qual usuário é o pedido através do próprio
       token de pareamento — nunca é preciso mandar e-mail nem senha de
       novo). `totp_falhas_consecutivas`/`totp_bloqueado_ate` acima são a
       trava de tentativas desse endpoint.
"""
import hashlib
import secrets
from datetime import datetime

from app.extensions import db


class AgenteLocalPareado(db.Model):
    __tablename__ = "agentes_locais_pareados"

    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    usuario = db.relationship("Usuario")

    apelido = db.Column(db.String(80), nullable=False)  # ex: "Notebook do escritório"
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    prefixo = db.Column(db.String(12), nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_contato_em = db.Column(db.DateTime)  # atualizado a cada polling — "visto por último em"
    revogado_em = db.Column(db.DateTime)

    # Trava de tentativas do autenticador (2FA) usado pra abrir a tela de
    # Configuração do agente local (pedido do usuário: "para o cara
    # acessar a configuração do aplicativo ele tem que autenticar com
    # autenticador igual funciona hoje para logar no sistema web" — ver
    # POST /api/agente-local/verificar-autenticador em
    # app/routes/agente_local_api.py). Mesmo espírito de
    # `_MAX_TENTATIVAS_TOTP_LOGIN` em app/routes/auth.py, só que aqui não
    # existe sessão Flask pra guardar a contagem (é uma API sem estado de
    # sessão, chamada pelo agente local) — por isso a contagem fica
    # persistida neste registro, por AGENTE (não por usuário: dois
    # notebooks pareados do mesmo advogado têm cada um sua própria trava).
    # Nullable de propósito (sincronizar_schema.py não aplica DEFAULT em
    # ALTER TABLE numa tabela que já tem linha) — None é tratado como 0
    # em todo lugar que lê este campo.
    totp_falhas_consecutivas = db.Column(db.Integer, nullable=True)
    totp_bloqueado_ate = db.Column(db.DateTime, nullable=True)

    @staticmethod
    def _hash_de(valor):
        return hashlib.sha256(valor.encode("utf-8")).hexdigest()

    def totp_bloqueado(self):
        """True enquanto a trava de tentativas erradas do autenticador
        estiver valendo para este agente."""
        return bool(self.totp_bloqueado_ate and self.totp_bloqueado_ate > datetime.utcnow())

    def registrar_falha_totp(self, max_tentativas=5, minutos_bloqueio=5):
        """Incrementa a contagem de tentativas erradas; ao atingir
        `max_tentativas`, bloqueia por `minutos_bloqueio` e zera a
        contagem (mesmo padrão de "N tentativas erradas → bloqueio
        temporário, contagem reinicia" já usado no login web — ver
        app/routes/auth.py::_MAX_TENTATIVAS_TOTP_LOGIN). Quem chama ainda
        precisa dar commit."""
        from datetime import timedelta
        self.totp_falhas_consecutivas = (self.totp_falhas_consecutivas or 0) + 1
        if self.totp_falhas_consecutivas >= max_tentativas:
            self.totp_bloqueado_ate = datetime.utcnow() + timedelta(minutes=minutos_bloqueio)
            self.totp_falhas_consecutivas = 0

    def resetar_falhas_totp(self):
        self.totp_falhas_consecutivas = 0
        self.totp_bloqueado_ate = None

    @classmethod
    def emitir_para(cls, usuario, apelido):
        """
        Cria e persiste (via db.session.add — quem chama precisa dar
        commit) um pareamento novo para `usuario`. Devolve
        (registro, valor_puro) — só essa chamada tem acesso ao valor
        puro; mostre-o uma única vez na tela de configuração do agente
        local, nunca em log nem em outro lugar.
        """
        valor = secrets.token_urlsafe(32)
        registro = cls(
            usuario_id=usuario.id,
            apelido=(apelido or "Agente local").strip()[:80] or "Agente local",
            token_hash=cls._hash_de(valor),
            prefixo=valor[:10],
        )
        db.session.add(registro)
        return registro, valor

    @classmethod
    def validar(cls, valor_recebido):
        """Devolve o AgenteLocalPareado ATIVO correspondente ao valor
        recebido (comparando por hash), ou None se não houver nenhum
        válido."""
        if not valor_recebido:
            return None
        return cls.query.filter_by(token_hash=cls._hash_de(valor_recebido), ativo=True).first()

    def revogar(self):
        self.ativo = False
        self.revogado_em = datetime.utcnow()


class SolicitacaoBuscaAutos(db.Model):
    """
    Um pedido de "busque os autos completos deste processo" — criado
    pelo advogado na tela do processo (botão "Buscar autos completos"),
    atendido pelo agente local instalado na máquina DELE (nunca por
    agente de outro advogado — ver `solicitado_por_id` e a checagem em
    app/routes/agente_local_api.py).
    """
    __tablename__ = "solicitacoes_busca_autos"

    STATUS = ("pendente", "em_andamento", "concluida", "erro", "cancelada")
    STATUS_ABERTOS = ("pendente", "em_andamento")

    id = db.Column(db.Integer, primary_key=True)
    processo_id = db.Column(db.Integer, db.ForeignKey("processos.id"), nullable=False)
    processo = db.relationship("Processo")

    # Slug do conector de tribunal a usar — ver app/utils/tribunais_conectores.py.
    # Multi-tribunal por desenho: cada advogado escolhe o conector certo pro
    # processo dele (tribunais diferentes usam sistemas diferentes), o
    # servidor só guarda QUAL foi pedido — quem sabe conversar com aquele
    # tribunal é o agente local, não o servidor.
    tribunal_conector = db.Column(db.String(40), nullable=False)
    numero_processo_solicitado = db.Column(db.String(40))  # snapshot do nº CNJ no momento do pedido

    status = db.Column(db.String(20), default="pendente", nullable=False)
    mensagem_erro = db.Column(db.String(500))

    solicitado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    solicitado_por = db.relationship("Usuario", foreign_keys=[solicitado_por_id])

    agente_local_id = db.Column(db.Integer, db.ForeignKey("agentes_locais_pareados.id"))
    agente_local = db.relationship("AgenteLocalPareado")

    documento_id = db.Column(db.Integer, db.ForeignKey("documentos.id"))
    documento = db.relationship("Documento")

    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    concluido_em = db.Column(db.DateTime)

    def iniciar(self, agente):
        self.status = "em_andamento"
        self.agente_local_id = agente.id

    def concluir(self, documento):
        self.status = "concluida"
        self.documento_id = documento.id
        self.concluido_em = datetime.utcnow()

    def falhar(self, mensagem):
        self.status = "erro"
        self.mensagem_erro = (mensagem or "Erro não especificado.")[:500]
        self.concluido_em = datetime.utcnow()

    def cancelar(self):
        self.status = "cancelada"
        self.concluido_em = datetime.utcnow()
