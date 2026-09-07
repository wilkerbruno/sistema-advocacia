"""
Gera as imagens ("Assets") exigidas pelo pacote MSIX — PENDENCIAS.md,
seção -70. Reaproveita o MESMO desenho do ícone do instalador tradicional
(círculo azul-marinho com "J" branco, ver gerar_icone.py), só que nos
tamanhos exatos que o AppxManifest.xml do MSIX exige, e SEM o fundo
transparente virar quadrado preto (a Store rejeita/mostra errado se o
canal alfa não for tratado do jeito certo em cada asset).

Rodado automaticamente pelo GitHub Actions (ver
.github/workflows/build-agente-local.yml), antes do makeappx.exe empacotar
o MSIX — igual ao gerar_icone.py já é rodado antes do PyInstaller.

Cada asset é exigido pela Store por um motivo específico:
  - Square44x44Logo.png  (44x44)   -> ícone do app na lista de programas/Início.
  - Square150x150Logo.png (150x150) -> "tile" médio do menu Iniciar.
  - StoreLogo.png        (50x50)   -> ícone mostrado dentro da própria loja.

Pra manter simples (pilotos/uso interno, não uma vitrine chamativa na
Store), não geramos tiles grandes/wide opcionais — o manifesto não os
referencia, então não precisam existir.
"""
import os

from PIL import Image, ImageDraw, ImageFont

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_ASSETS = os.path.join(AQUI, "Assets")

# (nome do arquivo, tamanho em pixels, proporção do texto "J" dentro do tamanho)
ASSETS = [
    ("Square44x44Logo.png", 44),
    ("Square150x150Logo.png", 150),
    ("StoreLogo.png", 50),
]


def _fonte(tamanho_px):
    try:
        return ImageFont.truetype("arialbd.ttf", tamanho_px)
    except OSError:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", tamanho_px)
        except OSError:
            return ImageFont.load_default()


def _gerar_um(nome_arquivo, tamanho):
    # Fundo TOTALMENTE opaco (a Store recomenda evitar transparência nos
    # ícones "quadrados" — em alguns temas do Windows um fundo transparente
    # vira um retângulo branco feio atrás do círculo). Usamos a mesma cor
    # de fundo do círculo do ícone tradicional, só que preenchendo o
    # quadrado inteiro, e desenhando um círculo levemente menor por cima
    # pra manter a mesma identidade visual.
    cor_fundo = (18, 34, 56)      # azul-marinho mais escuro, de fundo
    cor_circulo = (30, 58, 95)    # mesmo tom do ícone tradicional
    imagem = Image.new("RGBA", (tamanho, tamanho), cor_fundo + (255,))
    desenho = ImageDraw.Draw(imagem)
    margem = max(2, round(tamanho * 0.06))
    desenho.ellipse((margem, margem, tamanho - margem, tamanho - margem), fill=cor_circulo)

    letra = "J"
    tamanho_fonte = round(tamanho * 0.55)
    fonte = _fonte(tamanho_fonte)
    caixa = desenho.textbbox((0, 0), letra, font=fonte)
    largura_texto = caixa[2] - caixa[0]
    altura_texto = caixa[3] - caixa[1]
    x = (tamanho - largura_texto) / 2 - caixa[0]
    y = (tamanho - altura_texto) / 2 - caixa[1]
    desenho.text((x, y), letra, fill="white", font=fonte)

    caminho = os.path.join(PASTA_ASSETS, nome_arquivo)
    imagem.save(caminho)
    print(f"Asset gerado: {caminho} ({tamanho}x{tamanho})")


def gerar():
    os.makedirs(PASTA_ASSETS, exist_ok=True)
    for nome_arquivo, tamanho in ASSETS:
        _gerar_um(nome_arquivo, tamanho)


if __name__ == "__main__":
    gerar()
