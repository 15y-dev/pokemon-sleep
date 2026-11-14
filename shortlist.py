"""
shortlist.py
手持ちのポケモン(pokemon.json)から、目的別に「育てるとよい個体」を選び、
サブスキルとせいかくの良し悪しをランク(★)にして並べる。通信はしない。

目的(--purpose):
    dessert  デザート・ドリンク用の食材集め(食材とくいで、新デザートの食材を持つ)
    berry    好物のきのみ集め(きのみとくいで、アンバー渓谷の好物 = カゴ・ラム・ヤチェ)
    skill    料理スキル(料理パワーアップS・料理チャンスS・料理アシストS)
    healer   ヒーラー(げんきオールS・げんきエールS)
    future   アンバー渓谷で育てる個体(好物のきのみ = どく・むし・ドラゴン。とくいは問わない)

今のレベルは、選ぶ条件にも点数にも使わない(未解放のサブスキル・食材の枠も、解放済みと同じ扱い)。
★3以下は載せない(★4以上だけ。--min-star で変えられる)。

出力(shortlists フォルダ。Excel で開ける UTF-8 BOM つき):
    dessert.csv / berry.csv / skill.csv / healer.csv / future.csv

使い方:
    python shortlist.py                       # すべての目的
    python shortlist.py --purpose dessert     # 1つだけ
    python shortlist.py --purpose berry,skill --top 15
    python shortlist.py --min-star 3          # ★3も載せる

ランクの付け方は、下の定数にまとめてある(Wiki の記述を根拠にした目安。好みに合わせて変えてよい)。
"""
import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

DEFAULT_BERRIES = ("カゴのみ", "ラムのみ", "ヤチェのみ")

# Wiki「チーム編成/アンバー渓谷」(2026-11-12 更新)の、デザート・ドリンクの候補に出てくるポケモン。
# https://wikiwiki.jp/poke_sleep/チーム編成/アンバー渓谷
WIKI_DESSERT_AMBER = ("カイロス", "カメックス", "カイリュー", "クワガノン", "ハピナス", "バンギラス",
                      "モロバレル", "ウェーニバル", "ドオー", "パーモット")

# Game8(2026-11-11 時点)によると、11/16 に追加される新料理3(デザート・ドリンク)の食材。
# (ほかに「ひとくちナッツ」があるが、まだ手持ちの画面には出てこない)
NEW_DESSERT_INGREDIENTS = ("とくせんエッグ", "おいしいシッポ", "ワカクサ大豆")

# ---- ランクの基準 ----
SPEED = "おてつだいスピード"
# サブスキルの点数: 2=強い 1=役に立つ 0=この用途ではほぼ無関係(書いていないものは0)
SUB_COMMON = {   # どの特技でも
    "おてつだいボーナス": 2,       # チーム全員のおてつだい時間が短くなる(金スキル。Wiki で強力と明記)
    "おてつだいスピードM": 2, "おてつだいスピードS": 1,
    "最大所持数アップL": 1, "最大所持数アップM": 1,
}
SUB_BERRY = {"きのみの数S": 2, "睡眠EXPボーナス": 1}      # きのみとくい(Wiki: きのみの数S・おてつだいボーナスが強力。睡眠EXPも嬉しい)
SUB_ING = {"食材確率アップM": 2, "食材確率アップS": 1}    # 食材とくい
SUB_SKILL = {"スキル確率アップM": 2, "スキル確率アップS": 1, "スキルレベルアップM": 2, "スキルレベルアップS": 1}   # スキルとくい
SLOT_WEIGHT = (1, 1, 1, 0.5, 0.5)          # Wiki: 前半の3枠が主要な厳選要素。4・5枠目は半分に数える
UNLOCK_LV = (None, 25, 50, 70, 80)         # サブスキルの解放レベル(1枠目は最初から)
NATURE_POINT = 2                            # せいかくの「上がる効果」が有効なら +2、「下がる効果」が有効な項目なら -2
STAR_STEPS = ((7, 5), (5, 4), (3, 3), (1.5, 2))   # (この点数以上, ★の数)。どれにも当てはまらなければ ★1

def wanted_effects(specialty):
    """特技ごとの、せいかくの「上がると嬉しい効果」と「下がると困る効果」。

    おてつだいスピード … どの特技でも上がると嬉しい(おてつだい回数が増える)
    食材おてつだい確率 … 食材とくい  / メインスキル発生確率 … スキルとくい  / EXP獲得量・げんき回復量 … 小さな効果
    """
    good = {SPEED}
    if specialty == "食材":
        good.add("食材おてつだい確率")
    elif specialty == "スキル":
        good.add("メインスキル発生確率")
    elif specialty == "オール":
        good |= {"食材おてつだい確率", "メインスキル発生確率"}
    bad = {SPEED}
    if specialty == "食材":
        bad.add("食材おてつだい確率")
    elif specialty == "スキル":
        bad.add("メインスキル発生確率")
    elif specialty == "オール":
        bad |= {"食材おてつだい確率", "メインスキル発生確率"}
    return good, bad


