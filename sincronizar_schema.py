"""
Sincroniza o schema do banco de dados com os modelos do sistema, criando
SÓ o que estiver faltando — nunca apaga tabela, nunca apaga/altera coluna
existente, nunca mexe em dado.

Uso (no servidor onde o MySQL está acessível, com o .env configurado):

    python sincronizar_schema.py            # mostra o que falta e aplica
    python sincronizar_schema.py --checar    # só mostra o que falta, não aplica nada

Isso substitui ficar rodando `criar_tabelas.py` inteiro de novo (que
reinsere os dados de seed) só para pegar uma tabela ou coluna nova de uma
atualização — rode este script sempre que atualizar o código do sistema.
"""
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, ".")
from app import create_app
from app.extensions import db

SOMENTE_CHECAR = "--checar" in sys.argv

app = create_app()

with app.app_context():
    engine = db.engine
    inspetor = inspect(engine)
    tabelas_existentes = set(inspetor.get_table_names())

    tabelas_modelo = db.metadata.tables
    tabelas_faltando = [nome for nome in tabelas_modelo if nome not in tabelas_existentes]

    print(f"Banco: {engine.url.database} em {engine.url.host}")
    print(f"Tabelas já existentes: {len(tabelas_existentes)}")
    print(f"Tabelas esperadas pelo modelo: {len(tabelas_modelo)}")
    print()

    if tabelas_faltando:
        print("Tabelas FALTANDO no banco:")
        for nome in tabelas_faltando:
            print(f"  - {nome}")
    else:
        print("Nenhuma tabela faltando.")
    print()

    # Colunas faltando em tabelas que já existem
    colunas_faltando = []  # [(tabela, coluna, tipo_sql, nullable)]
    for nome_tabela, tabela in tabelas_modelo.items():
        if nome_tabela in tabelas_faltando:
            continue  # tabela inteira já vai ser criada, não precisa checar coluna
        colunas_existentes = {c["name"] for c in inspetor.get_columns(nome_tabela)}
        for coluna in tabela.columns:
            if coluna.name not in colunas_existentes:
                tipo_sql = coluna.type.compile(dialect=engine.dialect)
                colunas_faltando.append((nome_tabela, coluna.name, tipo_sql, coluna.nullable))

    if colunas_faltando:
        print("Colunas FALTANDO em tabelas existentes:")
        for tabela, coluna, tipo, nullable in colunas_faltando:
            print(f"  - {tabela}.{coluna} ({tipo}, {'permite nulo' if nullable else 'obrigatório'})")
    else:
        print("Nenhuma coluna faltando nas tabelas existentes.")
    print()

    if SOMENTE_CHECAR:
        print("Modo --checar: nada foi alterado no banco.")
        sys.exit(0)

    if not tabelas_faltando and not colunas_faltando:
        print("Banco já está sincronizado com o código atual. Nada a fazer.")
        sys.exit(0)

    resposta = input("Aplicar as mudanças acima no banco? [s/N] ").strip().lower()
    if resposta != "s":
        print("Cancelado — nada foi alterado.")
        sys.exit(0)

    if tabelas_faltando:
        objetos = [tabelas_modelo[nome] for nome in tabelas_faltando]
        db.metadata.create_all(bind=engine, tables=objetos)
        print(f"{len(tabelas_faltando)} tabela(s) criada(s).")

    if colunas_faltando:
        with engine.begin() as conexao:
            for tabela, coluna, tipo, nullable in colunas_faltando:
                nulo_sql = "NULL" if nullable else "NOT NULL"
                sql = f"ALTER TABLE `{tabela}` ADD COLUMN `{coluna}` {tipo} {nulo_sql}"
                print(f"Executando: {sql}")
                conexao.execute(text(sql))
        print(f"{len(colunas_faltando)} coluna(s) adicionada(s).")

    print("\nSincronização concluída. Nenhum dado existente foi alterado ou removido.")
