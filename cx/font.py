"""字形哈希适配自 GPL-3.0-only 的 chaoxing；来源与作者见 NOTICE.md。"""
import base64
import hashlib
import json
import re
import unicodedata
from io import BytesIO
from pathlib import Path

def glyph_hash(glyph):
    if glyph.numberOfContours <= 0:
        return ''
    values = []
    start = 0
    for end in glyph.endPtsOfContours:
        for i in range(start, end + 1):
            x, y = glyph.coordinates[i]
            values.append('%s%s%s' % (x, y, glyph.flags[i] & 1))
        start = end + 1
    return hashlib.md5(''.join(values).encode()).hexdigest()

def decoder(html):
    style = re.search(r'<style\b[^>]*\bid=[\"\x27]cxSecretStyle[\"\x27][^>]*>(.*?)</style>', html, re.S)
    if not style:
        return lambda text: text
    data = re.search(r'base64,([A-Za-z0-9+/=\s]+)', style.group(1))
    if not data:
        raise ValueError('页面未提供有效字体数据')
    from fontTools.ttLib import TTFont
    known = json.loads((Path(__file__).parent / 'resource/font_map_table.json').read_text())
    reverse = {digest: name for name, digest in known.items()}
    translations = {}
    with TTFont(BytesIO(base64.b64decode(data.group(1))), lazy=False) as font:
        glyphs = font['glyf']
        for point, name in (font.getBestCmap() or {}).items():
            target = reverse.get(glyph_hash(glyphs[name]))
            if target and target.startswith('uni'):
                translations[point] = chr(int(target[3:], 16))
    def decode(text):
        result = text.translate(translations)
        if any('\ue000' <= c <= '\uf8ff' for c in result):
            raise ValueError('部分加密字形无法还原，停止自动作答')
        return ''.join(unicodedata.normalize('NFKC', c) if '\u2f00' <= c <= '\u2fdf' else c for c in result)
    return decode
