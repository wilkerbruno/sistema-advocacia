from datetime import datetime
from app.extensions import db


class CredencialTribunal(db.Model):
    """
    Item 2 da lista de pipeline de IA jurídica trazida pelo usuário
    (PENDENCIAS.md, seção -108): "Download dos autos — Sessão autenticada
    do escritório no PJe, eproc, Projudi e ESAJ, com certificado ou
    credencial guardada no cofre já existente."

    ⚠️ Escopo desta rodada, honesto: isto é SÓ o cofre de credenciais —
    onde o escritório guarda o login/senha (cifrado, nunca em texto puro,
    ver app/utils/cofre.py, mesmo cofre já usado pra chave de API BYOK em
    app/routes/integracoes.py) de cada sistema de tribunal que usa. NÃO há
    aqui nenhum conector que efetivamente FAÇA login automatizado nem
    baixe autos sozinho — isso ficou de fora conscientemente (ver
    app/utils/conector_esaj_publico.py e app/utils/eproc_links.py, que já
    documentam por que uma automação de login real esbarraria em
    CAPTCHA/Cloudflare de cada tribunal, o que tornaria a automação
    tecnicamente frágil — quebra a cada mudança de layout do tribunal — e
    juridicamente arriscada — login automatizado imitando humano pode
    violar os termos de uso do próprio sistema do tribunal). A
    credencial fica guardada, pronta pra um dia um conector autenticado
    de verdade (ou um humano, manualmente) usar — este cadastro sozinho
    já resolve o "onde guardar com segurança", que é a parte que dá pra
    fazer sem esses riscos.

    Escopo: por EMPRESA inteira (não por unidade) — mesma decisão de
    ModeloPeca/timbrado: o escritório normalmente usa uma única credencial
    por sistema de tribunal, não uma por filial. Diferente de ModeloPeca,
    porém, um mesmo escritório pode ter VÁRIAS credenciais (uma por
    tribunal — ex.: PJe do TJSP e PJe do TRT2 são login e senha
    diferentes), por isso é uma lista, não um campo único na Empresa (ao
    contrário da chave BYOK do Claude/Gemini/DataJud, que é um valor só
    por empresa — ver app/routes/integracoes.py).

    `sistema` é um dos valores de SISTEMAS (dropdown fechado — são só
    esses 5 sistemas que este cadastro cobre por enquanto, mesmos citados
    literalmente no pedido do usuário). `tribunal` é texto livre (ex.:
    "TJSP", "TRT2", "TRF3") — identifica QUAL instância daquele sistema
    (o mesmo eproc, por exemplo, roda em vários tribunais diferentes, cada
    um com login próprio).

    `senha_cifrada` nunca é lida de volta em texto puro pra tela nenhuma
    depois de cadastrada — só decifrada no momento de uso por um conector
    autenticado de verdade (que ainda não existe). A tela só mostra "tem
    credencial cadastrada" (booleano), mesmo padrão já usado pras chaves
    de API BYOK em app/routes/integracoes.py.
    """
    __tablename__ = "credenciais_tribunal"

    SISTEMAS = ("eproc", "projudi", "esaj", "pje", "outro")

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False, index=True)

    sistema = db.Column(db.String(20), nullable=False)
    tribunal = db.Column(db.String(30), nullable=False)
    usuario_login = db.Column(db.String(200), nullable=False)
    senha_cifrada = db.Column(db.LargeBinary, nullable=True)
    observacao = db.Column(db.String(400), nullable=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    criado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    empresa = db.relationship("Empresa")
    criado_por = db.relationship("Usuario")

    def __repr__(self):
        return f"<CredencialTribunal {self.id} empresa={self.empresa_id} {self.sistema}/{self.tribunal}>"