def sub_table(specialty):
    t = dict(SUB_COMMON)
    t.update({"きのみ": SUB_BERRY, "食材": SUB_ING, "スキル": SUB_SKILL}.get(specialty, {}))
    if specialty == "オール":
        t.update(SUB_ING)
        t.update(SUB_SKILL)
    return t


def score(rec, as_specialty=None):
    """戻り値: (点数, 良い点のメモ, 気になる点のメモ)。今のレベルは見ない(未解放の枠も同じ重さで数える)。

    as_specialty: 評価に使う「とくい」。None なら、その個体の本来の「とくい」を使う。
                  (ヒーラーは、とくいが何であれ、スキル発動が大事なので「スキル」で評価する)
    """
    sp = as_specialty or rec.get("specialty")
    table = sub_table(sp)
    pts, good_notes, bad_notes = 0.0, [], []
    seen = set()
    for i in range(5):
        name = rec.get(f"sub_skill{i + 1}")
        if not name:
            continue
        v = table.get(name, 0)
        if v == 0 or name in seen:
            continue
        seen.add(name)
        # 今のレベルは見ない。未解放の枠も、解放済みと同じ重さで数える(育てれば必ず解放されるため)
        pts += v * SLOT_WEIGHT[i]
        good_notes.append(name)
    good, bad = wanted_effects(sp)
    up, down = rec.get("nature_up"), rec.get("nature_down")
    if up in good:
        pts += NATURE_POINT
        good_notes.append(f"せいかく:{up}↑")
    if down in bad:
        pts -= NATURE_POINT
        bad_notes.append(f"せいかく:{down}↓")
    return pts, good_notes, bad_notes


def stars(points):
    for th, n in STAR_STEPS:
        if points >= th:
            return n
    return 1


# 「アメの系統名(進化前)」から、Wiki の候補に進化できるもの。ニックネームで種名が分からない個体や、
# 進化前の個体も、育てれば候補になるので拾う。
# (アメの系統名 -> 進化後の候補)。好物のきのみで絞った後に使うので、タイプが合わないもの(カメックスなど)は出てこない。
LINE_TO_WIKI = {
    "ミニリュウ": "カイリュー", "アゴジムシ": "クワガノン", "ヨーギラス": "バンギラス", "タマゲタケ": "モロバレル",
    "ウパー": "ドオー", "カイロス": "カイロス",
}


def wiki_match(rec):
    hit = []
    for n in (rec.get("species"), rec.get("candy_species")):
        if n in WIKI_DESSERT_AMBER and n not in hit:
            hit.append(n)
        w = LINE_TO_WIKI.get(n)
        if w in WIKI_DESSERT_AMBER and w not in hit:
            hit.append(w)
    return hit


def dessert_ingredients(rec):
    """画面に出ている食材(食材1〜3)のうち、新デザート・ドリンクで使うもの。今のレベルは見ない。

    注意: Lv30未満は食材2、Lv60未満は食材3が画面に出ないので、読み取れていない(判定できない)。
    """
    return [rec[f"ingredient{s}"] for s in (1, 2, 3) if rec.get(f"ingredient{s}") in NEW_DESSERT_INGREDIENTS]

def make_row(r, pts, good, bad):
    lv = r.get("level") or 0
    return {
        "★": stars(pts), "点数": round(pts, 1), "番号": r["id"],
        "種名": r.get("species") or "", "系統": r.get("candy_species") or "", "ニックネーム": r.get("nickname") or "",
        "Lv": r.get("level"), "タイプ": r.get("type"), "きのみ": r.get("berry"), "とくい": r.get("specialty") or "",
        "メインスキル": r.get("main_skill") or "", "せいかく": r.get("nature") or "",
        "サブスキル": " / ".join(
            (r.get(f"sub_skill{i + 1}") or "-") + ("" if r.get(f"sub_skill{i + 1}_unlocked") else "(未)")
            for i in range(5)),
        "おてつだい時間(秒)": r.get("help_interval_sec"),
        "食材": " / ".join(filter(None, [r.get("ingredient1"), r.get("ingredient2"), r.get("ingredient3")])),
        "新デザートの食材": ",".join(dessert_ingredients(r)), "Wiki候補": ",".join(wiki_match(r)),
        "良い点": ", ".join(good), "気になる点": ", ".join(bad),
    }


# ===================== 目的別の選出 =====================
# 目的ごとに「どの個体を候補にするか(select)」と「どのサブスキル・せいかくを良しとするか」が違う。
FAV_BERRIES = DEFAULT_BERRIES    # アンバー渓谷の好物
HEAL_SKILLS = ("げんきオール", "げんきエール")   # チーム全員を回復するもの(げんきチャージSは自分だけなので含めない)
COOK_SKILLS = ("料理パワーアップ", "料理チャンス", "料理アシスト")


