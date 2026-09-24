"""
Testes do login local (OAB + senha) — a parte SEM interface gráfica de
login_local.py, a mais sensível por guardar a lógica de hash/verificação
de senha (ver docstring do módulo pra que serve isto e por que é
100% local). Não testa `tela_bloqueio.py` (GUI, precisa de um display —
fora de escopo de teste automatizado, mesmo espírito de `config_gui.py`
nunca ter sido coberto por teste).
"""
import login_local


def test_hash_e_verificacao_roundtrip():
    hash_hex, salt_hex = login_local.gerar_hash("minhaSenh@123")
    assert login_local.verificar_senha("minhaSenh@123", hash_hex, salt_hex) is True


def test_senha_errada_nao_bate():
    hash_hex, salt_hex = login_local.gerar_hash("senhaCorreta")
    assert login_local.verificar_senha("senhaErrada", hash_hex, salt_hex) is False


def test_hash_e_salt_diferentes_a_cada_chamada():
    """Sal aleatório — duas chamadas pra MESMA senha nunca geram o mesmo
    hash (proteção contra rainbow table, mesmo sem reaproveitar isso pra
    comparação: cada verificação usa o sal salvo junto)."""
    hash1, salt1 = login_local.gerar_hash("mesma-senha")
    hash2, salt2 = login_local.gerar_hash("mesma-senha")
    assert salt1 != salt2
    assert hash1 != hash2
    assert login_local.verificar_senha("mesma-senha", hash1, salt1) is True
    assert login_local.verificar_senha("mesma-senha", hash2, salt2) is True


def test_verificar_senha_com_campos_vazios_ou_ausentes_nunca_bate():
    assert login_local.verificar_senha("", "", "") is False
    assert login_local.verificar_senha("senha", None, None) is False
    assert login_local.verificar_senha(None, "abc", "def") is False


def test_verificar_senha_com_hex_invalido_nao_quebra():
    """hash/salt corrompidos no config.json (edição manual, versão antiga
    incompatível etc.) nunca devem derrubar o agente com uma exceção —
    tratado como "não bate", igual senha errada."""
    assert login_local.verificar_senha("qualquer", "não-é-hex-válido", "também-não") is False


def test_login_local_configurado():
    assert login_local.login_local_configurado({}) is False
    assert login_local.login_local_configurado({"oab_local": "123456/SP"}) is False  # falta hash/salt
    assert login_local.login_local_configurado(
        {"oab_local": "123456/SP", "oab_senha_hash": "abc", "oab_senha_salt": "def"}) is True


def test_validar_senha_nova():
    assert login_local.validar_senha_nova("12345") is not None  # curta demais (< 6)
    assert login_local.validar_senha_nova("") is not None
    assert login_local.validar_senha_nova("123456") is None
    assert login_local.validar_senha_nova("senha-bem-mais-longa") is None
