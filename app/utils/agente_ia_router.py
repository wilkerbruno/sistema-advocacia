"""
Roteador de provedor do motor de IA — usado tanto pelo Agente de IA de
portfólio (app/routes/agente_ia.py) quanto pela Análise de processo
(app/utils/analise_processo_ia.py), pra que nenhum dos dois precise saber
qual provedor está por trás da resposta.

Cada empresa (tenant) escolhe, em "Minhas Integrações"
(app/routes/integracoes.py):
  - `Empresa.PROVEDOR_IA_LOCAL` (padrão): usa o modelo local gratuito do
    próprio servidor (app/utils/ia_local.py) — sem custo, sem chave, mas
    modelo pequeno.
  - `Empresa.PROVEDOR_IA_CLAUDE_BYOK`: usa a API da Anthropic (Claude) com
    a CHAVE PRÓPRIA da empresa (app/utils/claude_api.py) — a empresa paga
    a Anthropic diretamente pelo uso.

A chave, quando cadastrada, fica cifrada no banco (app/utils/cofre.py,
mesmo mecanismo Fernet já usado para SenhaProcesso) — nunca em texto puro,
e nunca reexibida depois de salva (só "chave cadastrada: sim/não").
"""
from app.models import Empresa
from app.utils import ia_local, claude_api, cofre


class ProvedorIAIndisponivelError(Exception):
    """Erro amigável — cobre tanto 'modelo local não baixado' quanto 'chave
    Claude não cadastrada/inválida/recusada pela Anthropic'. Quem chama
    nunca precisa diferenciar os dois casos."""


def _usa_claude_byok(empresa):
    return bool(empresa) and empresa.agente_ia_provedor_efetivo == Empresa.PROVEDOR_IA_CLAUDE_BYOK


def provedor_disponivel(empresa):
    """
    Checagem rápida (sem chamar rede) pra exibir aviso nas telas antes de
    deixar o usuário tentar enviar uma mensagem. Para o provedor local, só
    olha se o arquivo do modelo existe (ver ia_local.modelo_disponivel);
    para Claude BYOK, só olha se existe uma chave cadastrada — não valida
    se ela ainda é aceita pela Anthropic (isso só se sabe na hora de usar,
    ou no botão "testar chave" da tela de Integrações).
    """
    if _usa_claude_byok(empresa):
        return bool(empresa.agente_ia_claude_chave_cifrada)
    return ia_local.modelo_disponivel()


def descricao_provedor(empresa):
    """Texto curto pra exibir na UI (ex: rodapé do chat), pra deixar claro
    pra quem está usando qual provedor está ativo no momento."""
    if _usa_claude_byok(empresa):
        modelo = (empresa.agente_ia_claude_modelo or claude_api.MODELO_PADRAO)
        return f"API do Claude (chave própria da empresa) — modelo {modelo}"
    return "Modelo de IA local (grátis, roda no próprio servidor)"


def gerar_resposta(empresa, system, mensagens_api, max_tokens=None):
    """
    Gera a resposta usando o provedor configurado para `empresa`. Levanta
    ProvedorIAIndisponivelError com mensagem amigável em qualquer cenário
    de falha (modelo local não baixado, chave Claude ausente/cofre não
    configurado/chave inválida/recusada, erro de rede).
    """
    if _usa_claude_byok(empresa):
        if not empresa.agente_ia_claude_chave_cifrada:
            raise ProvedorIAIndisponivelError(
                "Esta empresa está configurada para usar a API do Claude com chave própria, mas "
                "nenhuma chave foi cadastrada ainda. Cadastre em \"Minhas Integrações\" (menu do "
                "administrador) ou volte a usar o modelo local gratuito."
            )
        try:
            chave = cofre.decifrar_segredo(empresa.agente_ia_claude_chave_cifrada)
        except (cofre.CofreNaoConfiguradoError, ValueError) as e:
            raise ProvedorIAIndisponivelError(str(e)) from e
        try:
            return claude_api.gerar_resposta(
                system, mensagens_api, api_key=chave,
                modelo=empresa.agente_ia_claude_modelo, max_tokens=max_tokens,
            )
        except claude_api.ClaudeIndisponivelError as e:
            raise ProvedorIAIndisponivelError(str(e)) from e

    try:
        return ia_local.gerar_resposta(system, mensagens_api, max_tokens=max_tokens)
    except ia_local.ModeloIndisponivelError as e:
        raise ProvedorIAIndisponivelError(str(e)) from e