def has_skill(r, names):
    return any(n in (r.get("main_skill") or "") for n in names)


def has_dessert_ing(r):
    return bool(dessert_ingredients(r))


def sel_dessert(r):
    """デザート・ドリンク用の食材集め: 食材(またはオール)とくい で、新デザートの食材を解放済みで持つ"""
    return r.get("specialty") in ("食材", "オール") and has_dessert_ing(r)


def sel_berry(r):
    """好物のきのみ集め: きのみとくい で、アンバー渓谷の好物(どく・むし・ドラゴン)"""
    return r.get("specialty") == "きのみ" and r.get("berry") in FAV_BERRIES


def sel_skill(r):
    """料理スキル: スキルとくい で、料理パワーアップS・料理チャンスS・料理アシストS を持つ"""
    return r.get("specialty") == "スキル" and has_skill(r, COOK_SKILLS)


def sel_healer(r):
    """ヒーラー: げんきオールS・げんきエールS(チーム全員を回復する)"""
    return has_skill(r, HEAL_SKILLS)


def sel_future(r):
    """アンバー渓谷の好物のきのみ(どく・むし・ドラゴン)で、とくいを問わず、サブスキルとせいかくが良い個体。"""
    return r.get("berry") in FAV_BERRIES


MIN_STAR = 4    # ★3以下は載せない(今のレベルは見ない)

PURPOSES = {
    # 目的: (名前, 選ぶ条件, 評価に使う「とくい」(None なら個体の本来の「とくい」を使う))
    "dessert": ("デザート・ドリンク用の食材集め", sel_dessert, "食材"),
    "berry": ("好物のきのみ集め(アンバー渓谷)", sel_berry, "きのみ"),
    "skill": ("料理スキル(料理パワーアップ・チャンス・アシスト)", sel_skill, "スキル"),
    "healer": ("ヒーラー(げんきオール・エール)", sel_healer, "スキル"),
    "future": ("アンバー渓谷で育てる個体(好物のきのみ・とくい不問)", sel_future, None),
}


def sort_key(x):
    # 点数が同じなら、新デザートの食材・Wiki候補を先に。今のレベルは並びに使わない(番号の順にする)
    return (x["点数"], bool(x["新デザートの食材"]), bool(x["Wiki候補"]), [-ord(c) for c in x["番号"]])


def build_purpose(recs, key, top=None, min_star=MIN_STAR):
    """目的 key の個体を選んで、点数順に並べて返す。今のレベルは見ない。★が min_star 未満のものは載せない。"""
    title, sel, as_sp = PURPOSES[key]
    rows = []
    for r in recs:
        if not sel(r):
            continue
        pts, good, bad = score(r, as_sp)
        row = make_row(r, pts, good, bad)
        if row["★"] >= min_star:
            rows.append(row)
    rows.sort(key=sort_key, reverse=True)
    return rows[:top] if top else rows


def show(rows, n):
    for i, r in enumerate(rows[:n], 1):
        name = r["種名"] or f"{r['系統']}系({r['ニックネーム']})"
        print(f"{i:>2}. ★{r['★']} {r['点数']:>4}  {r['番号']} {name} Lv{r['Lv']} {r['タイプ']}/{r['とくい']} {r['せいかく']}"
              f" | {r['メインスキル']} | {r['サブスキル']}"
              + (f" | 食材:{r['新デザートの食材']}" if r["新デザートの食材"] else "")
              + (f" | Wiki:{r['Wiki候補']}" if r["Wiki候補"] else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=str(HERE / "pokemon.json"))
    ap.add_argument("--out-dir", default=str(HERE / "shortlists"), help="目的別の CSV の出力先")
    ap.add_argument("--purpose", default="all",
                    help="目的(カンマ区切り): " + " / ".join(PURPOSES) + " / all(すべて)")
    ap.add_argument("--top", type=int, default=10, help="画面に出す件数(目的ごと)")
    ap.add_argument("--min-star", type=int, default=MIN_STAR, help=f"載せる★の下限(既定 {MIN_STAR})")
    a = ap.parse_args()

    recs = json.loads(Path(a.json).read_text(encoding="utf-8"))
    keys = list(PURPOSES) if a.purpose == "all" else [k.strip() for k in a.purpose.split(",") if k.strip()]
    bad = [k for k in keys if k not in PURPOSES]
    if bad:
        sys.exit(f"知らない目的です: {bad}(使えるもの: {', '.join(PURPOSES)})")

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for key in keys:
        title = PURPOSES[key][0]
        rows = build_purpose(recs, key, min_star=a.min_star)
        print(f"\n=== {title}  ({len(rows)}匹) ===")
        if not rows:
            print("  条件に合う個体がありません")
            continue
        path = out_dir / f"{key}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        show(rows, a.top)
        print(f"  -> {path}")


if __name__ == "__main__":
    main()


