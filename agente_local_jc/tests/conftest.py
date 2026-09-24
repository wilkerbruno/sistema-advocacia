"""
Permite `import login_local`, `import cliente_api` etc. nos testes desta
pasta sem precisar instalar o agente_local_jc como pacote — os módulos
são scripts soltos, pensados pra rodar com o cwd em agente_local_jc/ (ver
README.md), então só adiciona a pasta pai ao sys.path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
