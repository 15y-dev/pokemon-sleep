"""
report_html.py
全ポケモンを一覧にして、絞り込み・並べ替えで探せる HTML レポートを作る。通信はしない。

出力:
    report.html          一覧(データは JSON で埋め込み。画像は別ファイルを読む)
    report_img\\*.jpg     カード用の画像(上部 NNNN_t.jpg / スキル部 NNNN_s.jpg)。report.html と同じ場所に置くこと

全体の画像(撮影したパノラマ)は、panoramas\\ の元の画像を直接開く(コピーしない)。

使い方:
    python report_html.py                  # report.html と report_img\\ を作る
    python report_html.py --no-images      # 画像を作り直さない(既にあるものを使う。HTML だけ速く作り直したいとき)
"""
import argparse
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
OUT_IMG = HERE / "report_img"

# 画像の切り出し範囲(撮影画像は幅720。縦は約2940)
TOP_CROP = (0, 745)          # 上部: ヘッダー・スプライト・名前・アメ
SKILL_START_FROM_H = -30     # スキル部の上端: 「メインスキル・サブスキル」の見出しの少し上
SKILL_END_FROM_Y = -80       # スキル部の下端: 「詳細ステータス」の少し上

TYPE_COLOR = {
    "ノーマル": "#8d8a7c", "ほのお": "#d9623a", "みず": "#3f7fd0", "でんき": "#d3a61c", "くさ": "#4c9f4a",
    "こおり": "#4aaeb8", "かくとう": "#b8453a", "どく": "#9250b0", "じめん": "#b08a45", "ひこう": "#7b8fd8",
    "エスパー": "#d4577f", "むし": "#8aa31e", "いわ": "#9b8d58", "ゴースト": "#625494", "ドラゴン": "#4b5bc4",
    "あく": "#5a4a48", "はがね": "#6f8fa3", "フェアリー": "#d884b8",
}
PURPOSE_NAME = {k: v[0] for k, v in sl.PURPOSES.items()}
PURPOSE_SHORT = {k: v[0].split("(")[0] for k, v in sl.PURPOSES.items()}


def write_images(id_, anchors):
    """カード用の画像を report_img\\ に書く。戻り値: (上部あり, スキル部あり)"""
    img = cv2.imread(str(IMG_DIR / f"{id_}.png"))
    if img is None:
        return False, False
    h = img.shape[0]
    top = img[TOP_CROP[0]:TOP_CROP[1]]
    Y, H = anchors
    y0, y1 = max(0, H + SKILL_START_FROM_H), min(h, Y + SKILL_END_FROM_Y)
    skill = img[y0:y1]
    q = [cv2.IMWRITE_JPEG_QUALITY, 74]
    cv2.imwrite(str(OUT_IMG / f"{id_}_t.jpg"), cv2.resize(top, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA), q)
    cv2.imwrite(str(OUT_IMG / f"{id_}_s.jpg"), cv2.resize(skill, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA), q)
    return True, True


def skill_anchors(id_):
    """(Y, H) = (「詳細ステータス」の y, 「メインスキル・サブスキル」の y)。OCR の位置から求める。"""
    import extract_fields as ef
    toks, syms = ef.load_tokens(HERE / "ocr" / f"{id_}.json")
    c = ef.Ctx(id_, toks, syms, date.today())
    Y, H = ef.anchors(c)
    H = int(H) if H else 1575
    Y = int(Y) if Y else H + 675
    return Y, H


def nature_text(r):
    up, down = r.get("nature_up"), r.get("nature_down")
    if not up and not down:
        return "補正なし"
    return f"↑{up or '-'} / ↓{down or '-'}"

AX = list(sl.AXES)


def item(row, r):
    """1匹ぶんの、画面に出す・絞り込みに使うデータ。キーは短くして、HTML を軽くする。"""
    name = r.get("species") or f"{r.get('candy_species') or '?'}系"
    nick = r.get("nickname")
    sec_after = row["おてつだい時間(計算後・秒)"]
    sec_now = row["おてつだい時間(画面・秒)"]
    subs = []
    for i in range(1, 6):
        n = r.get(f"sub_skill{i}")
        subs.append([n or "", 1 if r.get(f"sub_skill{i}_unlocked") else 0])
    ings = [x for x in (r.get("ingredient1"), r.get("ingredient2"), r.get("ingredient3")) if x]
    return {
        "id": r["id"], "name": name, "nick": nick if nick and nick != name else "",
        "lv": row["Lv"] or 0, "type": row["タイプ"] or "", "sp": row["とくい"] or "", "berry": row["きのみ"] or "",
        "skill": row["メインスキル"] or "", "nature": row["せいかく"] or "", "nat": nature_text(r),
        "natUp": r.get("nature_up") or "", "natDown": r.get("nature_down") or "",
        "ings": ings, "dessert": row["新デザートの食材"], "wiki": row["Wiki候補"],
        "fav": 1 if r.get("favorite") else 0, "gender": r.get("gender") or "",
        "subs": subs,
        "m": row["★"], "pt": row["点数"],
        "ax": [[row[f"月:{k}"], row[f"点:{k}"], row[f"順位:{k}"]] for k in AX],
        "rank": row["順位:総合"],
        "purposes": row["目的"],
        "helpNow": sec_now, "help": sec_after, "helpText": row["おてつだい時間(計算後)"],
        "carryNow": row["最大所持数(画面)"], "carry": row["最大所持数(計算後)"],
        "color": TYPE_COLOR.get(row["タイプ"] or "", "#8a84a0"),
        "hits": {k: [n for n, _ in subs if n and sl.AXES[k][0].get(n, 0) > 0] for k in AX},
    }


