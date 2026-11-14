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


# ===================== 角度別の評価 =====================
# 同じ個体を、複数の物差し(角度)で評価する。総合(目的に合わせたもの)のほかに、下の5つ。
# 角度ごとに「効くサブスキルの点数表」と「せいかくで上がると嬉しい(下がると困る)効果」を持つ。
# 点数の付け方(前半3枠=1、4・5枠目=0.5、せいかく±2)は総合と同じ。月の数は、その角度の理論上の最大点に対する割合で決める。
AXES = {
    # 角度: (点数表, せいかくの効果, ひとこと)
    "おてつだい": ({"おてつだいスピードM": 2, "おてつだいスピードS": 1, "おてつだいボーナス": 2},
                {SPEED}, "おてつだい時間の短さ(スピード系とボーナス)"),
    "食材": ({"食材確率アップM": 2, "食材確率アップS": 1, "最大所持数アップL": 1, "最大所持数アップM": 1},
           {SPEED, "食材おてつだい確率"}, "食材の集めやすさ"),
    "スキル": ({"スキル確率アップM": 2, "スキル確率アップS": 1, "スキルレベルアップM": 2, "スキルレベルアップS": 1},
            {SPEED, "メインスキル発生確率"}, "メインスキルの出やすさ・強さ"),
    "きのみ": ({"きのみの数S": 2, "最大所持数アップL": 1, "最大所持数アップM": 1},
            {SPEED}, "きのみの集めやすさ"),
    "サポート": ({"おてつだいボーナス": 2, "睡眠EXPボーナス": 1, "げんき回復ボーナス": 1,
              "ゆめのかけらボーナス": 1, "リサーチEXPボーナス": 1},
             set(), "チーム全体やプレイヤーに効く金スキル"),
}
AXIS_RATIO_STEPS = ((0.75, 5), (0.55, 4), (0.33, 3), (0.17, 2))   # (最大点に対する割合, 月の数)。総合の STAR_STEPS とほぼ同じ割合


def axis_points(rec, key):
    table, effects, _ = AXES[key]
    pts, seen = 0.0, set()
    for i in range(5):
        name = rec.get(f"sub_skill{i + 1}")
        v = table.get(name, 0)
        if v and name not in seen:
            seen.add(name)
            pts += v * SLOT_WEIGHT[i]
    if rec.get("nature_up") in effects:
        pts += NATURE_POINT
    if rec.get("nature_down") in effects:
        pts -= NATURE_POINT
    return pts


def axis_max(key):
    table, effects, _ = AXES[key]
    vals = sorted(table.values(), reverse=True)[:5]
    return sum(v * w for v, w in zip(vals, SLOT_WEIGHT)) + (NATURE_POINT if effects else 0)


def axis_moons(pts, key):
    ratio = pts / axis_max(key)
    for th, n in AXIS_RATIO_STEPS:
        if ratio >= th:
            return n
    return 1


def axis_all(rec):
    """戻り値: {角度: (点数, 月の数)}"""
    return {k: (axis_points(rec, k), axis_moons(axis_points(rec, k), k)) for k in AXES}


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

SPEED_SUB = {"おてつだいスピードS": 0.07, "おてつだいスピードM": 0.14}
SPEED_SUB_CAP = 0.35      # Wiki: サブスキルによるおてつだいスピードの合計は最大35%まで
LEVEL_STEP = 0.002        # レベルが1上がるごとに、おてつだい時間が0.2%短くなる(479匹の画面の値で確認)


def speed_bonus(rec, only_unlocked):
    s = 0.0
    for i in range(1, 6):
        if only_unlocked and not rec.get(f"sub_skill{i}_unlocked"):
            continue
        s += SPEED_SUB.get(rec.get(f"sub_skill{i}"), 0.0)
    return min(s, SPEED_SUB_CAP)


def help_time_after(rec, at_level=None):
    """計算後のおてつだい時間(秒)。画面の値は、今のレベル・せいかく・解放済みのサブスキルを反映済みなので、
    そこから、未解放のおてつだいスピードS/Mを反映する。at_level を指定すると、そのレベルでの時間にする。
    (画面の値と、公開データの基本の時間から計算した値は、479匹で ±0.05% 以内で一致)
    おてつだいボーナス(チーム全体に効く)は含めない。"""
    sec = rec.get("help_interval_sec")
    if not sec:
        return None
    t = sec * (1 - speed_bonus(rec, False)) / (1 - speed_bonus(rec, True))
    lv = rec.get("level")
    if at_level and lv:
        t *= (1 - (at_level - 1) * LEVEL_STEP) / (1 - (lv - 1) * LEVEL_STEP)
    return int(round(t))


