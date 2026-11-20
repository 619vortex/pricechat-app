"""Google Play store art for Kiwi VPN, made from the app's own icon (ios-client/.../icon-1024.png, the same kiwi as the Android icon).
   python android-client/tools/store-assets.py <out dir>
Makes play-icon-512.png (512x512, 32-bit PNG) and play-feature-graphic-1024x500.png (1024x500, no transparency).
Claims on the graphic are only what the app does and is tested for: no kill-switch, leak-proof or speed claims."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ICON = ROOT / "ios-client/KiwiVPN/Assets.xcassets/AppIcon.appiconset/icon-1024.png"
GROUND, LIME, SOFT = (8, 12, 11), (164, 230, 58), (214, 222, 218)  # the app's Kiwi colours (ground, accent, soft text)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for name in (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit("No Segoe UI or Arial font found")


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    icon = Image.open(ICON).convert("RGBA")
    icon.resize((512, 512), Image.LANCZOS).save(out / "play-icon-512.png")

    g = Image.new("RGB", (1024, 500), GROUND)
    bird = icon.resize((420, 420), Image.LANCZOS)
    g.paste(bird, (30, 40), bird)
    d = ImageDraw.Draw(g)
    d.text((470, 120), "KIWI VPN", font=font(84, True), fill=LIME)
    # no other companies' app names or logos on store art (trademarks): those belong in the description text only
    d.text((474, 232), "Choose which apps use the VPN,", font=font(34), fill=SOFT)
    d.text((474, 278), "or every app on your phone.", font=font(34), fill=SOFT)
    d.text((474, 324), "Encrypted with WireGuard®.", font=font(34), fill=SOFT)
    d.text((474, 392), "by Price Chat", font=font(26), fill=(140, 150, 146))
    g.save(out / "play-feature-graphic-1024x500.png")
    print("written to", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "android-client/store-assets")
