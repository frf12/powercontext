# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "playwright"]
# ///
"""Render the three-state README hero from SVG templates and benchmark evidence.

Run: uv run docs/assets/render_hero.py
Uses installed Chrome on macOS, otherwise Playwright Chromium.
SVG/PNG files are stable first-stage fallbacks. GIFs animate a six-second loop,
with 1.3 seconds of readable values and 0.7 seconds of motion per stage.
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
FRAME_MS = 50
HOLD_MS = 1300
TRANSITION_MS = 700
STAGE_MS = HOLD_MS + TRANSITION_MS
LOOP_MS = STAGE_MS * 3


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



TIMELINE_JS = r"""
const ns = 'http://www.w3.org/2000/svg';
const root = document.querySelector('svg');
const parser = new DOMParser();
const states = window.heroSVGs.map(s => parser.parseFromString(s, 'image/svg+xml'));
const at = (scope, x, y) => scope.querySelector(`text[x="${x}"][y="${y}"]`);
const main = at(root, 798, 202).parentElement;
const make = (name, attrs) => {
  const e = document.createElementNS(ns, name);
  for (const [k,v] of Object.entries(attrs)) e.setAttribute(k,v);
  return e;
};
const tokens = at(root, 798, 202), costs = at(root, 798, 309);
const offTotal = at(root, 1208, 473), pcTotal = at(root, 1208, 536);
const bar = root.querySelector('rect[x="360"][y="512"]');
for (const e of [tokens,costs,offTotal,pcTotal]) e.style.fontVariantNumeric = 'tabular-nums';
const labels = [[804,131],[804,237],[804,343],[57,421],[58,581]].map(([x,y]) => {
  const outgoing = at(root,x,y);
  const incoming = outgoing.cloneNode(true);
  incoming.setAttribute('opacity','0');
  outgoing.after(incoming);
  return {outgoing,incoming,texts:states.map(s=>at(s,x,y).textContent)};
});
const tabText = [0,1,2].map(i => at(root,98+i*400,646));
const tabMarks = [0,1,2].map(i => root.querySelector(`rect[x="${54+i*400}"][y="637"]`));
const defs = root.querySelector('defs');
const clip = make('clipPath',{id:'motion-bar-clip'});
const clipRect = make('rect',{x:360,y:512,width:400,height:30,rx:7});
clip.append(clipRect); defs.append(clip);
const gradient = make('linearGradient',{id:'motion-glint'});
for (const [offset,opacity] of [['0',0],['.5',.24],['1',0]]) {
  gradient.append(make('stop',{offset,'stop-color':'white','stop-opacity':opacity}));
}
defs.append(gradient);
const glint = make('rect',{x:360,y:512,width:90,height:30,fill:'url(#motion-glint)','clip-path':'url(#motion-bar-clip)'});
bar.after(glint);
main.append(make('rect',{x:54,y:672,width:1172,height:3,rx:1.5,fill:'#dce6f5'}));
const progress = make('rect',{x:54,y:672,width:0,height:3,rx:1.5,fill:'#0866ee'});
main.append(progress);
const smooth = t => t*t*(3-2*t);
const mix = (a,b,t) => a+(b-a)*t;
const blue = [8,102,238], grey = [99,115,141];
const tint = strength => `rgb(${grey.map((v,i)=>Math.round(mix(v,blue[i],strength))).join(',')})`;
window.seekHero = milliseconds => {
  const elapsed = ((milliseconds % window.loopMS)+window.loopMS)%window.loopMS;
  const stage = Math.floor(elapsed/window.stageMS);
  const next = (stage+1)%3;
  const local = elapsed-stage*window.stageMS;
  const transition = Math.max(0,Math.min(1,(local-window.holdMS)/window.transitionMS));
  const eased = smooth(transition);
  const v = window.heroMetrics[stage].map((a,i)=>mix(a,window.heroMetrics[next][i],eased));
  const [off,pc,offCost,pcCost] = v;
  tokens.firstChild.data = ((1-pc/off)*100).toFixed(2);
  costs.firstChild.data = ((1-pcCost/offCost)*100).toFixed(2);
  offTotal.textContent = (off/1e6).toFixed(2)+'M';
  pcTotal.textContent = (pc/1e6).toFixed(2)+'M';
  const width = 670*pc/off;
  bar.setAttribute('width',width); clipRect.setAttribute('width',width);
  const lift = Math.sin(Math.PI*transition)*3;
  tokens.setAttribute('transform',`translate(0 ${-lift})`);
  costs.setAttribute('transform',`translate(0 ${-lift})`);
  for (const {outgoing,incoming,texts} of labels) {
    outgoing.textContent = texts[stage]; incoming.textContent = texts[next];
    const same = texts[stage]===texts[next];
    outgoing.setAttribute('opacity',same?1:Math.max(0,1-2*eased));
    incoming.setAttribute('opacity',same?0:Math.max(0,2*eased-1));
    outgoing.setAttribute('transform',`translate(0 ${same?0:-12*eased})`);
    incoming.setAttribute('transform',`translate(0 ${12*(1-eased)})`);
  }
  for (let i=0;i<3;i++) {
    const active = i===stage?1-eased:(i===next?eased:0);
    tabText[i].setAttribute('fill',tint(active));
    tabMarks[i].setAttribute('fill',tint(active));
    tabMarks[i].setAttribute('width',32);
  }
  progress.setAttribute('width',1172*elapsed/window.loopMS);
  // A gentle moving highlight and progress keep the readable hold alive.
  glint.setAttribute('x',360-90+(width+180)*local/window.stageMS);
  root.dataset.stage = String(stage);
  root.dataset.progress = String(transition);
  root.dataset.values = JSON.stringify(v);
  return {stage,next,transition,values:v,tokens:tokens.firstChild.data,cost:costs.firstChild.data};
};
window.seekHero(0);
if (new URLSearchParams(location.search).has('play')) {
  const start=performance.now();
  const tick = now => {window.seekHero(now-start); requestAnimationFrame(tick);};
  requestAnimationFrame(tick);
}
"""


def preview_html(svgs, values):
    config = {
        "heroSVGs": svgs,
        "heroMetrics": values,
        "holdMS": HOLD_MS,
        "transitionMS": TRANSITION_MS,
        "stageMS": STAGE_MS,
        "loopMS": LOOP_MS,
    }
    setup = "\n".join(f"window.{key} = {json.dumps(value)};" for key, value in config.items())
    return ('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:#fff}'
            'svg{display:block}</style>' + svgs[0] + '<script>' + setup + '(() => {' + TIMELINE_JS + '})();</script>')


def main():
    values = metrics()
    frame_count = LOOP_MS // FRAME_MS
    with sync_playwright() as p:
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        browser = p.chromium.launch(**({'executable_path': str(chrome)} if chrome.exists() else {}))
        static = browser.new_page(viewport={"width": 1280, "height": 700}, device_scale_factor=2)
        motion = browser.new_page(viewport={"width": 1280, "height": 700}, device_scale_factor=1)
        for lang in ["zh", "en"]:
            template_path = ASSETS / f"readme-hero-{lang}.svg"
            template = template_path.read_text()
            svgs = [frame(template,lang,index,value) for index,value in enumerate(values)]
            for index,svg in enumerate(svgs):
                static.set_content('<style>html,body{margin:0}</style>' + svg)
                static.evaluate('document.fonts.ready')
                assert static.locator('text').evaluate_all('(es)=>es.every(e=>{let b=e.getBBox();return b.x>=0 && b.x+b.width<1258})'), (lang,index)
                if index == 0:
                    template_path.write_text(svg)
                    template_path.with_suffix('.png').write_bytes(static.screenshot())
            html = preview_html(svgs,values)
            preview = Path('/tmp') / f'pc-hero-motion-{lang}.html'
            preview.write_text(html)
            motion.set_content(html)
            motion.evaluate('document.fonts.ready')
            assert motion.evaluate('document.querySelector("svg").dataset.stage') == '0'
            assert motion.locator('image').evaluate('(e)=>e.getAttribute("href").startsWith("data:image/")')
            frames = []
            for tick in range(frame_count):
                state = motion.evaluate('(time)=>window.seekHero(time)', tick*FRAME_MS)
                if tick % (STAGE_MS//FRAME_MS) == 0:
                    expected = values[state['stage']]
                    assert state['values'] == list(expected)
                    assert state['tokens'] == f"{(1-expected[1]/expected[0])*100:.2f}"
                    assert state['cost'] == f"{(1-expected[3]/expected[2])*100:.2f}"
                    assert motion.locator('text[x="798"][y="202"]').text_content() == state['tokens'] + '%'
                raw = motion.screenshot()
                frames.append(Image.open(io.BytesIO(raw)).convert('RGB'))
            # A shared palette keeps the stationary canvas unchanged across GIF frames.
            samples = [frames[i] for i in [0,26,33,40,66,73,80,106,113]]
            sample = Image.new('RGB',(640,350*len(samples)))
            for index,image in enumerate(samples):
                sample.paste(image.resize((640,350)),(0,index*350))
            palette = sample.quantize(colors=256)
            encoded = [image.quantize(palette=palette,dither=Image.Dither.NONE) for image in frames]
            output = template_path.with_suffix('.gif')
            encoded[0].save(output,save_all=True,append_images=encoded[1:],
                            duration=FRAME_MS,loop=0,optimize=True,disposal=1)
            with Image.open(output) as animation:
                assert 3 < animation.n_frames <= frame_count and animation.size == (1280,700)
                duration = 0
                for index in range(animation.n_frames):
                    animation.seek(index)
                    duration += animation.info['duration']
                assert duration == LOOP_MS
                assert animation.info['loop'] == 0
                print(f"{output.name}: {animation.n_frames} frames, {duration} ms loop, {output.stat().st_size:,} bytes; {preview}",flush=True)
        browser.close()


if __name__ == '__main__':
    main()