def build_data(recs, make_images):
    rows = sl.build_all(recs)
    by_id = {r["id"]: r for r in recs}
    OUT_IMG.mkdir(exist_ok=True)
    items = []
    for n, row in enumerate(sorted(rows, key=lambda x: x["順位:総合"]), 1):
        r = by_id[row["番号"]]
        it = item(row, r)
        if make_images:
            ok_t, ok_s = write_images(r["id"], skill_anchors(r["id"]))
        else:
            ok_t = (OUT_IMG / f"{r['id']}_t.jpg").exists()
            ok_s = (OUT_IMG / f"{r['id']}_s.jpg").exists()
        it["img"] = [1 if ok_t else 0, 1 if ok_s else 0]
        items.append(it)
        if make_images and n % 100 == 0:
            print(f"  画像 {n}/{len(rows)}")
    return items

E = html.escape

FONTS = ("https://fonts.googleapis.com/css2?family=Fraunces:ital,wght@1,500;1,600&"
         "family=Shippori+Mincho:wght@500;800&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap")


def counted(values):
    """値ごとの個数(多い順)。絞り込みの選択肢に使う。"""
    from collections import Counter
    return Counter(v for v in values if v).most_common()


def check_group(gid, title, opts, open_=False, note=""):
    """チェックボックスの一群。opts: [(値, 表示名, 個数)]"""
    items = "".join(
        f'<label class="opt"><input type="checkbox" data-g="{gid}" value="{E(str(v))}"><span>{E(str(t))}</span><i>{n}</i></label>'
        for v, t, n in opts)
    return (f'<details class="grp"{" open" if open_ else ""}><summary>{E(title)}<b class="cnt" data-cnt="{gid}"></b></summary>'
            f'{f"<p class=hint>{E(note)}</p>" if note else ""}<div class="opts">{items}</div></details>')


def moon_select(sid, title):
    opts = "".join(f'<option value="{k}">{k}つ以上</option>' for k in range(1, 6))
    return f'<label class="sel"><span>{E(title)}</span><select id="{sid}"><option value="0">指定なし</option>{opts}</select></label>'


def build_panel(items):
    purposes = [(k, PURPOSE_SHORT[k], sum(k in it["purposes"] for it in items)) for k in sl.PURPOSES]
    types = [(t, t, n) for t, n in counted(it["type"] for it in items)]
    sps = [(t, t + "とくい", n) for t, n in counted(it["sp"] for it in items)]
    skills = [(t, t, n) for t, n in counted(it["skill"] for it in items)]
    subs = [(t, t, n) for t, n in counted(s for it in items for s, _ in it["subs"])]
    ings = [(t, t, n) for t, n in counted(g for it in items for g in it["ings"])]
    ups = [(t, t + "↑", n) for t, n in counted(it["natUp"] for it in items)]
    downs = [(t, t + "↓", n) for t, n in counted(it["natDown"] for it in items)]
    berries = [(t, t, n) for t, n in counted(it["berry"] for it in items)]
    parts = [
        '<div class="sec"><h3>月(評価)</h3><p class="hint">その月の数以上のものだけ表示します。複数指定すると、すべてを満たすものを探します。</p>'
        f'<div class="moonsel">{moon_select("mT", "総合")}'
        + "".join(moon_select(f"m{i}", k) for i, k in enumerate(AX)) + "</div></div>",
        '<div class="sec"><h3>数値</h3>'
        '<label class="num"><span>おてつだい時間(計算後)</span><input id="helpMax" type="number" min="0" step="1" placeholder="分以下"><em>分以下</em></label>'
        '<label class="num"><span>最大所持数(計算後)</span><input id="carryMin" type="number" min="0" step="1" placeholder="個以上"><em>個以上</em></label>'
        '<label class="num"><span>Lv</span><input id="lvMin" type="number" min="1" placeholder="以上"><em>以上</em><input id="lvMax" type="number" min="1" placeholder="以下"><em>以下</em></label>'
        '<label class="opt one"><input type="checkbox" id="favOnly"><span>お気に入りだけ</span></label>'
        '<label class="opt one"><input type="checkbox" id="dessertOnly"><span>新デザートの食材を持つ</span></label>'
        '<label class="opt one"><input type="checkbox" id="wikiOnly"><span>Wiki候補(アンバー渓谷)</span></label></div>',
        check_group("purposes", "目的", purposes, True, "選んだ目的のどれかに当てはまる個体"),
        check_group("type", "タイプ", types),
        check_group("sp", "とくい", sps),
        check_group("berry", "きのみ", berries),
        check_group("skill", "メインスキル", skills),
        check_group("sub", "サブスキル(持っているもの)", subs, False, "選んだものを、すべて持つ個体(未解放も含む)"),
        check_group("ing", "食材(持っているもの)", ings, False, "選んだものを、すべて持つ個体"),
        check_group("natUp", "せいかく(上がる効果)", ups),
        check_group("natDown", "せいかく(下がる効果)", downs),
    ]
    return "".join(parts)

