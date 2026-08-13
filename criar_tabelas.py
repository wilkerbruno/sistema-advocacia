"""
Cria (ou atualiza) todas as tabelas do sistema no banco MySQL informado.

Uso mais simples — usa a conexão já configurada no .env:

    python criar_tabelas.py

Ou apontando para outro banco sem mexer no .env:

    python criar_tabelas.py --database-url "mysql://usuario:senha@host:porta/nome_banco"

O script:
  1. Testa a conexão com o MySQL antes de tentar qualquer coisa.
  2. Cria o banco de dados se ele ainda não existir (CREATE DATABASE ... utf8mb4).
  3. Cria todas as tabelas do modelo de dados (db.create_all()) — é seguro
     rodar mais de uma vez: tabelas já existentes não são recriadas nem
     perdem dados.
  4. Popula tabelas de referência que o sistema espera encontrar já
     preenchidas (mapa TPU -> estado de negócio e motor de próxima ação,
     conforme seções 6 e 7.1 do briefing), só na primeira execução.
  5. Imprime um relatório com todas as tabelas criadas.

Este script NÃO apaga nem recria tabelas existentes — ele é aditivo.
Para mudanças de schema em produção depois da primeira carga de dados,
o caminho correto é uma ferramenta de migração (ex: Flask-Migrate/Alembic),
não este script.
"""
import argparse
import sys

import pymysql
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from config import Config, normalizar_url_mysql


def parse_args():
    p = argparse.ArgumentParser(description="Cria as tabelas do JusControl no MySQL.")
    p.add_argument(
        "--database-url",
        help="String de conexão MySQL. Se omitida, usa DATABASE_URL do .env / ambiente.",
        default=None,
    )
    p.add_argument(
        "--sem-seed-referencia",
        action="store_true",
        help="Não popular as tabelas de referência (mapa TPU e regras de próxima ação).",
    )
    return p.parse_args()


def extrair_partes_conexao(url_sqlalchemy):
    """
    Extrai usuário, senha, host, porta e nome do banco a partir de uma URL
    já normalizada no formato mysql+pymysql://user:pass@host:port/db
    (usado para o CREATE DATABASE, que precisa de uma conexão sem
    'USE <banco>' embutido, caso o banco ainda não exista).
    """
    sem_driver = url_sqlalchemy.replace("mysql+pymysql://", "", 1)
    credenciais, resto = sem_driver.split("@", 1)
    usuario, senha = credenciais.split(":", 1)
    host_porta, caminho = resto.split("/", 1)
    if ":" in host_porta:
        host, porta = host_porta.split(":", 1)
    else:
        host, porta = host_porta, "3306"
    banco = caminho.split("?", 1)[0]
    return dict(usuario=usuario, senha=senha, host=host, porta=int(porta), banco=banco)


