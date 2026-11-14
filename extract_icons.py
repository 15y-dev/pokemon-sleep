"""
extract_icons.py
撮影した画像(panoramas)から、文字では読めないアイコン(性別・お気に入り・タイプ)を、色で判定する。
OCR の座標(ocr フォルダの JSON)を使って、アイコンの位置を決める。通信はしない。

出力(既定はこのフォルダ):
    icons.json   番号ごとの判定結果と、要確認の一覧(extract_fields.py が読んで pokemon.csv に結合する)

使い方:
    python extract_icons.py                # 全件
    python extract_icons.py --ids 1,2,10   # 指定した番号だけ(画面に表示)

必要: opencv-python, numpy(撮影用の venv に入っている)

判定のしかた:
    性別         アイコンの色。青=オス、赤=メス、色なし(灰色)=性別なし
    お気に入り   星の色。黄色=あり、灰色=なし
    タイプ       アイコンの代表色を、タイプごとの基準色と比べて一番近いものにする。
                 1位と2位の差が小さい(取り違えやすい)ものや、基準色から遠いものは「不明」にして確認に回す。
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_fields as ef   # OCR の読み込みと、座標の扱いを共用する

HERE = Path(__file__).resolve().parent

# タイプの基準色(RGB)。629匹の実データを、色のまとまり(距離9以内)に分けて求めた中央値。
# 18タイプのうち、実データに現れた17タイプ分(「いわ」「むし」を含む)。ひこうまで全て実測。
TYPE_REF = {
    "みず": (52, 158, 254), "ほのお": (254, 107, 52), "でんき": (254, 226, 6), "くさ": (78, 206, 48),
    "ノーマル": (174, 174, 174), "どく": (162, 85, 219), "エスパー": (254, 112, 142), "フェアリー": (254, 185, 254),
    "いわ": (205, 203, 164), "ゴースト": (128, 95, 129), "ドラゴン": (138, 150, 233), "かくとう": (254, 193, 124),
    "むし": (192, 196, 127), "こおり": (122, 233, 254), "じめん": (191, 153, 111), "はがね": (151, 202, 231),
    "あく": (91, 84, 83), "ひこう": (160, 215, 254),
}
TYPE_NEAR_DIST = 12    # 基準色にこれ以内なら、通常どおり採用する(実測のばらつきは数ポイント)
TYPE_NEAR_MARGIN = 25  # そのとき、1位と2位の差がこの値以上であること
TYPE_FAR_DIST = 30     # 少し離れていても、これ以内で、2位との差が十分大きければ採用する(「確認」を付ける)
TYPE_FAR_MARGIN = 50   # そのときの、1位と2位の差の条件

_YY, _XX = np.mgrid[0:44, 0:44]
CIRCLE = ((_YY - 22) ** 2 + (_XX - 22) ** 2) <= 16 ** 2


ING_CX = (355, 473, 590)    # 食材3枠のアイコン中心x(画面は幅720固定)
ING_REF_DIR = HERE / "data" / "icons"
ING_NEAR = 14       # 基準画像との差(画素の平均絶対差)がこれ以内なら採用
ING_MARGIN = 6      # 別の食材のうち2番目に近いものとの差が、これ以上あること


def ing_crop(img, H, slot):
    """食材アイコンを切り出す(slot=1〜3)。数量ラベル(x2 など)は塗りつぶして、判定に使わない。"""
    iy = int(H - 332)
    x = ING_CX[slot - 1]
    p = img[iy - 42:iy + 42, x - 42:x + 42].copy()
    if p.shape[:2] != (84, 84):
        return None
    p[58:, 36:] = 255
    return p


def load_ing_refs():
    """data\\icons\\index.json に書かれた、食材の基準画像を読む。戻り値: [(食材名, 画像), ...]"""
    idx = ING_REF_DIR / "index.json"
    if not idx.is_file():
        return []
    refs = []
    for e in json.loads(idx.read_text(encoding="utf-8"))["refs"]:
        im = cv2.imread(str(ING_REF_DIR / e["file"]))
        if im is not None:
            refs.append((e["ja"], im.astype(np.float32)))
    return refs


def judge_ing(img, H, slot, refs):
    """戻り値: (一番近い食材名, 距離, 別の食材のうち2番目に近いものとの差)。切り出せなければ (None, None, None)。"""
    p = ing_crop(img, H, slot)
    if p is None or not refs:
        return None, None, None
    p = p.astype(np.float32)
    best = {}
    for ja, ref in refs:
        d = float(np.abs(p - ref).mean())
        if ja not in best or d < best[ja]:
            best[ja] = d
    s = sorted((d, ja) for ja, d in best.items())
    return s[0][1], s[0][0], (s[1][0] - s[0][0]) if len(s) > 1 else 99.0


def header_x(c, key):
    """ヘッダーのラベル(タイプ・せいべつ・お気に入り)の中心 x。アイコンはその真下にある。"""
    for t in c.toks:
        if 85 <= ef.cy(t) <= 125:
            if key == "type" and t.t == "タイプ":
                return int(ef.cx(t))
            if key == "gender" and "べつ" in t.t:
                return int(ef.cx(t))
            if key == "fav" and "入り" in t.t:
                return int(ef.cx(t))
    return None


def icon_color(img, x, half=22, y0=128, y1=172):
    """アイコンの代表色(RGB)。円の内側の、背景(白)ではない画素の中央値。"""
    p = img[y0:y1, x - half:x + half][..., ::-1].astype(float)
    if p.shape[:2] != (44, 44):
        return None
    keep = CIRCLE & (p.min(axis=2) < 235)
    return np.median(p[keep], axis=0) if keep.sum() > 40 else None

def judge_gender(img, x):
    """戻り値: (M / F / none / ?, メモ)"""
    patch = img[125:175, x - 30:x + 30]
    if patch.shape[:2] != (50, 60):
        return "?", "切り出し失敗"
    sat = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[..., 1] > 90
    if sat.sum() <= 30:
        return "none", ""
    b, g, r = patch[sat].mean(axis=0)
    if b > r + 40:
        return "M", ""
    if r > b + 40:
        return "F", ""
    return "?", f"色が曖昧 r={r:.0f} b={b:.0f}"


def judge_fav(img, x):
    patch = img[125:175, x - 30:x + 30]
    if patch.shape[:2] != (50, 60):
        return None, "切り出し失敗"
    sat = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[..., 1] > 90
    return bool(sat.sum() > 300), ""


def judge_type(img, x):
    """戻り値: (一番近いタイプ名, 距離, 2位との差)。色が取れなければ (None, None, None)。"""
    v = icon_color(img, x)
    if v is None:
        return None, None, None
    ds = sorted((float(np.linalg.norm(v - np.array(rgb, float))), name) for name, rgb in TYPE_REF.items())
    return ds[0][1], ds[0][0], ds[1][0] - ds[0][0]


def extract_one(path, img_dir, today, refs=()):
    n = path.stem
    toks, syms = ef.load_tokens(path)
    c = ef.Ctx(n, toks, syms, today)
    img = cv2.imread(str(img_dir / f"{n}.png"))
    rec, notes = {"id": n}, []
    if img is None:
        return rec, [("image", "画像が読めない")]
    xs = {k: header_x(c, k) for k in ("type", "gender", "fav")}
    for k, x in xs.items():
        if x is None:
            notes.append((k, "ヘッダーのラベルが見つからない"))
    if xs["gender"] is not None:
        g, memo = judge_gender(img, xs["gender"])
        rec["gender"] = {"M": "オス", "F": "メス", "none": "性別なし"}.get(g)
        if g == "?" or memo:
            notes.append(("gender", memo or "判定できない"))
    if xs["fav"] is not None:
        rec["favorite"], memo = judge_fav(img, xs["fav"])
        if memo:
            notes.append(("favorite", memo))
    if xs["type"] is not None:
        name, d, margin = judge_type(img, xs["type"])
        rec["type_color_dist"] = None if d is None else round(d, 1)
        if name is None:
            notes.append(("type", "アイコンの色が取れない"))
        elif d <= TYPE_NEAR_DIST and margin >= TYPE_NEAR_MARGIN:
            rec["type"] = name
        elif d <= TYPE_FAR_DIST and margin >= TYPE_FAR_MARGIN:
            rec["type"] = name
            notes.append(("type", f"基準色から少し離れているが、他のタイプとは十分に違うので採用: {name}(距離{d:.0f}, 差{margin:.0f})"))
        elif d > TYPE_FAR_DIST:
            notes.append(("type", f"基準色から遠く判定できない(最も近い={name}, 距離{d:.0f})"))
        else:
            notes.append(("type", f"他のタイプと色が近く判別が不確か(最も近い={name}, 差{margin:.0f})"))

    # 食材(3枠)。未解放の枠は薄く表示されるので、レベルで未解放と分かる枠は判定しない
    _, H = ef.anchors(c)
    if refs and H is not None:
        tmp = {}
        try:
            ef.ex_top(c, tmp)
        except Exception:
            pass
        lv = tmp.get("level")
        for slot in (1, 2, 3):
            if lv is not None and ((slot == 2 and lv < 30) or (slot == 3 and lv < 60)):
                continue
            name, d, margin = judge_ing(img, H, slot, refs)
            key = f"ingredient{slot}"
            if name is None:
                notes.append((key, "食材アイコンが切り出せない"))
            elif d <= ING_NEAR and margin >= ING_MARGIN:
                rec[key] = name
            else:
                notes.append((key, f"食材アイコンを判定できない(最も近い={name}, 距離{d:.0f}, 差{margin:.0f})"))
    return rec, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ocr-dir", default=str(HERE / "ocr"))
    ap.add_argument("--img-dir", default=str(HERE / "panoramas"))
    ap.add_argument("--out", default=str(HERE / "icons.json"))
    ap.add_argument("--ids", help="番号を指定(例: 1,2,10-12)。指定時はファイルに書かず、画面に表示する")
    a = ap.parse_args()

    files = sorted(Path(a.ocr_dir).glob("*.json"))
    if a.ids:
        want = set()
        for part in a.ids.split(","):
            lo, _, hi = part.partition("-")
            want.update(range(int(lo), int(hi or lo) + 1))
        files = [f for f in files if f.stem.isdigit() and int(f.stem) in want]
    if not files:
        sys.exit(f"OCR 結果がありません: {a.ocr_dir}")

    refs = load_ing_refs()
    if not refs:
        print("注意: data\\icons\\index.json が無いので、食材の判定は行いません")
    out, review = [], []
    for f in files:
        rec, notes = extract_one(f, Path(a.img_dir), date.today(), refs)
        out.append(rec)
        review += [{"id": rec["id"], "field": k, "reason": r} for k, r in notes]

    if a.ids:
        for rec in out:
            print(json.dumps(rec, ensure_ascii=False))
        for r in review:
            print(f"  [要確認] {r['id']} {r['field']}: {r['reason']}")
        return
    Path(a.out).write_text(json.dumps({"icons": out, "review": review}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(out)} 件を処理しました -> {a.out}")
    print(f"性別: {dict(Counter(r.get('gender') for r in out))}")
    print(f"お気に入り: {dict(Counter(r.get('favorite') for r in out))}")
    print(f"タイプ: {dict(Counter(r.get('type') for r in out))}")
    for s in (1, 2, 3):
        print(f"食材{s}: {dict(Counter(r.get(f'ingredient{s}') for r in out).most_common(8))}")
    print(f"要確認: {len(review)} 件 {dict(Counter(r['field'] for r in review))}")


if __name__ == "__main__":
    main()