CARRY_SUB = {"最大所持数アップS": 6, "最大所持数アップM": 12, "最大所持数アップL": 18}   # きのみ・食材の最大所持数が増える個数


def carry_after(rec):
    """計算後の最大所持数(個)。画面の値は、解放済みの「最大所持数アップ」までを反映済み(473匹で確認)なので、
    そこへ、未解放の「最大所持数アップ」S/M/L を足す。"""
    mc = rec.get("max_carry")
    if not mc:
        return None
    return mc + sum(CARRY_SUB.get(rec.get(f"sub_skill{i}"), 0) for i in range(1, 6) if not rec.get(f"sub_skill{i}_unlocked"))


def fmt_hms(sec):
    if sec is None:
        return ""
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return (f"{h}時間" if h else "") + (f"{m}分" if m or h else "") + f"{s}秒"


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
        "おてつだい時間(画面・秒)": r.get("help_interval_sec"),
        "おてつだい時間(計算後・秒)": help_time_after(r),
        "おてつだい時間(計算後)": fmt_hms(help_time_after(r)),
        "最大所持数(画面)": r.get("max_carry"),
        "最大所持数(計算後)": carry_after(r),
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


MIN_STAR = 4    # ★3以下は載せない(今のレベルは見ない)。総合か、その目的に関係する角度のどれかが★4以上なら載せる

# 目的ごとに、関係する角度(カードに出す・載せる条件に使う)
PURPOSE_AXES = {
    "dessert": ("食材", "おてつだい", "サポート"),
    "berry": ("きのみ", "おてつだい", "サポート"),
    "skill": ("スキル", "おてつだい", "サポート"),
    "healer": ("スキル", "おてつだい", "サポート"),
    "future": tuple(AXES),
}

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
        ax = axis_all(r)
        best = max([row["★"]] + [ax[k][1] for k in PURPOSE_AXES[key]])   # 総合か、関係する角度のどれかで一番高い月
        if best < min_star:
            continue
        row["最高の月"] = best
        for k, (p, m) in ax.items():
            row[f"点:{k}"], row[f"月:{k}"] = round(p, 1), m
        rows.append(row)
    rows.sort(key=sort_key, reverse=True)
    # 順位(同点は総合の並びの順)。総合と、角度ごと
    for i, row in enumerate(rows, 1):
        row["順位:総合"] = i
    for k in AXES:
        for rank, row in enumerate(sorted(rows, key=lambda x: -x[f"点:{k}"]), 1):   # sorted は安定なので、同点は総合の順
            row[f"順位:{k}"] = rank
    return rows[:top] if top else rows


def build_all(recs):
    """全個体を評価する(目的で絞らない)。HTML レポートの一覧用。

    各行に、総合の点・月(★)、5つの角度の点・月、どの目的に当てはまるか(「目的」)、
    全個体の中での順位(「順位:総合」「順位:角度名」)を付ける。総合は、その個体自身の「とくい」で評価する。
    """
    rows = []
    for r in recs:
        pts, good, bad = score(r)
        row = make_row(r, pts, good, bad)
        for k, (p, m) in axis_all(r).items():
            row[f"点:{k}"], row[f"月:{k}"] = round(p, 1), m
        row["目的"] = [k for k, v in PURPOSES.items() if v[1](r)]
        rows.append(row)
    ordered = sorted(rows, key=sort_key, reverse=True)
    for i, row in enumerate(ordered, 1):
        row["順位:総合"] = i
    for k in AXES:
        for rank, row in enumerate(sorted(ordered, key=lambda x: -x[f"点:{k}"]), 1):   # 同点は総合の順
            row[f"順位:{k}"] = rank
    return rows


def show(rows, n, key=None):
    for i, r in enumerate(rows[:n], 1):
        name = r["種名"] or f"{r['系統']}系({r['ニックネーム']})"
        angles = " ".join(f"{k}{r[f'月:{k}']}" for k in (PURPOSE_AXES[key] if key else AXES))
        print(f"{i:>2}. ★{r['★']} {r['点数']:>4}  {r['番号']} {name} Lv{r['Lv']} {r['タイプ']}/{r['とくい']} {r['せいかく']}"
              f" | 角度:{angles} | {r['メインスキル']} | {r['サブスキル']}"
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
        show(rows, a.top, key)
        print(f"  -> {path}")


if __name__ == "__main__":
    main()