def garantir_banco_existe(partes):
    """Conecta sem selecionar banco e cria o schema se ele não existir."""
    conexao = pymysql.connect(
        host=partes["host"], port=partes["porta"],
        user=partes["usuario"], password=partes["senha"],
        charset="utf8mb4",
    )
    try:
        with conexao.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{partes['banco']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conexao.commit()
        print(f"Banco de dados '{partes['banco']}' verificado/criado com sucesso.")
    finally:
        conexao.close()


def popular_tabelas_referencia():
    """
    Seed das tabelas de referência que o motor de tradução de estados e o
    motor de próxima ação precisam ter preenchidas (seções 6 e 7.1 do
    briefing). Só insere o que ainda não existe — seguro rodar sempre.
    """
    from app.models import MapaEstadoTPU, RegraProximaAcao

    mapa_estados = [
        ("Distribuído", "Distribuido"),
        ("Aguardando citação", "Aguardando_citacao"),
        ("Citado / prazo de resposta", "Citado_prazo_resposta"),
        ("Em instrução", "Em_instrucao"),
        ("Aguardando sentença", "Aguardando_sentenca"),
        ("Sentenciado", "Sentenciado"),
        ("Em fase recursal", "Em_fase_recursal"),
        ("Trânsito em julgado", "Transito_em_julgado"),
        ("Em cumprimento/execução", "Em_cumprimento_execucao"),
        ("Arquivado", "Arquivado"),
        ("Citado em execução", "Citado_em_execucao"),
        ("Garantido o juízo", "Garantido_o_juizo"),
        ("Embargos opostos", "Embargos_opostos"),
        ("Auto de infração", "Auto_de_infracao"),
        ("Recurso administrativo pendente", "Recurso_administrativo_pendente"),
    ]
    for descricao, estado in mapa_estados:
        codigo = estado[:20]
        if not MapaEstadoTPU.query.filter_by(codigo_tpu=codigo).first():
            db.session.add(MapaEstadoTPU(codigo_tpu=codigo, descricao_tpu=descricao, estado_negocio=estado))

    regras = [
        ("Citação / intimação para contestar", "Elaborar contestação", 15, "dias_uteis", None, "advogado"),
        ("Despacho de emenda à inicial", "Emendar a inicial", 15, "dias_uteis", None, "advogado"),
        ("Intimação de sentença", "Decidir sobre apelação e comunicar cliente", 15, "dias_uteis", None, "advogado"),
        ("Intimação de decisão interlocutória", "Avaliar agravo de instrumento", 15, "dias_uteis", None, "advogado"),
        ("Intimação para especificar provas", "Manifestar sobre provas", None, "data_evento", "conforme despacho", "advogado"),
        ("Designação de audiência", "Preparar audiência e intimar cliente e testemunhas", None, "data_evento", "data da audiência", "advogado"),
        ("Intimação de laudo pericial", "Manifestar sobre o laudo", 15, "dias_uteis", None, "advogado"),
        ("Início de cumprimento de sentença", "Pagar ou impugnar", 15, "dias_uteis", None, "advogado"),
        ("Citação em execução fiscal", "Garantir o juízo ou avaliar exceção de pré-executividade", 5, "dias_uteis", None, "advogado"),
        ("Garantia do juízo em execução fiscal", "Opor embargos à execução", 30, "dias_uteis", None, "advogado"),
        ("Auto de infração ambiental ou da ANM", "Apresentar defesa administrativa", 20, "dias_corridos", "20 a 30 dias, conforme órgão", "advogado"),
        ("Certidão de decurso de prazo sem manifestação", "Alerta vermelho ao contratante", 0, "data_evento", "imediato", "gestor"),
    ]
    for ato, acao, prazo_dias, unidade, obs, papel in regras:
        if not RegraProximaAcao.query.filter_by(ato_capturado=ato).first():
            db.session.add(RegraProximaAcao(
                ato_capturado=ato, acao_exigida=acao, prazo_base_dias=prazo_dias,
                unidade_prazo=unidade, observacao_prazo=obs, responsavel_sugerido_papel=papel,
            ))

    db.session.commit()
    print(f"Tabelas de referência populadas: {len(mapa_estados)} estados TPU, {len(regras)} regras de próxima ação.")


def main():
    args = parse_args()

    url_bruta = args.database_url or Config.SQLALCHEMY_DATABASE_URI
    url = normalizar_url_mysql(url_bruta)

    print(f"Conectando em: {url.split('@')[-1]}")  # nunca imprime usuário/senha no log

    try:
        partes = extrair_partes_conexao(url)
        garantir_banco_existe(partes)
    except Exception as e:
        print(f"\nERRO ao conectar/criar o banco: {e}")
        print("Verifique host, porta, usuário e senha, e se o servidor MySQL está acessível "
              "a partir de onde este script está rodando (regras de firewall/rede do EasyPanel).")
        sys.exit(1)

    app = create_app()
    app.config["SQLALCHEMY_DATABASE_URI"] = url

    with app.app_context():
        db.create_all()

        inspetor = inspect(db.engine)
        tabelas = sorted(inspetor.get_table_names())
        print(f"\n{len(tabelas)} tabelas presentes no banco após a criação:")
        for t in tabelas:
            print(f"  - {t}")

        if not args.sem_seed_referencia:
            popular_tabelas_referencia()

    print("\nConcluído. O sistema já pode ser apontado para este banco (DATABASE_URL no .env).")


if __name__ == "__main__":
    main()
