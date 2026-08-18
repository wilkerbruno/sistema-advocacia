"""
Sincroniza o schema do banco de dados com os modelos do sistema, criando
SÓ o que estiver faltando — nunca apaga tabela, nunca apaga/estreita coluna
existente, nunca mexe em dado.

Uso (no servidor onde o MySQL está acessível, com o .env configurado):

    python sincronizar_schema.py            # mostra o que falta e aplica
    python sincronizar_schema.py --checar    # só mostra o que falta, não aplica nada

Isso substitui ficar rodando `criar_tabelas.py` inteiro de novo (que
reinsere os dados de seed) só para pegar uma tabela ou coluna nova de uma
atualização — rode este script sempre que atualizar o código do sistema.

Exceção única e deliberada à regra "nunca altera coluna existente": quando
o modelo Python torna uma coluna que já existe OPCIONAL (nullable=True) e o
banco ainda a tem como obrigatória (NOT NULL) — ex.: MapaEstadoTPU.codigo_tpu
passou a aceitar nulo quando o mapeamento é só por texto (ver
app/utils/estado_processual_engine.py). Essa é a ÚNICA direção de ALTER que
este script executa: AFROUXAR uma restrição NUNCA apaga nem corrompe dado
existente (toda linha já cadastrada já satisfazia "obrigatório", então
continua satisfazendo "opcional" sem qualquer mudança de valor) — o
oposto (tornar uma coluna opcional em obrigatória) exigiria decidir o que
fazer com linhas que já estão nulas, e ESSE caso continua fora do escopo
deste script de propósito.
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

    # Colunas que EXISTEM nos dois lados, mas o modelo agora aceita nulo e o
    # banco ainda exige valor — única forma de ALTER que este script faz
    # (ver docstring do módulo: afrouxar restrição nunca perde/corrompe dado).
    colunas_para_afrouxar = []  # [(tabela, coluna, tipo_sql)]
    for nome_tabela, tabela in tabelas_modelo.items():
        if nome_tabela in tabelas_faltando:
            continue
        colunas_existentes = {c["name"]: c for c in inspetor.get_columns(nome_tabela)}
        for coluna in tabela.columns:
            info = colunas_existentes.get(coluna.name)
            if info is None:
                continue  # já contabilizada acima em colunas_faltando
            if coluna.nullable and not info["nullable"]:
                tipo_sql = coluna.type.compile(dialect=engine.dialect)
                colunas_para_afrouxar.append((nome_tabela, coluna.name, tipo_sql))

    if colunas_para_afrouxar:
        print("Colunas que o modelo tornou OPCIONAIS mas o banco ainda exige valor (só afrouxa, nunca restringe):")
        for tabela, coluna, tipo in colunas_para_afrouxar:
            print(f"  - {tabela}.{coluna} -> passa a permitir nulo")
    print()

    if SOMENTE_CHECAR:
        print("Modo --checar: nada foi alterado no banco.")
        sys.exit(0)

    if not tabelas_faltando and not colunas_faltando and not colunas_para_afrouxar:
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

    if colunas_para_afrouxar:
        with engine.begin() as conexao:
            for tabela, coluna, tipo in colunas_para_afrouxar:
                sql = f"ALTER TABLE `{tabela}` MODIFY COLUMN `{coluna}` {tipo} NULL"
                print(f"Executando: {sql}")
                conexao.execute(text(sql))
        print(f"{len(colunas_para_afrouxar)} coluna(s) afrouxada(s) (passam a aceitar nulo).")

    print("\nSincronização concluída. Nenhum dado existente foi alterado ou removido.")
