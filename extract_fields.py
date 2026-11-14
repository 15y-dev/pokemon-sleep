"""
extract_fields.py
ocr フォルダの JSON(Google Cloud Vision の OCR 結果)から、画面の項目を取り出して表にする。
通信は一切しない(OCR 済みの JSON を読むだけ)。標準ライブラリだけで動く。

出力(既定はこのフォルダ):
    pokemon.csv   1匹1行(Excel で開けるよう UTF-8 BOM つき)
    pokemon.json  同じ内容(JSON)
    review.csv    要確認の値(どの画像の、どの項目が、なぜ怪しいか)

使い方:
    python extract_fields.py                  # 全件
    python extract_fields.py --ids 1,2,3      # 指定した番号だけ(確認用。ファイルには書かず画面に表示)
    python extract_fields.py --as-of 2026-11-14   # 睡眠時間の妥当性チェックの基準日(既定は今日)

文字の位置(座標)を使って、ラベルの右隣などの値だけを読む。アイコンの種類
(きのみ・食材・タイプ・色など)は文字では取れないので、この段階では扱わない。
"""
import argparse
import csv
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict, namedtuple
from statistics import median
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

NATURES = ["がんばりや", "さみしがり", "ゆうかん", "いじっぱり", "やんちゃ", "ずぶとい", "すなお", "のんき", "わんぱく",
           "のうてんき", "おくびょう", "せっかち", "まじめ", "ようき", "むじゃき", "ひかえめ", "おっとり", "れいせい",
           "てれや", "うっかりや", "おだやか", "おとなしい", "なまいき", "しんちょう", "きまぐれ"]
# 性格ごとの「上がる効果」「下がる効果」(None=特徴なし)。
# 元は PokeAPI の natures.csv(上がる・下がるステータスの対応)で、ポケモンスリープでの効果名への対応は、
# 629匹の実データ(性格と、画面に出ている効果)で全25性格について確認した。
NATURE_TABLE = {
    "がんばりや": (None, None), "すなお": (None, None), "てれや": (None, None), "きまぐれ": (None, None),
    "まじめ": (None, None),
    "さみしがり": ("おてつだいスピード", "げんき回復量"), "いじっぱり": ("おてつだいスピード", "食材おてつだい確率"),
    "やんちゃ": ("おてつだいスピード", "メインスキル発生確率"), "ゆうかん": ("おてつだいスピード", "EXP獲得量"),
    "ずぶとい": ("げんき回復量", "おてつだいスピード"), "わんぱく": ("げんき回復量", "食材おてつだい確率"),
    "のうてんき": ("げんき回復量", "メインスキル発生確率"), "のんき": ("げんき回復量", "EXP獲得量"),
    "ひかえめ": ("食材おてつだい確率", "おてつだいスピード"), "おっとり": ("食材おてつだい確率", "げんき回復量"),
    "うっかりや": ("食材おてつだい確率", "メインスキル発生確率"), "れいせい": ("食材おてつだい確率", "EXP獲得量"),
    "おだやか": ("メインスキル発生確率", "おてつだいスピード"), "おとなしい": ("メインスキル発生確率", "げんき回復量"),
    "しんちょう": ("メインスキル発生確率", "食材おてつだい確率"), "なまいき": ("メインスキル発生確率", "EXP獲得量"),
    "おくびょう": ("EXP獲得量", "おてつだいスピード"), "せっかち": ("EXP獲得量", "げんき回復量"),
    "ようき": ("EXP獲得量", "食材おてつだい確率"), "むじゃき": ("EXP獲得量", "メインスキル発生確率"),
}
NEUTRAL_NATURES = {n for n, v in NATURE_TABLE.items() if v == (None, None)}   # 「せいかくによる特徴なし」の5種
NATURE_EFFECTS = ["おてつだいスピード", "EXP獲得量", "げんき回復量", "食材おてつだい確率", "メインスキル発生確率"]
SUBSKILLS = ["おてつだいスピードS", "おてつだいスピードM", "食材確率アップS", "食材確率アップM", "スキル確率アップS",
             "スキル確率アップM", "最大所持数アップS", "最大所持数アップM", "最大所持数アップL", "スキルレベルアップS",
             "スキルレベルアップM", "睡眠EXPボーナス", "ゆめのかけらボーナス", "リサーチEXPボーナス",
             "おてつだいボーナス", "げんき回復ボーナス", "きのみの数S"]
FIELDS = ["ワカクサ本島", "アンバー渓谷", "ラピスラズリ湖畔", "ゴールド旧発電所", "ウノハナ雪原", "シアンの砂浜", "トープ洞窟"]
SPECIALTIES = ["きのみ", "食材", "スキル", "オール"]
MAIN_SKILL_FIXES = {"パースト": "バースト"}  # OCR の典型的な誤読
# メインスキルの基本名(カッコ付きの別名スキルは「別名(基本名)」の形。例: ものまね(スキルコピー))。629匹の実データから作成。
MAIN_SKILLS = [
    "エナジーチャージS", "エナジーチャージM", "食材ゲットS", "げんきチャージS", "ゆめのかけらゲットS", "料理パワーアップS",
    "げんきオールS", "げんきエールS", "料理チャンスS", "きのみバースト", "ゆびをふる", "おてつだいサポートS",
    "食材セレクトS", "きのみゾーン", "料理アシストS", "おてつだいブースト",
]
SLOT_CX = (390, 512, 628)   # 食材3枠の中心x(画面は幅720固定)
SUB_SLOT_LV = (None, 25, 50, 70, 80)   # サブスキル5枠の解放レベル(1枠目は最初から解放)

Tok = namedtuple("Tok", "x0 y0 x1 y1 t")
Line = namedtuple("Line", "yc x0 text toks")
ARROWS = "▲▼AVv△▽"


def cx(t): return (t.x0 + t.x1) / 2
def cy(t): return (t.y0 + t.y1) / 2
def squash(s): return re.sub(r"\s+", "", s)


