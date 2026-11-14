"""
report_html.py
目的別の選出(shortlist.py)を、スクリーンショット付きの HTML レポートにする。通信はしない。

出力: report.html(1ファイル。画像は JPEG にして埋め込むので、ファイルを移動しても画像が消えない)

使い方:
    python report_html.py                      # すべての目的
    python report_html.py --purpose dessert    # 1つだけ
    python report_html.py --min-star 3         # ★3も載せる(既定は★4以上)
    python report_html.py --top 30             # 目的ごとの最大件数(既定は全件)

各カードには、画像2枚(上部=名前・SP・特技、スキル部=メインスキル・サブスキル)が付く。
クリックすると、撮影した全体の画像(縮小版)が開く。
"""
import argparse
import base64
import html
import json
import sys
from datetime import date
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shortlist as sl

HERE = Path(__file__).resolve().parent
IMG_DIR = HERE / "panoramas"

# 画像の切り出し範囲(撮影画像は幅720。縦は約2940)。範囲はどの画像でも大きくは動かない。
TOP_CROP = (0, 745)          # 上部: ヘッダー・スプライト・名前・アメ
SKILL_START_FROM_H = -30     # スキル部の上端: 「メインスキル・サブスキル」の見出しの少し上
SKILL_END_FROM_Y = -80       # スキル部の下端: 「詳細ステータス」の少し上(最後のサブスキルの下に余白が残らず、次の帯も写り込まない)


def read_css():
    """スタイルを2つのファイルから読んで、HTML に埋め込む。"""
    return "\n".join((HERE / n).read_text(encoding="utf-8") for n in ("report_base.css", "report_card.css"))


def jpeg_b64(img, scale=1.0, q=70):
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


_anchor_cache = {}


def skill_anchors(id_):
    """(Y, H) = (「詳細ステータス」の y, 「メインスキル・サブスキル」の y)。OCR の位置から求める。"""
    if id_ in _anchor_cache:
        return _anchor_cache[id_]
    import extract_fields as ef
    toks, syms = ef.load_tokens(HERE / "ocr" / f"{id_}.json")
    c = ef.Ctx(id_, toks, syms, date.today())
    Y, H = ef.anchors(c)
    H = int(H) if H else 1575
    Y = int(Y) if Y else H + 675
    _anchor_cache[id_] = (Y, H)
    return _anchor_cache[id_]


def images_for(id_):
    """カード用の画像(上部・スキル部)と、全体の縮小版。画像が無ければ None。"""
    p = IMG_DIR / f"{id_}.png"
    img = cv2.imread(str(p))
    if img is None:
        return None, None, None
    h = img.shape[0]
    top = img[TOP_CROP[0]:TOP_CROP[1]]
    Y, H = skill_anchors(id_)
    y0, y1 = max(0, H + SKILL_START_FROM_H), min(h, Y + SKILL_END_FROM_Y)
    skill = img[y0:y1]
    # 表示の幅に対して、画像が小さすぎるとぼやけるので、スキル部は大きめ(0.8)にする
    return jpeg_b64(top, 0.6, 72), jpeg_b64(skill, 0.8, 72), jpeg_b64(img, 0.5, 66)

E = html.escape

