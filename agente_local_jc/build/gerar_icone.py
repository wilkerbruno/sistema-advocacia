"""
Gera build/icone.ico (ícone do próprio .exe, mostrado no instalador, no
atalho e na barra de tarefas) a partir de desenho simples (não depende
de nenhum arquivo de imagem versionado) — rodado uma vez no início do
build do GitHub Actions, antes do PyInstaller (ver
.github/workflows/build-agente-local.yml).
"""
import os

from PIL import Image, ImageDraw

AQUI = os.path.dirname(os.path.abspath(__file__))


def gerar():
    tamanho = 256
    imagem = Image.new("RGBA", (tamanho, tamanho), (0, 0, 0, 0))
    desenho = ImageDraw.Draw(imagem)
    desenho.ellipse((8, 8, tamanho - 8, tamanho - 8), fill=(30, 58, 95))  # azul-marinho, tom "documento jurídico"
    desenho.text((tamanho / 2 - 34, tamanho / 2 - 60), "J", fill="white",
                  font=_fonte_grande())
    caminho = os.path.join(AQUI, "icone.ico")
    imagem.save(caminho, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Ícone gerado em {caminho}")


def _fonte_grande():
    from PIL import ImageFont
    try:
        return ImageFont.truetype("arialbd.ttf", 140)
    except OSError:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", 140)
        except OSError:
            return ImageFont.load_default()


if __name__ == "__main__":
    gerar()
