# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "playwright"]
# ///
"""Render the three-state README hero from SVG templates and benchmark evidence.

Run: uv run docs/assets/render_hero.py
Uses installed Chrome on macOS, otherwise Playwright Chromium.
SVG/PNG files are static first-frame fallbacks; GIFs hold each state for 6 seconds.
"""

import io
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parent
EVIDENCE = ASSETS.parent / "benchmarks" / "token-savings"
DURATION_MS = 6000


def metrics():
    data = json.loads((EVIDENCE / "results.json").read_text())
    cost = json.loads((EVIDENCE / "cost-estimate.json").read_text())
    rows = data["mcs_common24"]
    off_tokens = sum(row["arms"]["gateway_off"]["tokens"]["total_tokens"] for row in rows)
    pc_tokens = sum(row["arms"]["MCS"]["tokens"]["total_tokens"] for row in rows)
    cache_rate = cost["observed_memory_cached"] / cost["observed_memory_input"]
    rates = cost["points_per_million"]
    cached = cost["observed_memory_cached"] + cost["missing_cache_input"] * cache_rate
    memory_points = ((cost["memory_input"] - cached) * rates["uncached_input"]
                     + cached * rates["cached_input"] + cost["memory_output"] * rates["output"]) / 1e6
    memory_tokens = cost["memory_input"] + cost["memory_output"]
    peak = max(cost["paired_case_bounds"], key=lambda row: 1 - row["pc_solving_plus_memory"] / row["off_solving"])
    assert peak["case"] == 456 and peak["powercontext_pass"] and peak["off_pass"]
    peak_points = peak["pc_plus_memory_points_min"] + (1 - cache_rate) * (
        peak["pc_plus_memory_points_max"] - peak["pc_plus_memory_points_min"])
    return [
        (off_tokens, pc_tokens, cost["off_solving_points"], cost["pc_solving_points"]),
        (off_tokens, pc_tokens + memory_tokens, cost["off_solving_points"], cost["pc_solving_points"] + memory_points),
        (peak["off_solving"], peak["pc_solving_plus_memory"], peak["off_solving_points"], peak_points),
    ]


def text_at(svg, x, y, text):
    pattern = rf'(<text x="{x}" y="{y}"[^>]*>).*?(</text>)'
    svg, count = re.subn(pattern, lambda m: m[1] + escape(text) + m[2], svg)
    assert count == 1, (x, y)
    return svg


