"""
Migração única para o modelo multi-tenant (SaaS por empresa).

O que faz, nesta ordem, e SÓ isso:
  1. Garante que as tabelas novas existem (empresas, licencas, pagamentos) —
     via db.create_all(), que nunca mexe em tabela já existente.
  2. Garante que a coluna `unidades.empresa_id` existe (criada como NULL,
     nunca NOT NULL direto — não quebra unidade já cadastrada).
  3. Cria (se ainda não existir) a empresa marcada `dono_da_plataforma=True`
     — a "empresa" que representa o próprio escritório, dona do sistema,
     isenta de licenciamento. Os admins que já existem hoje viram admins
     desenvolvedores automaticamente (porque passam a pertencer a essa
     empresa, via unidade).
  4. Vincula toda unidade com `empresa_id` NULL a essa empresa dona da
     plataforma (é o que faz o sistema continuar funcionando exatamente
     como antes para quem já usa).

NUNCA apaga dado, nunca mexe em unidade que já tenha empresa_id definido
(rodar de novo é seguro — idempotente).

Uso: python migrar_multitenant.py
"""
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db
from app.models import Empresa, Unidade

app = create_app()

with app.app_context():
    engine = db.engine
    inspetor = inspect(engine)
    tabelas_existentes = set(inspetor.get_table_names())

    print("=== Passo 1: tabelas novas (empresas, licencas, pagamentos) ===")
    faltando = [t for t in ("empresas", "licencas", "pagamentos") if t not in tabelas_existentes]
    if faltando:
        objetos = [db.metadata.tables[t] for t in faltando]
        db.metadata.create_all(bind=engine, tables=objetos)
        print(f"Criadas: {', '.join(faltando)}")
    else:
        print("Já existem.")

    print("\n=== Passo 2: coluna unidades.empresa_id ===")
    colunas_unidades = {c["name"] for c in inspetor.get_columns("unidades")}
    if "empresa_id" not in colunas_unidades:
        with engine.begin() as conexao:
            conexao.execute(text("ALTER TABLE `unidades` ADD COLUMN `empresa_id` INTEGER NULL"))
        print("Coluna criada (NULL por enquanto).")
    else:
        print("Já existe.")

    print("\n=== Passo 3: empresa dona da plataforma ===")
    empresa_plataforma = Empresa.query.filter_by(dono_da_plataforma=True).first()
    if empresa_plataforma is None:
        nome = input("Nome do escritório/empresa dona da plataforma (isenta de licença): ").strip()
        if not nome:
            print("Nome não pode ser vazio. Abortando.")
            sys.exit(1)
        empresa_plataforma = Empresa(nome=nome, dono_da_plataforma=True, ativa=True)
        db.session.add(empresa_plataforma)
        db.session.commit()
        print(f"Empresa \"{nome}\" criada (id={empresa_plataforma.id}), marcada como dona da plataforma.")
    else:
        print(f"Já existe: \"{empresa_plataforma.nome}\" (id={empresa_plataforma.id}).")

    print("\n=== Passo 4: vincular unidades sem empresa ===")
    unidades_sem_empresa = Unidade.query.filter(Unidade.empresa_id.is_(None)).all()
    if unidades_sem_empresa:
        print(f"{len(unidades_sem_empresa)} unidade(s) sem empresa: "
              f"{', '.join(u.codigo for u in unidades_sem_empresa)}")
        resposta = input(f"Vincular todas essas à empresa \"{empresa_plataforma.nome}\"? [s/N] ").strip().lower()
        if resposta == "s":
            for u in unidades_sem_empresa:
                u.empresa_id = empresa_plataforma.id
            db.session.commit()
            print("Vinculadas.")
        else:
            print("Nada vinculado — rode de novo quando quiser aplicar.")
    else:
        print("Nenhuma unidade sem empresa. Nada a fazer.")

    print("\nMigração concluída. Os usuários com papel 'admin' que pertencem a "
          f"unidades da empresa \"{empresa_plataforma.nome}\" agora são admins "
          "desenvolvedores (acesso a todas as empresas).")