JS = """
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
// --- 全体画像の表示 ---
const dlg=$('#viewer'),big=$('img',dlg),cap=$('.cap',dlg);
document.addEventListener('click',e=>{const b=e.target.closest('[data-full]');if(!b)return;
  big.src=document.getElementById(b.dataset.full).dataset.src;cap.textContent=b.dataset.cap||'';dlg.showModal();});
dlg.addEventListener('click',()=>dlg.close());
// --- 絞り込み・並べ替え ---
const q=$('#q'),minStar=$('#star'),sortBy=$('#sort'),skillToggle=$('#skillimg');
const norm=s=>s.normalize('NFKC').toLowerCase();
function apply(){
  const words=norm(q.value).split(/\\s+/).filter(Boolean),ms=+minStar.value,key=sortBy.value;
  let shown=0;
  $$('.grid').forEach(g=>{
    const cards=$$('.card',g);
    cards.sort((a,b)=>key==='pts'?(+a.dataset.i-+b.dataset.i):(a.dataset.no.localeCompare(b.dataset.no)));
    cards.forEach(c=>{g.appendChild(c);
      const ok=+c.dataset.star>=ms&&words.every(w=>c.dataset.text.includes(w));
      c.hidden=!ok;if(ok)shown++;});
    const sec=g.closest('section'),n=cards.filter(c=>!c.hidden).length;
    $('.n',sec).textContent=n+'匹';$('.none',sec).hidden=n>0;
  });
  $('#shown').textContent=shown;
}
[q,minStar,sortBy].forEach(el=>el.addEventListener('input',apply));
skillToggle.addEventListener('change',()=>document.body.classList.toggle('noskill',!skillToggle.checked));
// --- いま見ている目的をタブで示す ---
const tabs=$$('.tabs a'),io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)
  tabs.forEach(t=>t.classList.toggle('cur',t.getAttribute('href')==='#'+e.target.id));}),{rootMargin:'-30% 0px -60% 0px'});
$$('section.purpose').forEach(s=>io.observe(s));
apply();
"""

FONTS = ("https://fonts.googleapis.com/css2?family=Fraunces:ital,wght@1,500;1,600&"
         "family=Shippori+Mincho:wght@500;800&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap")

# タイプの色(カードの縁・タイプのチップ)。ポケモンの公式の色に近い、落ち着いた色。
TYPE_COLOR = {
    "ノーマル": "#8d8a7c", "ほのお": "#d9623a", "みず": "#3f7fd0", "でんき": "#d3a61c", "くさ": "#4c9f4a",
    "こおり": "#4aaeb8", "かくとう": "#b8453a", "どく": "#9250b0", "じめん": "#b08a45", "ひこう": "#7b8fd8",
    "エスパー": "#d4577f", "むし": "#8aa31e", "いわ": "#9b8d58", "ゴースト": "#625494", "ドラゴン": "#4b5bc4",
    "あく": "#5a4a48", "はがね": "#6f8fa3", "フェアリー": "#d884b8",
}

PURPOSE_HOW = {
    "dessert": "食材とくいで、新デザート・ドリンクの食材(とくせんエッグ・おいしいシッポ・ワカクサ大豆)を、解放済みの枠で持つ個体。",
    "berry": "きのみとくいで、アンバー渓谷の好物(カゴ・ラム・ヤチェ = どく・むし・ドラゴン)のきのみを集める個体。",
    "skill": "スキルとくいで、料理パワーアップS・料理チャンスS・料理アシストS を持つ個体。",
    "healer": "げんきオールS・げんきエールS(チーム全員を回復する)を持つ個体。サブスキルは、スキルの発動に関わるものを評価します。",
    "future": "アンバー渓谷の好物のきのみ(どく・むし・ドラゴン)で、とくいを問わず、サブスキルとせいかくが良い個体。",
}


def nature_text(r):
    up, down = r.get("nature_up"), r.get("nature_down")
    if not up and not down:
        return "補正なし"
    return f"↑{up or '-'} / ↓{down or '-'}"


def sub_list(r, table):
    """サブスキル5枠の <li>。解放済み=on、未解放=off、この目的で評価に効くもの=hit。"""
    out = []
    for i in range(5):
        name = r.get(f"sub_skill{i + 1}")
        on = bool(r.get(f"sub_skill{i + 1}_unlocked"))
        lv = "Lv1" if i == 0 else f"Lv{sl.UNLOCK_LV[i]}"
        cls = ["on" if on else "off"]
        if name and table.get(name, 0) > 0:
            cls.append("hit")
        out.append(f'<li class="{" ".join(cls)}"><span class="lv">{lv}</span>'
                   f'<span class="nm">{E(name or "-")}</span></li>')
    return "".join(out)