def box(vertices):
    xs = [p.get("x", 0) for p in vertices]
    ys = [p.get("y", 0) for p in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def load_tokens(path):
    """OCR の JSON から、単語(tokens)と文字(symbols)を座標つきで取り出す。"""
    resp = json.loads(path.read_text(encoding="utf-8"))["response"]
    toks = [Tok(*box(a["boundingPoly"]["vertices"]), a["description"])
            for a in resp.get("textAnnotations", [])[1:]]
    syms = []
    for page in resp.get("fullTextAnnotation", {}).get("pages", []):
        for b in page.get("blocks", []):
            for para in b.get("paragraphs", []):
                for w in para.get("words", []):
                    for s in w.get("symbols", []):
                        syms.append(Tok(*box(s["boundingBox"]["vertices"]), s["text"]))
    return toks, syms


def group_lines(toks, tol=12):
    """同じ高さにある単語を1行にまとめる(左から右へ連結)。"""
    groups = []
    for t in sorted(toks, key=cy):
        for g in groups:
            if abs(sum(cy(k) for k in g) / len(g) - cy(t)) <= tol:
                g.append(t)
                break
        else:
            groups.append([t])
    lines = []
    for g in groups:
        g.sort(key=lambda t: t.x0)
        lines.append(Line(sum(cy(k) for k in g) / len(g), g[0].x0, "".join(t.t for t in g), g))
    return sorted(lines, key=lambda l: l.yc)


def closest(value, choices, cutoff=0.7, loose=False):
    """候補の中から一番近いものを返す。戻り値: (結果, 状態)  状態: ok / fixed / unknown

    loose=True のときは、通常のしきい値に届かなくても、1位が十分近く(0.65以上)、
    2位との差が大きい(0.1以上)ときだけ採用する(名前の一部が欠けた誤読を拾うため)。
    """
    if value in choices:
        return value, "ok"
    m = difflib.get_close_matches(value, choices, n=1, cutoff=cutoff)
    if m:
        return m[0], "fixed"
    if loose and value:
        scores = sorted(((difflib.SequenceMatcher(None, value, ch).ratio(), ch) for ch in choices), reverse=True)
        if scores[0][0] >= 0.65 and scores[0][0] - scores[1][0] >= 0.1:
            return scores[0][1], "fixed"
    return value, "unknown"


def to_int(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


class Ctx:
    """1画像ぶんの OCR 結果と、要確認の記録。"""

    def __init__(self, id_, toks, syms, today):
        self.id, self.toks, self.syms, self.today = id_, toks, syms, today
        self.review = []

    def flag(self, field, value, reason, sev="mid"):
        """要確認の記録。sev: high=値が取れていない/明らかにおかしい, mid=疑わしい, low=補正した/参考"""
        self.review.append({"id": self.id, "field": field, "value": value, "reason": reason, "severity": sev})

    def sel(self, xmin, xmax, ymin, ymax, src=None):
        return [t for t in (self.toks if src is None else src)
                if xmin <= cx(t) <= xmax and ymin <= cy(t) <= ymax]

    def lines(self, xmin, xmax, ymin, ymax, tol=12):
        return group_lines(self.sel(xmin, xmax, ymin, ymax), tol)

    def text(self, xmin, xmax, ymin, ymax):
        return "".join(l.text for l in self.lines(xmin, xmax, ymin, ymax))

def joined(toks):
    return "".join(t.t for t in sorted(toks, key=lambda k: k.x0))


def parse_lv(row):
    """行の単語列から「Lv.14」を読む(「Lv」「.」「80」に割れる場合も)。戻り値: (レベル, 次の単語の位置)"""
    for i, t in enumerate(row):
        m = re.fullmatch(r"Lv\.?(\d+)", t.t)
        if m:
            return int(m.group(1)), i + 1
        if t.t.rstrip(".") == "Lv":
            j, digits = i + 1, ""
            while j < len(row) and re.fullmatch(r"\.|\d+", row[j].t):
                digits += row[j].t.replace(".", "")
                j += 1
            if digits:
                return int(digits), j
    return None, 0


def anchors(c):
    """画面の基準位置。Y=「詳細ステータス」の高さ, H=「メインスキル・サブスキル」の高さ。"""
    Y = next((cy(t) for t in sorted(c.toks, key=cy) if t.t == "詳細" and cx(t) < 200), None)
    if Y is None:
        back = next((cy(t) for t in c.toks if t.t == "もどる" and cx(t) < 200), None)
        Y = back - 584 if back else None
    H = next((cy(t) for t in sorted(c.toks, key=cy)
              if t.t == "メイン" and cx(t) < 170 and 1450 <= cy(t) <= 1750), None)
    if H is None and Y is not None:
        H = Y - 676
    return Y, H


def ex_top(c, rec):
    """画面の上部: SP / Lv / ニックネーム / アメ / EXP / 機嫌 / 特技 / リボン / 進化ボタン"""
    cand = [t for t in c.sel(90, 345, 100, 190) if re.fullmatch(r"\d[\d,]*", t.t)]
    rec["sp"] = to_int(max(cand, key=lambda k: (k.x1 - k.x0) * (k.y1 - k.y0)).t) if cand else None
    if rec["sp"] is None:
        c.flag("sp", "", "SPが読めない", "high")

    row = sorted((t for t in c.toks if 590 <= cy(t) <= 690 and cx(t) < 640), key=lambda t: t.x0)
    level, k = parse_lv(row)
    rec["level"] = level
    if level is None:
        c.flag("level", joined(row), "Lvが読めない", "high")
    name_toks = row[k:] if level is not None else []
    if len(name_toks) >= 2:  # 名前の右にある鉛筆アイコンが記号として混ざる(例: 「!」「/」)
        last, prev = name_toks[-1], name_toks[-2]
        if re.fullmatch(r"\W{1,2}", last.t) and last.x0 - prev.x1 > 20:
            name_toks = name_toks[:-1]
    rec["name_text"] = "".join(t.t for t in name_toks) or None
    if rec["name_text"] is None:
        c.flag("name_text", "", "名前が読めない", "high")

    # 「◯◯のアメ」。個体によって左に「種類」の枠が増えて位置が動くので、「アメ」から左へ、隣り合う文字だけをたどる
    row2 = sorted(c.sel(40, 470, 690, 742), key=lambda t: t.x0)
    idx = max((i for i, t in enumerate(row2) if t.t.endswith("アメ")), default=None)
    species, ccx, form = None, None, None
    if idx is not None:
        parts = [row2[idx]]
        j = idx - 1
        while j >= 0 and parts[0].x0 - row2[j].x1 <= 30:   # 同じ枠の文字は隣り合う。別の枠とは大きく離れる
            parts.insert(0, row2[j])
            j -= 1
        m = re.fullmatch(r"(.+)のアメ", joined(parts))
        species = m.group(1) if m else None
        ccx = sum(cx(t) for t in parts) / len(parts)
        left = [t for t in c.sel(40, parts[0].x0 - 30, 690, 800) if re.search(r"\w", t.t)]
        form = "".join(t.t for t in sorted(left, key=lambda k: (round(cy(k) / 15), k.x0))) or None
    rec["candy_species"] = species
    rec["form_text"] = form   # アメの左にある枠(「種類」など)の文字。無い個体が多い
    if species is None:
        c.flag("candy_species", "", "「◯◯のアメ」が読めない", "mid")
    rec["name_is_candy_species"] = (rec["name_text"] == species) if species and rec["name_text"] else None

    nums = [t for t in c.sel(0, 720, 742, 800)
            if ccx is not None and re.fullmatch(r"\d[\d,]*", t.t) and abs(cx(t) - ccx) <= 110]
    rec["candy_count"] = to_int(nums[-1].t) if nums else None
    m = re.search(r"EXP([\d,]+)", joined(c.sel(380, 700, 742, 800)))
    rec["exp_to_next"] = to_int(m.group(1)) if m else None
    if rec["exp_to_next"] is None:
        c.flag("exp_to_next", "", "「あと EXP」が読めない", "low")

    m = re.search(r"(\d{1,3})%", joined(c.sel(60, 270, 440, 520)))
    rec["mood_percent"] = int(m.group(1)) if m else None  # 画面に出ない個体もあるので、無くても要確認にしない
    st = joined(c.sel(150, 560, 525, 585))
    rec["status_note"] = st or None

    lab = [t for t in c.sel(380, 680, 90, 125) if "とくい" in t.t or t.t in ("な", "もの", "なもの")]
    center = (min(t.x0 for t in lab) + max(t.x1 for t in lab)) / 2 if lab else 560
    # バッジの幅は約95px。右隣のリボンのアイコンが「村」などの文字になって混ざらないよう、幅を絞る
    sp_txt = joined(t for t in c.sel(center - 55, center + 55, 125, 175) if not re.fullmatch(r"[\d,%]+", t.t))
    # 小さい文字の「材」が「村」に誤読される(「食材」→「村」「宮村」など)ので、先に直してから照合する
    spec, st_ = closest(sp_txt.replace("村", "材"), SPECIALTIES, 0.5) if sp_txt else ("", "unknown")
    rec["specialty"] = spec or None
    if not sp_txt:
        c.flag("specialty", "", "とくいなもの(バッジの文字)が読めない", "low")
    elif st_ != "ok":
        c.flag("specialty", sp_txt, f"補正または未知: {spec}", "low" if st_ == "fixed" else "mid")

    rec["ribbon_present"] = any(t.t == "リボン" for t in c.sel(380, 680, 90, 125))
    rec["has_evolve_button"] = any(t.t == "進化" and 150 <= cx(t) <= 320 and 820 <= cy(t) <= 890 for t in c.toks)


def parse_duration(s):
    """「1時間11分25秒」「35分51秒」「200時間55分」などを秒に直す。戻り値: 秒(読めなければ None)"""
    m = re.fullmatch(r"(?:(\d+)時間)?(?:(\d+)分)?(?:(\d+)秒)?", s)
    if not m or not any(m.groups()):
        return None
    h, mi, se = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + se


def ex_middle(c, rec, H):
    """おてつだい能力: きのみ・食材の個数, おてつだい時間, 最大所持数"""
    # 「x2」の位置: きのみ(1枠)は上の行、食材(3枠)は下の行(y≈1280 付近)にある
    berry, ingr = [], []
    for t in c.toks:
        if re.fullmatch(r"[xX×]\d{1,2}", t.t) and 1000 <= cy(t) <= H - 150:
            (berry if cy(t) < 1180 else ingr).append(t)
    rec["berry_count"] = int(berry[0].t[1:]) if berry else None
    slots = [None, None, None]
    for t in ingr:
        i = min(range(3), key=lambda k: abs(SLOT_CX[k] - cx(t)))
        if abs(SLOT_CX[i] - cx(t)) <= 70:
            slots[i] = int(t.t[1:])
    for i in range(3):
        rec[f"ingredient{i + 1}_count"] = slots[i]
    # 未解放(薄く表示)の枠は「x5」が読めないことがある。レベルで未解放と分かる枠は欠けていても正常として扱う
    lv0 = rec.get("level")
    need = [True, lv0 is None or lv0 >= 30, lv0 is None or lv0 >= 60]
    miss = [i + 1 for i in range(3) if slots[i] is None and need[i]]
    if miss:
        c.flag("ingredient_counts", str(slots), f"解放済みなのに個数が読めない枠: {miss}", "mid")
    elif any(s is None for s in slots):
        c.flag("ingredient_counts", str(slots), "未解放の枠の個数が読めない(未解放なので問題なし)", "low")
    # 食材2・3枠の未解放(鍵つきバッジ「Lv.30」「Lv.60」)
    lock = {30: False, 60: False}
    for t in c.toks:
        m = re.fullmatch(r"Lv\.?(30|60)", t.t)
        if m and 1150 <= cy(t) <= 1260:
            lock[int(m.group(1))] = True
    # 解放はレベルで決まる(食材2は Lv30、食材3は Lv60)。OCR のバッジより確実なので、レベルを正とし、食い違いは記録する
    lv = rec.get("level")
    if lv is not None:
        exp_lock = {30: lv < 30, 60: lv < 60}
        if any(lock[k] and not exp_lock[k] for k in lock):   # 解放済みのはずなのに鍵が見える = レベルか鍵の誤読
            c.flag("ingredient_lock", f"Lv{lv} バッジ={lock}", "食材の鍵バッジがレベルと矛盾する(レベルの誤読の疑い)", "mid")
        elif exp_lock != lock:   # 鍵バッジの読み落としは普通にある。レベルから未解放と分かるので問題なし
            c.flag("ingredient_lock", f"Lv{lv} バッジ={lock}", "鍵バッジの読み落とし(レベルから未解放と判断)", "low")
        lock = exp_lock
    rec["ingredient2_locked"], rec["ingredient3_locked"] = lock[30], lock[60]

    # おてつだい時間(「◯時間◯分◯秒ごと」)。ラベル「おてつだい 時間」と同じ高さの右側。
    # ラベルが読めない・「ごと」が欠ける画像もあるので、ラベルの有無に頼らず、時間として読める部分を取る
    ys = [cy(t) for t in c.toks if t.t == "時間" and cx(t) < 230 and 1250 <= cy(t) <= H - 150]
    yl = ys[0] if ys else H - 211   # ラベルが無いときは、メインスキル見出しとの距離(約211px上)から推定
    raw = "".join(t.t for t in sorted(c.sel(290, 640, yl - 25, yl + 25), key=lambda k: k.x0))
    body = re.split(r"ごと", re.sub(r"[①ⓘ④\(\)\s]", "", raw))[0]
    body = re.sub(r"[^0-9時間分秒]", "", body)
    secs = parse_duration(body)
    rec["help_interval_text"] = body or None
    rec["help_interval_sec"] = secs
    if secs is None:
        c.flag("help_interval", raw, "おてつだい時間が読めない", "high")
    elif not 1200 <= secs <= 7200:
        c.flag("help_interval", raw, f"{secs}秒は想定(20分〜2時間)の範囲外", "mid")

    # 最大所持数(「N個」)。ラベルが読めない画像もあるので、ラベルの位置は使わず、この帯(おてつだい時間の下)から探す
    val = None
    for t in sorted(c.sel(280, 660, H - 190, H - 60), key=cy):
        m = re.fullmatch(r"(\d+)個", t.t)
        if m is None and re.fullmatch(r"\d+", t.t):   # 「10」「個」と分かれる場合(「個」が数字の右隣にある)
            if any(k.t == "個" and abs(cy(k) - cy(t)) < 15 and -15 <= k.x0 - t.x1 < 30 for k in c.toks):
                m = re.fullmatch(r"(\d+)", t.t)
        if m:
            val = int(m.group(1))
            break
    rec["max_carry"] = val
    if val is None:
        c.flag("max_carry", "", "最大所持数が読めない", "high")


DASH = re.compile(r"[-ー－−—–_.]+")   # 未設定の枠に出る「-」
BADGE = re.compile(r"L[vu]\.?\d*|\.|\d{1,3}")   # 「Lv」が「Lu」に誤読されることがある(0359 の Lv.80 バッジ)   # 「Lv.25」や、「Lv」「.」「80」に割れたバッジの破片


def ex_skills(c, rec, H, Y):
    """メインスキル(名前・Lv・説明)とサブスキル(5枠: 名前・解放済みか)"""
    row = [t for t in c.toks if H + 85 <= cy(t) <= H + 140 and cx(t) < 640]
    lv, _ = parse_lv(sorted((t for t in row if cx(t) > 560), key=lambda t: t.x0))
    rec["main_skill_level"] = lv
    name = squash(joined(t for t in row if cx(t) <= 560))
    for bad, good in MAIN_SKILL_FIXES.items():
        name = name.replace(bad, good)
    # 末尾の「S」「M」が「5」「ー」などに誤読されることがある。「別名(基本名)」の形も含め、基本名を一覧と照合して直す
    m_ = re.fullmatch(r"(.*?)(?:\((.+?)\))?", name)
    if m_ and name:
        outer, inner = m_.group(1), m_.group(2)
        def fix(s):
            if not s or s in MAIN_SKILLS:
                return s
            hit = difflib.get_close_matches(s, MAIN_SKILLS, n=1, cutoff=0.8)
            return hit[0] if hit else s
        new_inner = fix(inner) if inner else None
        new_outer = fix(outer) if not inner else outer   # 別名つきのときは、外側は別名(固有名)なので直さない
        fixed = f"{new_outer}({new_inner})" if inner else new_outer
        if fixed != name:
            c.flag("main_skill", name, f"一覧に一番近いもので補正: {fixed}", "low")
            name = fixed
    rec["main_skill"] = name or None
    if not name:
        c.flag("main_skill", "", "メインスキル名が読めない", "high")
    if lv is None:
        c.flag("main_skill_level", "", "メインスキルのLvが読めない", "mid")
    desc = [l.text for l in group_lines([t for t in c.sel(160, 640, H + 150, H + 262)
                                         if not re.fullmatch(r"Lv\.?\d*", t.t)])]   # 数字(「6個」など)は残す
    rec["main_skill_desc"] = "".join(desc) or None

    # サブスキル: 1行目=(1枠目,2枠目) 2行目=(3枠目,4枠目) 3行目=(5枠目)。バッジ類は除いて名前だけを行にまとめる
    region = [t for t in c.sel(40, 700, H + 285, Y - 30) if not BADGE.fullmatch(t.t)]
    # 鍵バッジの南京錠アイコンが「✰」「Ô」「Lu」などの記号になって、余分な行ができることがある。
    # 名前の行には必ず仮名・漢字が2文字以上ある(未設定の枠は「-」だけ)ので、どちらでもない行は捨てる
    def is_name_line(ln):
        return bool(re.search(r"[ぁ-んァ-ヶ一-龥]{2}", ln.text)) or bool(DASH.fullmatch(squash(ln.text)))
    lines = [ln for ln in group_lines(region, tol=14) if is_name_line(ln)]
    cells = []
    for ln in lines:
        left = [t for t in ln.toks if cx(t) < 350]
        right = [t for t in ln.toks if cx(t) >= 350]
        cells.append((squash(joined(left)), squash(joined(right))))
    order = []
    if len(cells) >= 1:
        order += [cells[0][0], cells[0][1]]
    if len(cells) >= 2:
        order += [cells[1][0], cells[1][1]]
    if len(cells) >= 3:
        order += [cells[2][0]]
    order += [""] * (5 - len(order))
    names, empty_idx, ambiguous = [], [], {}
    for i, txt in enumerate(order[:5]):
        if DASH.fullmatch(txt):   # 未設定の枠は、名前の代わりに「-」が表示される
            names.append(None)
            c.flag(f"sub_skill{i + 1}", txt, "サブスキルが未設定(「-」表示)", "low")
            continue
        if not txt:
            names.append(None)
            empty_idx.append(i)
            continue
        # 末尾の「S」「M」「L」だけが読めないことがある(例: 「おてつだいスピード」)。S と M のどちらか決められないので、
        # 黙って S にはせず、後で「同じサブスキルは重複しない」ことを使って決める
        pref = [n for n in SUBSKILLS if txt not in SUBSKILLS and len(txt) >= 4 and n.startswith(txt)]
        if len(pref) >= 2:
            names.append(None)
            ambiguous[i] = (txt, pref)
            continue
        best, st_ = closest(txt, SUBSKILLS, 0.75, loose=True)
        names.append(best)
        if st_ == "fixed":
            c.flag(f"sub_skill{i + 1}", txt, f"一覧に一番近いもので補正: {best}", "low")
        elif st_ == "unknown":
            c.flag(f"sub_skill{i + 1}", txt, "サブスキルの一覧に無い(新しい種類か誤読)", "mid")
    # 末尾が読めなかった枠は、同じ記録にすでにあるサブスキルを除いて決める(1匹に同じサブスキルは付かない)
    used = {n for n in names if n}
    for i, (txt, pref) in ambiguous.items():
        rest = [p for p in pref if p not in used]
        if len(rest) == 1:
            names[i] = rest[0]
            used.add(rest[0])
            c.flag(f"sub_skill{i + 1}", txt, f"末尾のS/M/Lが読めず、同じサブスキルは重複しないことから推定: {rest[0]}", "low")
        else:
            names[i] = txt + "?"
            c.flag(f"sub_skill{i + 1}", txt, f"末尾のS/M/Lが読めず、候補を絞れない: {' / '.join(rest)}", "mid")
    # 解放済みか: 枠の上に鍵つきバッジ(「Lv.◯◯」)があれば未解放。
    # バッジの数字(25/50/70/80)は誤読されやすい(80→50など)ので、数字は見ずに「どの行のどちら側にあるか」で枠を決める。
    #   (行, 右列か) -> 枠: 1行目右=2枠目, 2行目左=3枠目, 2行目右=4枠目, 3行目左=5枠目
    # 行は、1行目の名前の高さ(L0)を基準に、約113pxおきに並ぶ(バッジは名前より約36px上)。
    # 「-」の行は文字が読めず行ごと欠けることがあるので、欠けた行も位置から数える。
    slot_of = {(0, True): 1, (1, False): 2, (1, True): 3, (2, False): 4}
    locked = [False] * 5
    L0 = lines[0].yc if lines else None
    for b in (t for t in c.sel(40, 700, H + 200, Y - 30) if re.fullmatch(r"L[vu]\.?\d*", t.t)):
        if L0 is None:
            break
        ri = round((cy(b) + 36 - L0) / 113)
        if (ri, cx(b) >= 350) in slot_of:
            locked[slot_of[(ri, cx(b) >= 350)]] = True
    for i in empty_idx:
        if locked[i]:   # 鍵つきバッジはあるが名前が無い = 未設定の枠(「-」は小さく、OCR が読まないことがある)
            c.flag(f"sub_skill{i + 1}", "", "名前が無く鍵バッジのみ(未設定の枠とみなす)", "low")
        else:
            c.flag(f"sub_skill{i + 1}", "", "サブスキル名が読めない", "mid")
    # 名前の行が足りないのは、欠けた枠が「-」(未設定)のとき(上で鍵バッジから判定済み)なら正常。
    # 鍵バッジも無いのに行が欠けているときだけ、読み落としとして警告する。
    slots_of_row = ((0, 1), (2, 3), (4,))   # 行ごとの枠(0始まり)
    for r in range(len(lines), 3):
        if not all(locked[s] for s in slots_of_row[r]):
            c.flag("sub_skills", str([ln.text for ln in lines]), f"サブスキルの{r + 1}行目が読めない(鍵バッジも無い)", "mid")
    for n, k in Counter(n for n in names if n and not n.endswith("?")).items():
        if k > 1:   # 1匹に同じサブスキルは付かない
            c.flag("sub_skills", n, f"同じサブスキル「{n}」が{k}つある(あり得ないので、読み違いの疑い)", "mid")
    for i in range(5):
        rec[f"sub_skill{i + 1}"] = names[i]
        rec[f"sub_skill{i + 1}_unlocked"] = not locked[i]

def ex_detail(c, rec, Y):
    """詳細ステータス: せいかく, せいかくの効果, 出会った日, 出会ったフィールド"""
    nat = [t for t in c.toks if "せいかく" in t.t and cx(t) < 200 and Y + 40 <= cy(t) <= Y + 130]
    yl = cy(nat[0]) if nat else Y + 80
    # 名前は1行だが、文字が別の行として分かれて読まれることがあるので、枠の中を位置順につなぐ
    ntoks = c.sel(100, 340, yl + 10, yl + 75)
    raw = squash(joined(ntoks))
    nat_right = max((t.x1 for t in ntoks), default=None)   # 文字列の右端(名前は枠の中央に置かれるので、長さの目安になる)
    rec["nature_text"] = raw or None
    rec["nature"] = None
    nat_state = "empty"
    if raw:
        best, nat_state = closest(raw, NATURES, 0.6)
        rec["nature"] = best if nat_state != "unknown" else None
        if nat_state == "fixed":
            c.flag("nature", raw, f"一覧に一番近いもので補正: {best}", "low")

    neutral, effs = False, []    # effs: [効果名, 向き(up/down/None)] を、画面の上の行から順に
    for ln in group_lines(c.sel(350, 700, yl - 12, yl + 95)):
        txt = squash(ln.text)
        if "特徴なし" in txt:
            neutral = True
            continue
        label = txt.rstrip(ARROWS)
        if not label:
            continue
        best, st_ = closest(label, NATURE_EFFECTS, 0.7)
        if st_ == "unknown":
            c.flag("nature_effect", label, "せいかくの効果名が一覧に無い", "mid")
            continue
        tail = txt[len(label):]   # 効果名のあとの矢印(▲▲ / ▼▼。「A」「V」に誤読されることもある)
        effs.append([best, None if not tail else ("up" if tail[0] in "▲△A" else "down")])
    # 画面では、上がる効果(▲)が上の行、下がる効果(▼)が下の行。矢印が読めないときは、この並び順から決める
    if len(effs) == 2:
        d0, d1 = effs[0][1], effs[1][1]
        if (d0, d1) in (("up", "down"), ("up", None), (None, "down"), (None, None)):
            if (d0, d1) != ("up", "down"):
                c.flag("nature_effect", str(effs), "矢印が読めないので、並び順(上=▲,下=▼)から推定", "low")
            effs[0][1], effs[1][1] = "up", "down"
        else:   # 読めた矢印が並び順と食い違う。読めた矢印を優先し、読めなかった側はその反対にする
            c.flag("nature_effect", str(effs), "矢印の向きが並び順(上=▲,下=▼)と食い違う", "mid")
            d0 = d0 or ("down" if d1 == "up" else "up")
            d1 = d1 or ("down" if d0 == "up" else "up")
            effs[0][1], effs[1][1] = d0, d1
    elif not neutral:
        c.flag("nature_effect", str(effs), f"せいかくの効果が{len(effs)}個しか読めない", "mid")
    seen_up = next((e for e, d in effs if d == "up"), None)      # 画面から読めた効果(参考。性格の確認に使う)
    seen_down = next((e for e, d in effs if d == "down"), None)
    rec["nature_up"], rec["nature_down"], rec["nature_neutral"] = seen_up, seen_down, neutral
    if nat_state in ("unknown", "empty"):
        # 名前の前半が読めないことがある(「てれや」→「や」)。末尾が一致し、画面の効果(上がる・下がる、特徴なし)とも
        # 合う候補が1つだけなら採用する
        cand = [n for n in NATURES if raw and n.endswith(raw) and (n in NEUTRAL_NATURES) == neutral
                and (neutral or NATURE_TABLE[n] == (seen_up, seen_down))]
        if len(cand) > 1 and nat_right is not None:
            # 枠の中央(x≈198)に1文字約22pxで並ぶので、右端の位置から文字数を推定して絞る
            exp_right = lambda n: 198 + 11 * len(n)
            cand.sort(key=lambda n: abs(exp_right(n) - nat_right))
            if abs(exp_right(cand[0]) - nat_right) + 12 < abs(exp_right(cand[1]) - nat_right):
                cand = cand[:1]
                reason = f"名前の一部しか読めず、末尾・効果の有無・文字列の長さから推定: {cand[0]}"
            else:
                reason = "候補を絞れない"
        else:
            reason = f"名前の一部しか読めず、末尾と効果の有無から推定: {cand[0]}" if cand else ""
        if len(cand) == 1:
            rec["nature"] = cand[0]
            c.flag("nature", raw, reason, "low" if "末尾と効果の有無から" in reason else "mid")
        elif nat_state == "unknown":
            c.flag("nature", raw, "せいかくの一覧に無い", "mid")
        else:
            c.flag("nature", "", "せいかくが読めない", "high")

    # 上がる・下がる効果は、性格で決まる。性格が決まったら、表から求める(矢印を読む必要はない)。
    # 画面から読めた効果は、性格の読み間違いを見つける検証にだけ使う。
    nat = rec.get("nature")
    if nat in NATURE_TABLE:
        up, down = NATURE_TABLE[nat]
        screen = (seen_up, seen_down)
        if neutral and (up, down) != (None, None):
            c.flag("nature", nat, f"画面は「特徴なし」だが、{nat}には効果がある(性格の誤読の疑い)", "mid")
        elif not neutral and (seen_up or seen_down) and screen != (up, down):
            c.flag("nature", nat, f"画面の効果(上={seen_up}, 下={seen_down})が、{nat}の効果(上={up}, 下={down})と合わない", "mid")
        rec["nature_up"], rec["nature_down"], rec["nature_neutral"] = up, down, (up, down) == (None, None)

    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", "".join(l.text for l in c.lines(380, 700, Y + 215, Y + 330)))
    rec["met_date"] = None
    if m:
        try:
            rec["met_date"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            c.flag("met_date", m.group(0), "存在しない日付", "high")
    else:
        c.flag("met_date", "", "出会った日が読めない", "high")

    fl = squash("".join(l.text for l in c.lines(380, 700, Y + 295, Y + 385)))
    ex = fl.startswith(("◆", "◇")) or fl.endswith("EX")
    fl = re.sub(r"^[◆◇◈♦]+|EX$", "", fl).strip()
    best, st_ = closest(fl, FIELDS, 0.6) if fl else ("", "unknown")
    rec["field"] = best if st_ != "unknown" else (fl or None)
    rec["field_ex"] = ex
    if not fl:
        c.flag("field", "", "出会ったフィールドが読めない", "high")
    elif st_ == "fixed":
        c.flag("field", fl, f"一覧に一番近いもので補正: {best}", "low")
    elif st_ == "unknown":
        c.flag("field", fl, "フィールドの一覧に無い(新しい場所か誤読)", "mid")


def ex_sleep(c, rec, Y):
    """一緒に眠った時間。手前にある「Zz」アイコンが文字として混ざるので、文字の位置で切り離す。"""
    ysl = Y + 404
    seg = sorted((s for s in c.syms if abs(cy(s) - ysl) <= 30 and cx(s) > 330), key=lambda s: s.x0)
    body = [s for s in seg if s.x0 >= 413]   # アイコンは x≈370〜410、数字はそれより右から始まる
    raw = re.sub(r"\s+", "", "".join(s.t for s in body))
    full = "".join(s.t for s in seg)
    if re.fullmatch(r"0\d{1,2}", raw):   # 「0分」の「分」が数字に誤読される(例: 053, 05)。先頭が0で数字が続く値は実在しない
        c.flag("sleep_time", full, f"「分」の誤読とみて0分に補正: {raw}", "low")
        raw = "0分"
    rec["sleep_text"] = raw or None
    secs = parse_duration(raw)
    rec["sleep_minutes"] = secs // 60 if secs is not None else None
    if not raw:
        c.flag("sleep_time", full, "一緒に眠った時間が読めない", "high")
        return
    hm = re.fullmatch(r"(?:(\d+)時間)?(?:(\d+)分)?", raw)
    if secs is None or not hm:
        c.flag("sleep_time", full, f"読み取れない形: {raw}", "high")
    elif hm.group(2) and int(hm.group(2)) >= 60:
        c.flag("sleep_time", full, f"分が60以上(誤読の疑い): {raw}", "high")
    elif rec.get("met_date"):
        days = (c.today - date.fromisoformat(rec["met_date"])).days
        if secs / 3600 > max(days, 0) * 24:
            c.flag("sleep_time", full, f"出会ってから{days}日なのに睡眠時間が{raw}(あり得ない)", "high")
    if raw and not re.search(r"(分|時間)$", raw):
        c.flag("sleep_time", full, f"末尾が「分」「時間」でない(欠けた疑い): {raw}", "high")

COLUMNS = [
    "id", "level", "species", "nickname", "name_text", "candy_species", "name_is_candy_species",
    "type", "berry", "gender", "favorite", "sp", "exp_to_next", "candy_count",
    "mood_percent", "status_note", "specialty", "ribbon_present", "has_evolve_button",
    "berry_count", "ingredient1", "ingredient1_count", "ingredient2", "ingredient2_count",
    "ingredient3", "ingredient3_count",
    "ingredient2_locked", "ingredient3_locked", "help_interval_sec", "help_interval_text", "max_carry",
    "main_skill", "main_skill_level", "main_skill_desc",
    "sub_skill1", "sub_skill2", "sub_skill3", "sub_skill4", "sub_skill5",
    "sub_skill1_unlocked", "sub_skill2_unlocked", "sub_skill3_unlocked", "sub_skill4_unlocked", "sub_skill5_unlocked",
    "nature", "nature_up", "nature_down", "nature_neutral", "nature_text",
    "met_date", "field", "field_ex", "sleep_minutes", "sleep_text", "form_text", "note",
]


# CSV の見出し(日本語)。キーは内部名(JSON はこの内部名のまま出力する)
HEADERS_JA = {
    "id": "番号", "level": "レベル", "species": "種名", "nickname": "ニックネーム",
    "name_text": "名前の欄(OCR)", "candy_species": "アメの系統名",
    "name_is_candy_species": "種名とアメの系統名が同じ", "type": "タイプ", "gender": "性別", "favorite": "お気に入り",
    "sp": "SP", "exp_to_next": "次のレベルまでのEXP",
    "candy_count": "アメの個数", "mood_percent": "機嫌(%)", "status_note": "状態バッジ",
    "specialty": "とくいなもの", "ribbon_present": "リボンあり", "has_evolve_button": "進化ボタンあり",
    "berry": "きのみ", "ingredient1": "食材1", "ingredient2": "食材2", "ingredient3": "食材3",
    "berry_count": "きのみの個数", "ingredient1_count": "食材1の個数", "ingredient2_count": "食材2の個数",
    "ingredient3_count": "食材3の個数", "ingredient2_locked": "食材2は未解放", "ingredient3_locked": "食材3は未解放",
    "help_interval_sec": "おてつだい時間(秒)", "help_interval_text": "おてつだい時間(表示)",
    "max_carry": "最大所持数", "main_skill": "メインスキル", "main_skill_level": "メインスキルLv",
    "main_skill_desc": "メインスキルの説明",
    "sub_skill1": "サブスキル1", "sub_skill2": "サブスキル2", "sub_skill3": "サブスキル3",
    "sub_skill4": "サブスキル4", "sub_skill5": "サブスキル5",
    "sub_skill1_unlocked": "サブスキル1は解放済み", "sub_skill2_unlocked": "サブスキル2は解放済み",
    "sub_skill3_unlocked": "サブスキル3は解放済み", "sub_skill4_unlocked": "サブスキル4は解放済み",
    "sub_skill5_unlocked": "サブスキル5は解放済み",
    "nature": "せいかく", "nature_up": "せいかくで上がる効果", "nature_down": "せいかくで下がる効果",
    "nature_neutral": "せいかくによる特徴なし", "nature_text": "せいかく(OCR生の文字)",
    "met_date": "出会った日", "field": "出会ったフィールド", "field_ex": "EXフィールド",
    "sleep_minutes": "一緒に眠った時間(分)", "sleep_text": "一緒に眠った時間(表示)",
    "form_text": "姿(バケッチャなど)", "note": "備考",
}
REVIEW_HEADERS_JA = {"severity": "重要度", "id": "番号", "field": "項目", "value": "読み取った値", "reason": "理由"}
SEVERITY_JA = {"high": "高(要修正)", "mid": "中(要確認)", "low": "低(補正済み・参考)"}
# review.csv の「項目」(内部名)を日本語にする。HEADERS_JA に無いもの(処理の段階名など)はここで補う
FIELD_JA = dict(HEADERS_JA, **{
    "ingredient_counts": "食材の個数", "ingredient_lock": "食材の解放状態", "help_interval": "おてつだい時間",
    "sub_skills": "サブスキル(全体)", "sleep_time": "一緒に眠った時間", "nature_effect": "せいかくの効果",
    "layout": "画面の位置合わせ", "gender": "性別", "favorite": "お気に入り", "type": "タイプ",
    "species": "種名", "ex_top": "上部の読み取り", "ex_middle": "おてつだい能力の読み取り",
    "ex_skills": "スキルの読み取り", "ex_detail": "詳細ステータスの読み取り", "ex_sleep": "睡眠時間の読み取り",
})


def ja_cell(v):
    """CSV に書くときの表示。True/False は「はい/いいえ」、None は空欄。"""
    if v is True:
        return "はい"
    if v is False:
        return "いいえ"
    return "" if v is None else v


def load_species_names():
    """ポケモンの名前一覧(data\\pokemon_names.json)を読む。無ければ None(種名の確定は行わない)。"""
    p = HERE / "data" / "pokemon_names.json"
    if not p.is_file():
        return None
    return [s["ja"] for s in json.loads(p.read_text(encoding="utf-8"))["species"]]


def resolve_species(rec, names, c):
    """名前の欄とアメの系統名を、一覧と照合して、種名・ニックネームに分ける。

    - 名前の欄が一覧にある → 種名(進化後の名前もこれで確定する)
    - 一覧にない → ニックネーム(種名は推測しない。系統名だけは分かる)
    - 系統名(「◯◯のアメ」)が一覧にないときは、一番近い名前に直す(例: ウバー→ウパー)
    """
    fam = rec.get("candy_species")
    if fam and fam not in names:
        m = difflib.get_close_matches(fam, names, n=1, cutoff=0.6)
        if not m:   # 「パ」→「ハ」のように、濁点・半濁点が落ちた誤読は、清音にそろえて比べる
            base = unicodedata.normalize("NFD", fam)
            base = "".join(ch for ch in base if ch not in "\u3099\u309a")
            same = [n for n in names
                    if "".join(ch for ch in unicodedata.normalize("NFD", n) if ch not in "\u3099\u309a") == base]
            m = same[:1] if len(same) == 1 else []
        if m:
            c.flag("candy_species", fam, f"一覧に一番近いもので補正: {m[0]}", "low")
            rec["candy_species"] = fam = m[0]
        else:
            c.flag("candy_species", fam, "ポケモンの名前一覧に無い(誤読か新しいポケモン)", "mid")
    name = rec.get("name_text")
    if name in names:
        rec["species"], rec["nickname"] = name, None
    else:
        rec["species"], rec["nickname"] = None, name
        if name:
            c.flag("species", name, "名前が一覧に無いのでニックネームとみなし、種名は空欄(系統名のみ)", "low")
    rec["name_is_candy_species"] = (rec["species"] == fam) if rec["species"] and fam else None


SPEED = "おてつだいスピード"
SPEED_DOWN_FACTOR = 1.075   # せいかくで下がるときの倍率(実測。1.1 では揃わず、1.075 で最もよく揃った)
UNLOCK_LV = (None, 25, 50, 70, 80)   # サブスキル5枠の解放レベル(画面の鍵バッジの表示どおり)


def help_base(r):
    """おてつだい時間から、レベル・せいかく・サブスキルの影響を除いた「基準値」。同じ種類なら揃うはず。"""
    lv, sec = r.get("level"), r.get("help_interval_sec")
    if not lv or not sec:
        return None
    f = 1 - 0.002 * (lv - 1)
    if r.get("nature_up") == SPEED:
        f *= 0.9
    if r.get("nature_down") == SPEED:
        f *= SPEED_DOWN_FACTOR
    s = 0.0
    for i in range(1, 6):
        n = r.get(f"sub_skill{i}") or ""
        if r.get(f"sub_skill{i}_unlocked") and n.startswith(SPEED) and n.endswith("?"):
            return None   # S か M か分からないので、基準値は出せない
        if r.get(f"sub_skill{i}_unlocked") and n.startswith(SPEED):
            s += 0.14 if n.endswith("M") else 0.07
    return sec / (f * (1 - s))


def consistency_check(recs, reviews, min_n=4, major=0.9):
    """同じ種名のポケモンどうしで、決まっているはずの項目を突き合わせて、食い違いを要確認に出す。

    多数派が9割以上(かつ4件以上)なのに外れているものだけを出す(姿の違いなどで割れる種類は出さない)。
    """
    def flag(r, field, value, reason):
        reviews.append({"id": r["id"], "field": field, "value": value, "reason": reason, "severity": "mid"})

    by_species = defaultdict(list)
    for r in recs:
        if r.get("species"):
            by_species[r["species"]].append(r)
    fields = (
        ("specialty", "とくいなもの", lambda r: r.get("specialty")),
        ("type", "タイプ", lambda r: r.get("type")),
        ("main_skill", "メインスキル", lambda r: (r.get("main_skill") or "").replace(" ", "") or None),
        ("ingredient1_count", "食材1の個数", lambda r: r.get("ingredient1_count")),
    )
    for sp, group in by_species.items():
        for key, label, get in fields:
            vals = [(r, get(r)) for r in group if get(r) is not None]
            if len(vals) < min_n:
                continue
            top, cnt = Counter(v for _, v in vals).most_common(1)[0]
            if cnt / len(vals) < major:
                continue
            for r, v in vals:
                if v != top:
                    flag(r, key, str(v), f"同じ{sp}の{cnt}/{len(vals)}件は「{top}」なのに、これだけ「{v}」({label}の誤読か、姿の違い)")

    # おてつだい時間: 基準値が、同じ(種名, タイプ)の他の個体と1つも合わないものを出す
    by_form = defaultdict(list)
    for r in recs:
        if r.get("species") and help_base(r) is not None:
            # 姿(バケッチャのこだましゅ・ちゅうだましゅなど)で、おてつだい時間の基準が違うので、姿ごとに比べる
            by_form[(r["species"], r.get("type"), r.get("form_text"))].append(r)
    for (sp, _, _), group in by_form.items():
        if len(group) < min_n:
            continue
        bases = [help_base(r) for r in group]
        for r, b in zip(group, bases):
            if sum(1 for x in bases if abs(x - b) <= 6) == 1:   # 自分以外に近い値が無い
                flag(r, "help_interval_sec", f"{r['help_interval_sec']}秒(基準値{b:.0f})",
                     f"同じ{sp}の他の個体(基準値の中央値 {median(bases):.0f}秒)と、おてつだい時間の基準値が合わない(時間・レベル・せいかく・サブスキルのどれかの誤読の疑い)")

    # サブスキルの解放は、レベルで決まる(Lv25/50/70/80)
    for r in recs:
        lv = r.get("level")
        if not lv:
            continue
        for i in range(2, 6):
            got = r.get(f"sub_skill{i}_unlocked")
            if got is not None and got != (lv >= UNLOCK_LV[i - 1]):
                flag(r, f"sub_skill{i}_unlocked", f"Lv{lv}",
                     f"サブスキル{i}は Lv{UNLOCK_LV[i - 1]} で解放されるはずが、{'解放済み' if got else '未解放'}と判定(鍵バッジの誤判定かレベルの誤読)")


def apply_corrections(recs, reviews, path):
    """corrections.json(目で見て確認した値)を、機械の読み取りより優先して反映する。

    - 書かれた項目だけを上書きする。上書きした項目に関する要確認は、確認済みとして消す。
    - 「_note」は備考として残す。
    """
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in recs}
    done = 0
    for id_, fixes in data.items():
        if id_.startswith("_") or id_ not in by_id:
            continue
        rec = by_id[id_]
        touched = set()
        for k, v in fixes.items():
            if k == "_note":
                rec["note"] = v
                continue
            rec[k] = v
            touched.add(k)
        # 名前を手で決めたときの付随項目
        if "species" in fixes and rec.get("candy_species"):
            rec["name_is_candy_species"] = rec["species"] == rec["candy_species"]
        # 上書きした項目に関する要確認は、確認済みなので消す(関連する項目名もまとめて)
        related = set(touched)
        if touched & {"species", "nickname"}:
            related |= {"species", "candy_species"}
        if touched & {"nature", "nature_up", "nature_down", "nature_neutral"}:
            related |= {"nature"}
        if any(k.startswith("sub_skill") for k in touched):
            related |= {"sub_skills", "sub_skill1", "sub_skill2", "sub_skill3", "sub_skill4", "sub_skill5"}
        if touched & {"ingredient1_count", "ingredient2_count", "ingredient3_count"}:
            related |= {"ingredient_counts"}
        if touched & {"ingredient1", "ingredient2", "ingredient3"}:
            related |= {"ingredient1", "ingredient2", "ingredient3"}
        if "help_interval_sec" in touched:
            related |= {"help_interval"}
        reviews[:] = [r for r in reviews if not (r["id"] == id_ and r["field"] in related)]
        done += 1
    return done


def merge_icons(recs, reviews, path):
    """extract_icons.py の結果(icons.json)があれば、性別・お気に入り・タイプを結合する。"""
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data.get("icons", [])}
    for rec in recs:
        ic = by_id.get(rec["id"], {})
        rec["gender"] = ic.get("gender")
        rec["favorite"] = ic.get("favorite")
        rec["type"] = ic.get("type")
        for s in (1, 2, 3):
            rec[f"ingredient{s}"] = ic.get(f"ingredient{s}")
    for n in data.get("review", []):
        sev = "low" if "採用" in n["reason"] else "mid"
        reviews.append({"id": n["id"], "field": n["field"], "value": "", "reason": n["reason"], "severity": sev})


# タイプとあわせて決まるきのみ(ポケモンスリープ攻略・検証 Wiki の「きのみ」の一覧表より。
# 画面のきのみのアイコンの見た目と、タイプが、629匹の実データで100%対応することも確認した)
BERRY_OF_TYPE = {
    "ノーマル": "キーのみ", "ほのお": "ヒメリのみ", "みず": "オレンのみ", "でんき": "ウブのみ", "くさ": "ドリのみ",
    "こおり": "チーゴのみ", "かくとう": "クラボのみ", "どく": "カゴのみ", "じめん": "フィラのみ", "ひこう": "シーヤのみ",
    "エスパー": "マゴのみ", "むし": "ラムのみ", "いわ": "オボンのみ", "ゴースト": "ブリーのみ", "ドラゴン": "ヤチェのみ",
    "あく": "ウイのみ", "はがね": "ベリブのみ", "フェアリー": "モモンのみ",
}


def add_berry(recs):
    """タイプからきのみを決める(きのみはタイプと1対1で対応する)。"""
    for rec in recs:
        rec["berry"] = BERRY_OF_TYPE.get(rec.get("type"))


def extract_one(path, today, names=None):
    id_ = path.stem
    toks, syms = load_tokens(path)
    c = Ctx(id_, toks, syms, today)
    rec = {"id": id_}
    Y, H = anchors(c)
    if Y is None or H is None:
        c.flag("layout", "", "画面の基準位置(詳細ステータス等)が見つからない", "high")
        return rec, c.review
    for fn, args in ((ex_top, ()), (ex_middle, (H,)), (ex_skills, (H, Y)), (ex_detail, (Y,)), (ex_sleep, (Y,))):
        try:
            fn(c, rec, *args)
        except Exception as e:   # 1枚の失敗で全体を止めない
            c.flag(fn.__name__, "", f"処理中にエラー: {type(e).__name__}: {e}", "high")
    if names:
        resolve_species(rec, names, c)
    return rec, c.review


def write_outputs(recs, reviews, out_dir):
    # CSV は見出し・真偽値を日本語にする。JSON はプログラムから使うので、内部名(英語)のまま
    with open(out_dir / "pokemon.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([HEADERS_JA[k] for k in COLUMNS])
        for r in recs:
            w.writerow([ja_cell(r.get(k)) for k in COLUMNS])
    (out_dir / "pokemon.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    order = {"high": 0, "mid": 1, "low": 2}
    reviews = sorted(reviews, key=lambda r: (order[r["severity"]], r["field"], r["id"]))
    keys = ["severity", "id", "field", "value", "reason"]
    with open(out_dir / "review.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([REVIEW_HEADERS_JA[k] for k in keys])
        for r in reviews:
            w.writerow([SEVERITY_JA[r["severity"]], r["id"], FIELD_JA.get(r["field"], r["field"]), r["value"], r["reason"]])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ocr-dir", default=str(HERE / "ocr"), help="OCR 結果(JSON)のフォルダ")
    ap.add_argument("--out-dir", default=str(HERE), help="CSV/JSON の出力先")
    ap.add_argument("--ids", help="番号を指定(例: 1,2,10-12)。指定時はファイルに書かず、画面に表示する")
    ap.add_argument("--as-of", help="睡眠時間の妥当性チェックの基準日(YYYY-MM-DD)。既定は今日")
    a = ap.parse_args()

    today = datetime.strptime(a.as_of, "%Y-%m-%d").date() if a.as_of else date.today()
    files = sorted(Path(a.ocr_dir).glob("*.json"))
    if a.ids:
        want = set()
        for part in a.ids.split(","):
            lo, _, hi = part.partition("-")
            want.update(range(int(lo), int(hi or lo) + 1))
        files = [f for f in files if f.stem.isdigit() and int(f.stem) in want]
    if not files:
        sys.exit(f"OCR 結果がありません: {a.ocr_dir}")

    names = load_species_names()
    if names is None:
        print("注意: data\\pokemon_names.json が無いので、種名の確定は行いません(名前の欄をそのまま出します)")
    recs, reviews = [], []
    for f in files:
        rec, rev = extract_one(f, today, names)
        recs.append(rec)
        reviews += rev
    merge_icons(recs, reviews, Path(a.out_dir) / "icons.json")
    # 手修正を先に反映してから整合性チェックをする(確認済みの値を、他の個体の検証にも使えるようにする)
    n_fix = apply_corrections(recs, reviews, HERE / "corrections.json")
    add_berry(recs)   # タイプが手修正で決まった個体も含めて、あとから決める
    consistency_check(recs, reviews)
    # 手修正した個体は、整合性チェックの結果も確認済みなので、手で書いた項目に関するものは消す
    fixed_fields = {}
    if n_fix:
        data = json.loads((HERE / "corrections.json").read_text(encoding="utf-8"))
        fixed_fields = {k: {f for f in v if not f.startswith("_")} for k, v in data.items() if not k.startswith("_")}
        reviews[:] = [r for r in reviews if not (r["field"] in fixed_fields.get(r["id"], ()) and r["severity"] != "high")]
        if n_fix:
            print(f"手修正(corrections.json)を {n_fix} 件反映しました")

    if a.ids:
        for rec, f in zip(recs, files):
            print(json.dumps({k: rec.get(k) for k in COLUMNS if k in rec}, ensure_ascii=False))
        for r in reviews:
            print(f"  [{r['severity']}] {r['id']} {r['field']}: {r['reason']} ({r['value']!r})")
        return

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(recs, reviews, out_dir)
    sev = Counter(r["severity"] for r in reviews)
    print(f"{len(recs)} 件を処理しました -> {out_dir / 'pokemon.csv'}, pokemon.json")
    print(f"要確認: high {sev['high']} / mid {sev['mid']} / low {sev['low']} 件 -> {out_dir / 'review.csv'}")
    by_field = Counter((r["severity"], r["field"]) for r in reviews if r["severity"] != "low")
    for (s, fld), n in by_field.most_common(15):
        print(f"  [{s}] {fld}: {n} 件")


if __name__ == "__main__":
    main()
