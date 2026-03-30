"""
Erstellt AgentClaw.icns aus einem einfachen Python-generierten PNG.
Benötigt: pillow  (pip install pillow)
"""
import os
import subprocess
import struct
import zlib

def create_png(size=1024):
    """Minimales PNG mit grünem Claw-Symbol, ohne Pillow."""
    w = h = size
    pixels = []
    cx, cy = w // 2, h // 2
    r = int(w * 0.38)
    pad = int(w * 0.06)
    claw_w = int(w * 0.07)

    for y in range(h):
        row = []
        for x in range(w):
            dx, dy = x - cx, y - cy
            dist = (dx*dx + dy*dy) ** 0.5

            # Hintergrund — fast schwarz
            R, G, B, A = 5, 10, 6, 255

            # Äußerer Kreis (Ring)
            if r - claw_w <= dist <= r:
                R, G, B = 0, 230, 118   # --green #00e676

            # Drei Klauen (oben, links-unten, rechts-unten)
            import math
            for angle_deg in [90, 210, 330]:
                angle = math.radians(angle_deg)
                # Klaue: Linie von 40% bis 85% des Radius
                for t_pct in range(40, 86):
                    t = t_pct / 100
                    lx = cx + int(math.cos(angle) * r * t)
                    ly = cy - int(math.sin(angle) * r * t)
                    if abs(x - lx) <= claw_w // 2 and abs(y - ly) <= claw_w // 2:
                        R, G, B = 0, 230, 118

            row.extend([R, G, B, A])
        pixels.append(row)

    # PNG bauen
    def chunk(name, data):
        c = name + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)

    sig = b'\x89PNG\r\n\x1a\n'
    ihdr_data = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)  # RGB (kein Alpha für Einfachheit)

    # RGBA → RGB
    raw = b''
    for row in pixels:
        raw += b'\x00'  # filter type None
        for i in range(0, len(row), 4):
            raw += bytes(row[i:i+3])

    compressed = zlib.compress(raw, 9)

    ihdr = chunk(b'IHDR', ihdr_data)
    idat = chunk(b'IDAT', compressed)
    iend = chunk(b'IEND', b'')

    return sig + ihdr + idat + iend


def make_icns(png_path, icns_path):
    """Konvertiert PNG → icns via macOS iconutil."""
    iconset = icns_path.replace('.icns', '.iconset')
    os.makedirs(iconset, exist_ok=True)

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for s in sizes:
        dest = os.path.join(iconset, f'icon_{s}x{s}.png')
        subprocess.run(['sips', '-z', str(s), str(s), png_path, '--out', dest],
                       check=True, capture_output=True)
        # @2x
        if s <= 512:
            dest2 = os.path.join(iconset, f'icon_{s}x{s}@2x.png')
            subprocess.run(['sips', '-z', str(s*2), str(s*2), png_path, '--out', dest2],
                           check=True, capture_output=True)

    subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', icns_path], check=True)
    subprocess.run(['rm', '-rf', iconset])
    print(f'[Icon] Erstellt: {icns_path}')


if __name__ == '__main__':
    base = os.path.dirname(os.path.abspath(__file__))
    png  = os.path.join(base, 'AgentClaw_icon.png')
    icns = os.path.join(base, 'AgentClaw.icns')

    print('[Icon] Generiere PNG …')
    data = create_png(1024)
    with open(png, 'wb') as f:
        f.write(data)
    print(f'[Icon] PNG gespeichert: {png}')

    print('[Icon] Konvertiere zu .icns …')
    make_icns(png, icns)
    print('[Icon] Fertig!')