def card_html(row, r, table, imgs, delay, idx):
    top, skill, _full = imgs
    id_ = r["id"]
    name = r.get("species") or f"{r.get('candy_species') or '?'}系"
    nick = r.get("nickname")
    sub = f"<small>「{E(nick)}」</small>" if nick and nick != name else ""
    n = row["★"]
    ing = row["食材"] or "-"
    dessert = row["新デザートの食材"]
    notes = [f'<span class="good">{E(g)}</span>' for g in row["良い点"].split(", ") if g.startswith("せいかく")]
    notes += [f'<span class="bad">{E(b)}</span>' for b in row["気になる点"].split(", ") if b]
    if dessert:
        notes.append(f'<span class="good">新デザート食材: {E(dessert)}</span>')
    if row["Wiki候補"]:
        notes.append(f'<span class="wiki">Wiki候補: {E(row["Wiki候補"])}</span>')
    sec = row["おてつだい時間(秒)"]
    moons = "".join(f'<i class="{"on" if k < n else ""}"></i>' for k in range(5))
    tcolor = TYPE_COLOR.get(row["タイプ"] or "", "#8a84a0")
    # 検索用の文字列(名前・ニックネーム・タイプ・とくい・スキル・食材・きのみ・せいかく)
    text = " ".join(str(x) for x in (name, nick or "", r.get("candy_species") or "", row["タイプ"], row["とくい"],
                                     row["メインスキル"], row["食材"], row["きのみ"], row["せいかく"], id_)).lower()
    lv = row["Lv"] or 0
    parts = [
        f'<article class="card" style="--tc:{tcolor};animation-delay:{delay}ms" data-i="{idx}" data-star="{n}" '
        f'data-lv="{lv}" data-no="{E(id_)}" data-text="{E(text)}">',
        '<div class="top"><div class="info">',
        f'<div class="rank"><span class="moons" role="img" aria-label="ランク {n} / 5">{moons}</span>'
        f'<span class="pts">{row["点数"]}<small>pt</small></span><span class="no">No.{E(id_)}</span></div>',
        f'<div class="name">{E(name)}{sub}</div>',
        f'<div class="meta"><span class="chip lv">Lv.{lv}</span>'
        f'<span class="chip type">{E(row["タイプ"] or "?")}</span>'
        f'<span class="chip">{E(row["とくい"] or "?")}とくい</span>'
        f'<span class="chip">{E(row["きのみ"] or "?")}</span></div>',
        '<dl class="facts">',
        f'<div class="kv"><dt>メインスキル</dt><dd>{E(row["メインスキル"] or "-")}</dd></div>',
        f'<div class="kv"><dt>せいかく</dt><dd>{E(row["せいかく"] or "-")} <small>{E(nature_text(r))}</small></dd></div>',
        f'<div class="kv"><dt>食材</dt><dd>{E(ing)}</dd></div>',
        f'<div class="kv"><dt>おてつだい</dt><dd>{sec if sec else "-"} <small class="inline">秒</small></dd></div>',
        "</dl></div>",
    ]
    if top:
        parts.append(f'<button class="shot" data-full="f{id_}" data-cap="No.{E(id_)} {E(name)}" '
                     f'aria-label="全体の画像を開く"><img loading="lazy" alt="No.{E(id_)} の上部" src="{top}"></button>')
    parts.append("</div>")
    parts.append(f'<ul class="tl" aria-label="サブスキル">{sub_list(r, table)}</ul>')
    if notes:
        parts.append(f'<div class="note">{"".join(notes)}</div>')
    if skill:
        parts.append(f'<button class="skillshot" data-full="f{id_}" data-cap="No.{E(id_)} {E(name)}" '
                     f'aria-label="全体の画像を開く"><img loading="lazy" alt="No.{E(id_)} のスキル部" src="{skill}"></button>')
    parts.append("</article>")
    return "".join(parts)

_img_cache = {}


def cached_images(id_):
    if id_ not in _img_cache:
        _img_cache[id_] = images_for(id_)
    return _img_cache[id_]


def section_html(key, recs_by_id, rows, top):
    title = sl.PURPOSES[key][0]
    as_sp = sl.PURPOSES[key][2]
    shown = rows[:top] if top else rows
    cards = []
    for i, row in enumerate(shown):
        r = recs_by_id[row["番号"]]
        table = sl.sub_table(as_sp or r.get("specialty"))
        cards.append(card_html(row, r, table, cached_images(row["番号"]), min(i, 12) * 55, i))
    body = f'<div class="grid">{"".join(cards)}</div><p class="none" hidden>条件に合う個体がありません。</p>'
    count = f"{len(shown)}匹"
    return (f'<section class="purpose" id="{key}"><h2>{E(title)}<span class="n">{count}</span></h2>'
            f'<p class="how">{E(PURPOSE_HOW.get(key, ""))}</p>{body}</section>')