def frame(template, lang, index, values):
    off, pc, off_cost, pc_cost = values
    token_reduction = (1 - pc / off) * 100
    cost_reduction = (1 - pc_cost / off_cost) * 100
    zh = lang == "zh"
    titles = ["仅解题", "计入记忆生成", "单题最高"] if zh else ["SOLVING ONLY", "WITH MEMORY GENERATION", "BEST SINGLE TASK"]
    token_labels = ["Token 减少（仅解题）", "Token 减少（已计入记忆生成成本）", "单题最高 Token 降幅"] if zh else [
        "fewer tokens (solving only)", "fewer tokens (memory generation included)", "largest single-task token reduction"]
    svg = template
    for y, value, size in [(202, token_reduction, 36), (309, cost_reduction, 33)]:
        pattern = rf'(<text x="798" y="{y}"[^>]*>).*?(</text>)'
        svg = re.sub(pattern, lambda m: f'{m[1]}{value:.2f}<tspan font-size="{size}">%</tspan>{m[2]}', svg)
    svg = text_at(svg, 804, 237, token_labels[index])
    svg = text_at(svg, 804, 343, ("估算模型成本减少" if index < 2 else "单题最高估算成本降幅") if zh else (
        "lower estimated model cost" if index < 2 else "largest estimated cost reduction"))
    caption = ("SWE-bench Pro · 同 24 题 · 两组均通过 16 题" if index < 2 else "SWE-bench Pro · 第 456 题 · 两组均通过") if zh else (
        "SWE-bench Pro · 24 shared tasks · 16 solved in each arm" if index < 2 else "SWE-bench Pro · Case 456 · Both runs passed")
    svg = text_at(svg, 57, 421, caption)
    for y, value in [(473, off), (536, pc)]:
        svg = text_at(svg, 1208, y, f"{value / 1e6:.2f}M")
    svg = re.sub(r'(<rect x="360" y="512" width=")[^"]+', rf'\g<1>{670 * pc / off:.2f}', svg)
    detail = (f"模型成本：{off_cost:.2f} → {pc_cost:.2f} 点（估算） · " + (
        "不含记忆生成" if index == 0 else "已计入记忆生成成本")) if zh else (
        f"Estimated model cost: {off_cost:.2f} → {pc_cost:.2f} points · " + (
            "Excludes memory generation" if index == 0 else "Includes memory generation"))
    svg = text_at(svg, 58, 581, detail)
    svg = re.sub(r'<text x="54" y="650".*?</text>', '', svg)
    svg = re.sub(r'<g id="hero-pages">.*?</g>', '', svg)
    labels = titles
    tabs = ['<g id="hero-pages">']
    tabs.append(f'<text x="804" y="131" font-size="17" font-weight="600" fill="#0866ee">{index + 1:02} / 03 · {labels[index]}</text>')
    for i, label in enumerate(labels):
        x = 54 + i * 400
        active = i == index
        tabs.append(f'<rect x="{x}" y="637" width="32" height="4" rx="2" fill="{"#0866ee" if active else "#c9d5e7"}"/>')
        tabs.append(f'<text x="{x + 44}" y="646" font-size="17" fill="{"#0866ee" if active else "#63738d"}">{i + 1:02} · {label}</text>')
    tabs.append('</g>')
    svg = svg.replace('</g></svg>', ''.join(tabs) + '</g></svg>')
    desc = f"SWE-bench Pro. {titles[index]}. {token_reduction:.2f}% fewer tokens; {cost_reduction:.2f}% lower estimated model cost. See benchmark methodology."
    return re.sub(r'<desc id="desc">.*?</desc>', f'<desc id="desc">{escape(desc)}</desc>', svg)


def main():
    values = metrics()
    with sync_playwright() as p:
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        browser = p.chromium.launch(**({'executable_path': str(chrome)} if chrome.exists() else {}))
        page = browser.new_page(viewport={"width": 1280, "height": 700}, device_scale_factor=2)
        for lang in ["zh", "en"]:
            template_path = ASSETS / f"readme-hero-{lang}.svg"
            template = template_path.read_text()
            frames = []
            for index, value in enumerate(values):
                svg = frame(template, lang, index, value)
                page.set_content('<style>html,body{margin:0}</style>' + svg)
                page.evaluate('document.fonts.ready')
                assert page.locator('image').evaluate('(e)=>e.getAttribute("href").startsWith("data:image/")')
                assert page.locator('text').evaluate_all('(es)=>es.every(e=>{let b=e.getBBox();return b.x>=0 && b.x+b.width<1258})'), (lang, index)
                raw = page.screenshot()
                frames.append(Image.open(io.BytesIO(raw)).convert('RGB'))
                if index == 0:
                    template_path.write_text(svg)
                    template_path.with_suffix('.png').write_bytes(raw)
            # One shared palette prevents color shifts between states.
            sample = Image.new('RGB', (640, 350 * len(frames)))
            for index, image in enumerate(frames):
                sample.paste(image.resize((640, 350)), (0, index * 350))
            palette = sample.quantize(colors=256)
            encoded = [image.quantize(palette=palette) for image in frames]
            output = template_path.with_suffix('.gif')
            encoded[0].save(output, save_all=True, append_images=encoded[1:],
                            duration=DURATION_MS, loop=0, optimize=True, disposal=1)
            with Image.open(output) as animation:
                assert animation.n_frames == 3 and animation.size == (2560, 1400)
                for index in range(3):
                    animation.seek(index)
                    assert animation.info['duration'] == DURATION_MS
            print(f"{output.name}: 3 frames, {DURATION_MS} ms each, {output.stat().st_size:,} bytes")
        browser.close()


if __name__ == '__main__':
    main()
