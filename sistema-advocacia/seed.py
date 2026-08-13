"""
Script de inicialização do banco de dados.

Uso:
    python seed.py

Cria as tabelas (se não existirem) e, se o banco estiver vazio,
popula com dados de exemplo: 2 unidades, 1 usuário admin, 1 gestor
e 1 advogado por unidade, alguns clientes e processos de exemplo.

O usuário admin criado é:
    e-mail: admin@escritorio.com.br
    senha:  admin123          <-- TROQUE IMEDIATAMENTE após o primeiro login
"""
from datetime import date, timedelta
from app import create_app
from app.extensions import db
from app.models import Unidade, Usuario, Cliente, Processo, Andamento, Prazo, Lancamento, Tarefa

app = create_app()

with app.app_context():
    db.create_all()
    print("Tabelas criadas/verificadas com sucesso.")

    if Usuario.query.count() > 0:
        print("Banco já possui dados. Seed não será executado novamente.")
    else:
        # ---------- Unidades ----------
        matriz = Unidade(nome="Matriz São Paulo", codigo="SP-01", cidade="São Paulo", estado="SP",
                          endereco="Av. Paulista, 1000", telefone="(11) 4000-0000",
                          email="sp@escritorio.com.br", responsavel="Sócio Administrador")
        filial_rj = Unidade(nome="Filial Rio de Janeiro", codigo="RJ-01", cidade="Rio de Janeiro", estado="RJ",
                             endereco="Av. Rio Branco, 500", telefone="(21) 4000-0000",
                             email="rj@escritorio.com.br", responsavel="Sócio Filial RJ")
        db.session.add_all([matriz, filial_rj])
        db.session.flush()

        # ---------- Usuários ----------
        admin = Usuario(nome="Administrador Geral", email="admin@escritorio.com.br", papel="admin")
        admin.set_senha("admin123")

        gestor_sp = Usuario(nome="Gestora SP", email="gestora.sp@escritorio.com.br", papel="gestor",
                             unidade_id=matriz.id, oab="SP123456")
        gestor_sp.set_senha("123456")

        adv_sp = Usuario(nome="Advogado SP", email="advogado.sp@escritorio.com.br", papel="advogado",
                          unidade_id=matriz.id, oab="SP654321")
        adv_sp.set_senha("123456")

        adv_rj = Usuario(nome="Advogada RJ", email="advogada.rj@escritorio.com.br", papel="advogado",
                          unidade_id=filial_rj.id, oab="RJ111222")
        adv_rj.set_senha("123456")

        db.session.add_all([admin, gestor_sp, adv_sp, adv_rj])
        db.session.flush()

        # ---------- Clientes ----------
        cliente_sp = Cliente(tipo_pessoa="PF", nome="João da Silva", cpf_cnpj="123.456.789-00",
                              telefone="(11) 98888-0000", email="joao@cliente.com",
                              cidade="São Paulo", estado="SP", unidade_id=matriz.id, criado_por_id=gestor_sp.id)
        cliente_rj = Cliente(tipo_pessoa="PJ", nome="Comércio ABC Ltda", cpf_cnpj="12.345.678/0001-00",
                              telefone="(21) 97777-0000", email="contato@abc.com",
                              cidade="Rio de Janeiro", estado="RJ", unidade_id=filial_rj.id, criado_por_id=adv_rj.id)
        db.session.add_all([cliente_sp, cliente_rj])
        db.session.flush()

        # ---------- Processos de exemplo ----------
        processo_sp = Processo(
            numero_processo="1001234-56.2025.8.26.0100", area_direito="Cível",
            tipo_acao="Ação de cobrança", fase="Conhecimento", instancia="1ª instância",
            comarca="São Paulo", vara="3ª Vara Cível", polo_cliente="Autor",
            parte_contraria="Empresa XYZ Ltda", valor_causa=25000.00,
            data_distribuicao=date.today() - timedelta(days=60),
            descricao="Cobrança referente a contrato de prestação de serviços não pago.",
            cliente_id=cliente_sp.id, responsavel_id=adv_sp.id, unidade_id=matriz.id,
            criado_por_id=gestor_sp.id,
        )
        processo_rj = Processo(
            numero_processo="2002345-67.2025.8.19.0001", area_direito="Trabalhista",
            tipo_acao="Reclamação trabalhista", fase="Conhecimento", instancia="1ª instância",
            comarca="Rio de Janeiro", vara="10ª Vara do Trabalho", polo_cliente="Réu",
            parte_contraria="Ex-funcionário", valor_causa=18000.00,
            data_distribuicao=date.today() - timedelta(days=30),
            descricao="Defesa em reclamação trabalhista movida por ex-empregado.",
            cliente_id=cliente_rj.id, responsavel_id=adv_rj.id, unidade_id=filial_rj.id,
            criado_por_id=adv_rj.id,
        )
        db.session.add_all([processo_sp, processo_rj])
        db.session.flush()

        db.session.add_all([
            Andamento(processo_id=processo_sp.id, tipo="movimentacao",
                      descricao="Processo distribuído e citação expedida.", registrado_por_id=gestor_sp.id),
            Andamento(processo_id=processo_rj.id, tipo="movimentacao",
                      descricao="Contestação protocolada.", registrado_por_id=adv_rj.id),
        ])

        db.session.add_all([
            Prazo(processo_id=processo_sp.id, descricao="Apresentar réplica",
                  data_vencimento=date.today() + timedelta(days=4), prioridade="alta",
                  responsavel_id=adv_sp.id),
            Prazo(processo_id=processo_rj.id, descricao="Juntar documentos complementares",
                  data_vencimento=date.today() + timedelta(days=10), prioridade="normal",
                  responsavel_id=adv_rj.id),
        ])

        db.session.add_all([
            Lancamento(descricao="Honorário inicial - João da Silva", tipo="honorario", natureza="receita",
                       valor=3000.00, status="pendente", data_vencimento=date.today() + timedelta(days=15),
                       unidade_id=matriz.id, processo_id=processo_sp.id, cliente_id=cliente_sp.id,
                       criado_por_id=gestor_sp.id),
            Lancamento(descricao="Honorário contratual - Comércio ABC", tipo="honorario", natureza="receita",
                       valor=4500.00, status="pago", data_vencimento=date.today() - timedelta(days=5),
                       data_pagamento=date.today() - timedelta(days=3),
                       unidade_id=filial_rj.id, processo_id=processo_rj.id, cliente_id=cliente_rj.id,
                       criado_por_id=adv_rj.id),
        ])

        db.session.add_all([
            Tarefa(titulo="Elaborar réplica do processo de cobrança", prioridade="alta",
                   data_vencimento=date.today() + timedelta(days=3),
                   processo_id=processo_sp.id, responsavel_id=adv_sp.id, unidade_id=matriz.id,
                   criado_por_id=gestor_sp.id),
            Tarefa(titulo="Reunião com cliente Comércio ABC", prioridade="normal",
                   data_vencimento=date.today() + timedelta(days=7),
                   processo_id=processo_rj.id, responsavel_id=adv_rj.id, unidade_id=filial_rj.id,
                   criado_por_id=adv_rj.id),
        ])

        db.session.commit()

        print("\nDados de exemplo criados com sucesso!\n")
        print("Login do administrador (acesso a todas as unidades):")
        print("  e-mail: admin@escritorio.com.br | senha: admin123")
        print("\nLogin de gestor da unidade SP:")
        print("  e-mail: gestora.sp@escritorio.com.br | senha: 123456")
        print("\nLogin de advogado da unidade SP:")
        print("  e-mail: advogado.sp@escritorio.com.br | senha: 123456")
        print("\nLogin de advogada da unidade RJ:")
        print("  e-mail: advogada.rj@escritorio.com.br | senha: 123456")
        print("\n>>> TROQUE TODAS AS SENHAS PADRÃO ANTES DE USAR EM PRODUÇÃO <<<\n")