def read(*names):
    return "\n".join((HERE / n).read_text(encoding="utf-8") for n in names)


SORT_OPTIONS = [("total", "総合(点数の高い順)")] + [(f"ax{a}", f"{k}の評価が高い順") for a, k in enumerate(AX)] + [
    ("helpFast", "おてつだい時間が短い順(計算後)"), ("carryMany", "最大所持数が多い順(計算後)"),
    ("lvHigh", "Lvが高い順"), ("lvLow", "Lvが低い順"), ("no", "番号順")]


def build(recs, make_images):
    items = build_data(recs, make_images)
    meta = {
        "purposeShort": PURPOSE_SHORT,
        "unlock": ["1"] + [str(x) for x in sl.UNLOCK_LV[1:]],
        "axisNote": {k: sl.AXES[k][2] for k in AX},
    }
    sort_opts = "".join(f'<option value="{v}">{E(t)}</option>' for v, t in SORT_OPTIONS)
    data_js = (f"const DATA={json.dumps(items, ensure_ascii=False, separators=(',', ':'))};"
               f"const AX={json.dumps(AX, ensure_ascii=False)};const META={json.dumps(meta, ensure_ascii=False)};")
    data_js = data_js.replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ポケモンスリープ 手持ちポケモン一覧</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{FONTS}" rel="stylesheet">
<style>{read("report_base.css", "report_panel.css", "report_card.css")}</style></head><body>
<header class="hero"><div class="moon"></div><small>Pokémon Sleep — a field guide to the sleepers</small>
<h1>手持ちポケモン一覧</h1>
<p>全{len(items)}匹を表示しています。条件を選んで、探してください。月の数(評価)は、サブスキルとせいかくから決めた目安で、今のレベルは見ていません。
おてつだい時間と最大所持数は、未解放のスキルも反映した「計算後」の値です。作成日 <span style="white-space:nowrap">{date.today():%Y-%m-%d}</span>。</p></header>
<div class="bar">
<button class="btn" id="panelToggle" type="button">絞り込み</button>
<input id="q" type="search" placeholder="名前・タイプ・スキル・食材で検索(スペースで複数)" aria-label="検索">
<label>並び <select id="sort" aria-label="並べ替え">{sort_opts}</select></label>
<button class="btn" id="reset" type="button" disabled>条件をリセット</button>
<span class="count"><b id="shown">0</b> / <span id="total">0</span> 匹</span>
<div class="chips" id="chips"></div></div>
<main class="layout">
<aside class="panel" id="panel" aria-label="絞り込み">{build_panel(items)}</aside>
<section><div class="grid" id="list"></div>
<p class="none" id="none" hidden>条件に合うポケモンがいません。条件をゆるめてみてください。</p>
<button class="btn more" id="more" type="button" hidden>さらに表示</button><div id="sentinel"></div></section></main>
<footer>月の数は、Wiki の記述を根拠にした<b>目安</b>です(ゲームの実際の強さを測ったものではありません)。
睡眠タイムラインの <b>金の丸</b>は解放済み、<b>空の丸</b>は未解放のサブスキル。<b>蛍光ペン</b>は、評価に効くものです。
せいかく・サブスキルは OCR と色判定で読み取ったため、誤りがあれば corrections.json で直せます。
画像は <b>report_img</b> と <b>panoramas</b> のフォルダを、report.html と同じ場所に置いてください。</footer>
<dialog id="viewer"><img alt="全体の画像"><div class="cap"></div></dialog>
<script>{data_js}</script>
<script>{read("report_card.js", "report_filter.js")}</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=str(HERE / "pokemon.json"))
    ap.add_argument("--out", default=str(HERE / "report.html"))
    ap.add_argument("--no-images", action="store_true", help="画像を作り直さない(既にあるものを使う)")
    a = ap.parse_args()
    recs = json.loads(Path(a.json).read_text(encoding="utf-8"))
    out = Path(a.out)
    out.write_text(build(recs, not a.no_images), encoding="utf-8")
    imgs = list(OUT_IMG.glob("*.jpg"))
    print(f"{out}  ({out.stat().st_size / 1e6:.1f} MB)  画像 {len(imgs)}枚 ({sum(p.stat().st_size for p in imgs) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