def build(recs, keys, min_star, top):
    recs_by_id = {r["id"]: r for r in recs}
    built = [(k, sl.build_purpose(recs, k, min_star=min_star)) for k in keys]
    nav = "".join(f'<a href="#{k}">{E(sl.PURPOSES[k][0].split("(")[0])}<b>{len(rows[:top] if top else rows)}</b></a>'
                  for k, rows in built)
    sections = "".join(section_html(k, recs_by_id, rows, top) for k, rows in built)
    used = []
    for _, rows in built:
        for row in (rows[:top] if top else rows):
            if row["番号"] not in used:
                used.append(row["番号"])
    store = "".join(f'<div id="f{i}" data-src="{cached_images(i)[2]}" hidden></div>'
                    for i in used if cached_images(i)[2])
    star_options = "".join(f'<option value="{k}">{k}つ以上</option>' for k in range(min_star, 5)) + '<option value="5">5つ</option>'
    mode = f"今のレベルは見ません(未解放のサブスキルも同じ重さで数えます)。★{min_star}以上だけを載せています"
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ポケモンスリープ 育てるポケモン選出</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{FONTS}" rel="stylesheet">
<style>{read_css()}</style></head><body>
<header class="hero"><div class="moon"></div><small>Pokémon Sleep — a field guide to the sleepers</small>
<h1>育てるポケモン選出</h1>
<p>{len(recs)}匹の手持ちから、目的ごとに選びました。{E(mode)}。作成日 <span style="white-space:nowrap">{date.today():%Y-%m-%d}</span>。写真をクリックすると、撮影した全体が開きます。</p></header>
<div class="bar"><nav class="tabs">{nav}</nav>
<div class="ctl">
<input id="q" type="search" placeholder="名前・タイプ・スキルで検索" aria-label="検索">
<label>月 <select id="star" aria-label="ランクの下限">{star_options}</select></label>
<label>並び <select id="sort" aria-label="並べ替え"><option value="pts">点数が高い順</option><option value="no">番号順</option></select></label>
<label class="chk"><input id="skillimg" type="checkbox" checked>スキル画像</label>
<span><span id="shown">0</span>枚</span></div></div>
<main>{sections}</main>
<footer>月の数は、Wiki の記述を根拠にした<b>目安</b>です(ゲームの実際の強さを測ったものではありません)。
睡眠タイムラインの <b>金の丸</b>は解放済み、<b>空の丸</b>は未解放のサブスキル。<b>蛍光ペン</b>は、その目的の評価に効くものです。
せいかく・サブスキルは OCR と色判定で読み取ったため、誤りがあれば corrections.json で直せます。</footer>
{store}
<dialog id="viewer"><img alt="全体の画像"><div class="cap"></div></dialog>
<script>{JS}</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=str(HERE / "pokemon.json"))
    ap.add_argument("--out", default=str(HERE / "report.html"))
    ap.add_argument("--purpose", default="all", help="目的(カンマ区切り): " + " / ".join(sl.PURPOSES) + " / all")
    ap.add_argument("--top", type=int, default=0, help="目的ごとの最大件数(0 なら全件)")
    ap.add_argument("--min-star", type=int, default=sl.MIN_STAR, help=f"載せる★の下限(既定 {sl.MIN_STAR})")
    a = ap.parse_args()

    recs = json.loads(Path(a.json).read_text(encoding="utf-8"))
    keys = list(sl.PURPOSES) if a.purpose == "all" else [k.strip() for k in a.purpose.split(",") if k.strip()]
    bad = [k for k in keys if k not in sl.PURPOSES]
    if bad:
        sys.exit(f"知らない目的です: {bad}(使えるもの: {', '.join(sl.PURPOSES)})")
    out = Path(a.out)
    out.write_text(build(recs, keys, a.min_star, a.top), encoding="utf-8")
    print(f"{out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
