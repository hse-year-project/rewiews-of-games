import matplotlib.pyplot as plt
from matplotlib import font_manager

CJK_FONTS = ("Noto Sans CJK SC", "WenQuanYi Zen Hei", "Microsoft YaHei")


def use_cjk_font():
    """Let matplotlib draw Chinese text (otherwise it shows boxes).

    Needs a CJK font: Arch noto-fonts-cjk, Ubuntu/WSL fonts-noto-cjk. Installed it after
    matplotlib? Delete ~/.cache/matplotlib so matplotlib finds it.
    """
    installed = {f.name for f in font_manager.fontManager.ttflist}
    plt.rcParams["font.family"] = ["DejaVu Sans", *[f for f in CJK_FONTS if f in installed][:1]]
