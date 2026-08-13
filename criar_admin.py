pyt"""
Cria um usuário administrador (acesso a todas as unidades) ou promove um
usuário já existente a admin.

Modo interativo — o script pergunta nome, e-mail e senha:

    python criar_admin.py

Modo direto (não interativo, útil em deploy automatizado):

    python criar_admin.py --nome "Seu Nome" --email voce@escritorio.com.br --senha "senha-forte"

Se o e-mail informado já existir no banco, o script pergunta se você quer
promover esse usuário a admin (e opcionalmente trocar a senha) em vez de
tentar criar um duplicado.

Este script é seguro para rodar quantas vezes forem necessárias — ele não
mexe em nenhum outro dado do banco.
"""
import argparse
import getpass
import sys

from app import create_app
from app.extensions import db
from app.models import Usuario


def parse_args():
    p = argparse.ArgumentParser(description="Cria ou promove um usuário administrador do JusControl.")
    p.add_argument("--nome", help="Nome completo do administrador")
    p.add_argument("--email", help="E-mail de login")
    p.add_argument("--senha", help="Senha (se omitida no modo interativo, será pedida com input oculto)")
    return p.parse_args()


def perguntar(texto, obrigatorio=True):
    while True:
        valor = input(texto).strip()
        if valor or not obrigatorio:
            return valor
        print("Este campo é obrigatório.")


def main():
    args = parse_args()
    modo_interativo = not (args.nome and args.email and args.senha)

    app = create_app()
    try:
        with app.app_context():
            db.session.execute(db.text("SELECT 1"))
    except Exception as e:
        print(f"\nNão foi possível conectar ao banco configurado em DATABASE_URL: {e}")
        print("Confira o .env e se este host tem rede até o servidor MySQL "
              "(rode este script a partir de uma máquina/terminal que alcance o EasyPanel).")
        sys.exit(1)

    with app.app_context():
        if modo_interativo:
            print("=== Criar usuário administrador — JusControl ===\n")
            nome = args.nome or perguntar("Nome completo: ")
            email = (args.email or perguntar("E-mail de login: ")).strip().lower()
            senha = args.senha or getpass.getpass("Senha (mín. 6 caracteres): ")
            confirmacao = args.senha or getpass.getpass("Confirme a senha: ")
            if not args.senha and senha != confirmacao:
                print("\nAs senhas não conferem. Nada foi alterado.")
                sys.exit(1)
        else:
            nome, email, senha = args.nome, args.email.strip().lower(), args.senha

        if len(senha) < 6:
            print("\nA senha precisa ter pelo menos 6 caracteres. Nada foi alterado.")
            sys.exit(1)

        existente = Usuario.query.filter_by(email=email).first()

        if existente:
            print(f"\nJá existe um usuário com o e-mail '{email}' (papel atual: {existente.papel}).")
            if modo_interativo:
                resposta = perguntar("Deseja promovê-lo a admin e atualizar a senha? [s/N]: ", obrigatorio=False)
                confirmar = resposta.lower() in ("s", "sim", "y", "yes")
            else:
                confirmar = True  # modo não interativo: já veio com intenção explícita

            if not confirmar:
                print("Nada foi alterado.")
                sys.exit(0)

            existente.papel = "admin"
            existente.unidade_id = None  # admin não fica preso a uma unidade
            existente.ativo = True
            existente.nome = nome or existente.nome
            existente.set_senha(senha)
            db.session.commit()
            print(f"\nUsuário '{email}' promovido a admin e senha atualizada com sucesso.")
        else:
            admin = Usuario(nome=nome, email=email, papel="admin", ativo=True)
            admin.set_senha(senha)
            db.session.add(admin)
            db.session.commit()
            print(f"\nUsuário admin criado com sucesso: {email}")

        print("Este usuário já pode fazer login e vai enxergar todas as unidades do sistema.")


if __name__ == "__main__":
    main()
