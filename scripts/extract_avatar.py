#!/usr/bin/env python3
"""Copy Jade's avatar (the Live2D model, its texture, the runtime and the avatar code) out of a built Jade Vault app
page into jade-py's dashboard (src/jade/web/avatar/). Run it again when the app's avatar changes:

    python scripts/extract_avatar.py ../jade-vault/android/proj/assets/index.html
"""
import base64
import gzip
import re
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "../jade-vault/android/proj/assets/index.html").read_text()
out = Path(__file__).resolve().parent.parent / "src/jade/web/avatar"
out.mkdir(parents=True, exist_ok=True)


def block(i: str) -> str:
    m = re.search(r'<script[^>]*id="%s"[^>]*>' % i, src)
    if not m:
        sys.exit(f"no <script id={i}> in that page: is it a built Jade Vault app?")
    return src[m.end():src.index("</script>", m.end())]


(out / "pixi.min.js").write_text(block("lib-pixi"))
(out / "live2dcubismcore.min.js").write_text(block("lib-cubism-core"))
(out / "cubism4.min.js").write_text(block("lib-cubism4"))
(out / "jadecat.model3.json").write_text(block("l2d-model").strip())
(out / "jadecat.physics3.json").write_text(block("l2d-physics").strip())
(out / "jadecat.cdi3.json").write_text(block("l2d-cdi").strip())
(out / "jadecat.moc3").write_bytes(gzip.decompress(base64.b64decode(re.sub(r"\s+", "", block("l2d-moc")))))
tex = block("l2d-tex").strip()
mime, data = re.match(r"data:image/(\w+);base64,(.*)", tex, re.S).groups()
(out / f"texture.{mime}").write_bytes(base64.b64decode(data))

# the avatar code: themes, appearance and the Jade module, loading its files from /ui/avatar/ instead of the page
a = src.index("const JADE_THEMES = Object.freeze")
b = src.index("/* =====================================================================\n   CRON", a)
code = src[a:b]
code = code.replace("const b64gz = async (id) => {", "const b64gz = async (id) => { if (id === 'l2d-moc') return (await fetch(AV + 'jadecat.moc3')).arrayBuffer();", 1)
code = code.replace("const jsonOf = (id) => JSON.parse(document.getElementById(id).textContent);",
                    "const AVF = { 'l2d-model': 'jadecat.model3.json', 'l2d-physics': 'jadecat.physics3.json', 'l2d-cdi': 'jadecat.cdi3.json' };\n"
                    "  const jsonOf = async (id) => (await fetch(AV + AVF[id])).json();", 1)
code = code.replace("'jadecat.model3.json': jsonOf('l2d-model'), 'jadecat.physics3.json': jsonOf('l2d-physics'), 'jadecat.cdi3.json': jsonOf('l2d-cdi'),",
                    "'jadecat.model3.json': await jsonOf('l2d-model'), 'jadecat.physics3.json': await jsonOf('l2d-physics'), 'jadecat.cdi3.json': await jsonOf('l2d-cdi'),", 1)
code = code.replace("img.src = document.getElementById('l2d-tex').textContent.trim();", f"img.src = AV + 'texture.{mime}';", 1)
for need in ("AV + 'jadecat.moc3'", "await jsonOf('l2d-model')", f"AV + 'texture.{mime}'"):
    assert need in code, f"the app's avatar code changed: could not patch {need!r}"
# the theme controls exist only while the dashboard's colour picker is open
code = re.sub(r"\$\('(#\w+)'\)\.hidden = ([^;]+);", lambda m: "{ const _e = $('%s'); if (_e) _e.hidden = %s; }" % (m.group(1), m.group(2)), code)
# the pickers at the end run against the dashboard's own settings panel, if it has one
tail = code.index("renderJadeThemePicker();\nrenderJadeAppearancePicker();")
code = code[:tail]
prelude = """/* Jade's avatar, taken from the Jade Vault app by scripts/extract_avatar.py (do not edit here; re-run that).
   Needs window.S (settings), $, esc and saveSettings from the page, and pixi + Cubism loaded first. */
const AV = '/ui/avatar/';
"""
(out / "avatar.js").write_text(prelude + code + "\nwindow.Jade = Jade; Object.assign(window, { JADE_THEMES, resolveJadeTheme, applyJadeTheme, renderJadeThemePicker, renderJadeAppearancePicker });\n")

css = "".join(m.group(1) for m in re.finditer(r"<style[^>]*>(.*?)</style>", src, re.S))
rules, depth, buf = [], 0, ""
for ch in css:
    buf += ch
    if ch == "{":
        depth += 1
    elif ch == "}":
        depth -= 1
        if depth == 0:
            rules.append(buf.strip())
            buf = ""
keep = [r for r in rules if re.search(r"jade-|gem-theme|theme-color-input|appearance", r.split("{")[0])
        and not re.search(r"#app\.|body\.kb|\.m-screen|\.chat-bar|\.drawer", r.split("{")[0])]
(out / "avatar.css").write_text("/* from the Jade Vault app (scripts/extract_avatar.py) */\n" + "\n".join(keep) + "\n")
print("wrote", ", ".join(sorted(p.name for p in out.iterdir())))
