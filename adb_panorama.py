"""
adb_panorama.py
adb だけで「撮影 → 自動スワイプ → 撮影…」を繰り返し、縦長のパノラマ画像を作る。
--count を指定すると、横スワイプで次のポケモンに切り替えながら連続で撮影する。

準備:
    pip install opencv-python numpy
    スマホ側で USBデバッグ を有効にし、`adb devices` で認識されていること。

使い方(1匹だけ):
    スクロールしたい画面の一番上を表示した状態で
        python adb_panorama.py
    最後までスクロールし終わると自動で止まり、panorama.png を保存します。

使い方(連続撮影):
    1匹目の画面を表示した状態で
        python adb_panorama.py --count 637 --out-dir panoramas --skip-existing
    1匹撮り終えるごとに 横スワイプ → 一番上まで戻す → 撮影 を繰り返し、
    panoramas/0001.png, 0002.png ... と保存します。
    途中で止まっても、1匹目の画面に戻して同じコマンドを実行すれば、
    --skip-existing により撮影済みの分は飛ばして続きから撮影します。

オプション例:
    python adb_panorama.py --swipe 0.3 --duration 1200 --max-steps 80
    固定部分の自動検出がうまくいかない場合は --top / --bottom で指定(実機のpx)。
    adb が PATH に無い場合は --adb "D:\\android\\platform-tools\\adb.exe"
"""
import argparse
import os
import re
import subprocess
import time

import cv2
import numpy as np

ADB = "adb"


def adb(*args, binary=False):
    out = subprocess.run([ADB, *args], capture_output=True, check=True).stdout
    return out if binary else out.decode()


def capture():
    png = adb("exec-out", "screencap", "-p", binary=True)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("スクリーンショットの取得に失敗しました")
    return img


def screen_size():
    out = adb("shell", "wm", "size")
    sizes = re.findall(r"(\d+)x(\d+)", out)
    w, h = map(int, sizes[-1])  # Override size があればそちらを優先
    return w, h


def swipe(w, h, ratio, duration):
    """縦スワイプ。ratio>0 で下へスクロール(指は上へ)、ratio<0 で上へスクロール。"""
    x = w // 2
    y1 = int(h * (0.5 + ratio / 2))
    y2 = int(h * (0.5 - ratio / 2))
    adb("shell", "input", "swipe", str(x), str(y1), str(x), str(y2), str(duration))


def hswipe(w, h, direction, y_ratio, duration):
    """横スワイプ。direction='left' は指を右から左へ動かす(次のポケモンへ)。"""
    xa, xb = int(w * 0.85), int(w * 0.15)
    x1, x2 = (xa, xb) if direction == "left" else (xb, xa)
    y = int(h * y_ratio)
    adb("shell", "input", "swipe", str(x1), str(y), str(x2), str(y), str(duration))


def find_offset(prev, cur, thresh):
    """prev から cur へ内容が何px上に動いたか。見つからなければ None。"""
    pg = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    cg = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
    h = pg.shape[0]
    th = max(16, h // 8)
    for ratio in (0.7, 0.5, 0.3):
        y0 = int(h * ratio)
        tmpl = pg[y0:y0 + th]
        if tmpl.std() < 8:  # 特徴のない帯はスキップ
            continue
        res = cv2.matchTemplate(cg, tmpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score >= thresh:
            return y0 - loc[1]
    return None


def detect_fixed(a, b, tol=8):
    """2枚の画面で変化しなかった上端・下端の行数を返す(固定ヘッダー/フッター)。"""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=(1, 2))
    changed = np.where(diff > tol)[0]
    if len(changed) == 0:
        return None  # 全く動いていない
    H = a.shape[0]
    top = int(changed[0])
    bottom = int(H - 1 - changed[-1])
    limit = int(H * 0.4)  # 誤検出で本体が消えないよう上限を設ける
    return min(top, limit), min(bottom, limit)


def diff_ratio(a, b, tol=40):
    """2枚の画面で、色が大きく違うピクセルの割合(0〜1)。"""
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
    return float((d > tol).mean())


def scroll_to_top(w, h, a, max_tries=20):
    """画面が動かなくなるまで下方向へフリックして、一番上まで戻す。最後の画面を返す。"""
    prev = capture()
    y0, y1 = int(h * 0.4), int(h * 0.85)  # アニメーションするスプライトや固定バーを避けた領域
    for _ in range(max_tries):
        swipe(w, h, -0.6, a.top_duration)
        time.sleep(a.wait)
        cur = capture()
        if np.abs(prev[y0:y1].astype(np.int16) - cur[y0:y1].astype(np.int16)).mean() < 0.5:
            return cur
        prev = cur
    print("\n⚠ 一番上まで戻りきれなかった可能性があります")
    return prev


def shoot(w, h, a, first, fixed_tb):
    """first(一番上の画面)から下へスクロールしながら撮影してパノラマを返す。

    戻り値: (パノラマ画像, (top, bottom) または None)
    fixed_tb が渡されていれば固定部分の検出は行わずにその値を使う。
    """
    H = first.shape[0]
    swipe(w, h, a.swipe, a.duration)
    time.sleep(a.wait)
    second = capture()

    if fixed_tb is None:
        fixed = detect_fixed(first, second)
        if fixed is None:
            print("スクロールしませんでした。1枚だけ保存します。")
            return first, None
        # 固定部分が存在する側にだけ余白を足す(固定部分が無い側は 0 のまま)
        top = a.top if a.top is not None else (fixed[0] + a.margin if fixed[0] > 0 else 0)
        bottom = a.bottom if a.bottom is not None else (fixed[1] + a.margin if fixed[1] > 0 else 0)
        fixed_tb = (top, bottom)
        print(f"固定部分: 上 {top}px / 下 {bottom}px")
    top, bottom = fixed_tb

    body = lambda img: img[top:H - bottom]
    prev = body(first)
    strips = [prev]
    last = first
    cur_full = second
    stalled = 0

    for step in range(1, a.max_steps + 1):
        cur = body(cur_full)
        dy = find_offset(prev, cur, a.thresh)
        if dy is None:
            print(f"\n⚠ {step}回目: 一致が見つからず停止(--swipe を小さくすると改善するかも)")
            break
        if dy <= 2:
            stalled += 1
            if stalled >= 2:
                print("\n最後まで到達しました")
                break
        else:
            stalled = 0
            strips.append(cur[-dy:])
            prev, last = cur, cur_full
            print(f"\r{step}回目: +{dy}px", end="")
        swipe(w, h, a.swipe, a.duration)
        time.sleep(a.wait)
        cur_full = capture()

    pano = np.vstack([first[:top], *strips, last[H - bottom:]])
    return pano, fixed_tb


def main():
    global ADB
    ap = argparse.ArgumentParser()
    ap.add_argument("--swipe", type=float, default=0.4, help="1回のスワイプ距離(画面高さ比)")
    ap.add_argument("--duration", type=int, default=1000, help="スワイプ時間ms(長いほど慣性が出にくい)")
    ap.add_argument("--wait", type=float, default=0.6, help="スワイプ後の待ち時間(秒)")
    ap.add_argument("--max-steps", type=int, default=50)
    ap.add_argument("--top", type=int, help="上部固定部分の高さ(px)。省略で自動検出")
    ap.add_argument("--bottom", type=int, help="下部固定部分の高さ(px)。省略で自動検出")
    ap.add_argument("--margin", type=int, default=12,
                    help="自動検出した固定部分に足す余白(px)。境界の影や1px線を継ぎ目に出さないため")
    ap.add_argument("--thresh", type=float, default=0.9)
    ap.add_argument("-o", "--out", default="panorama.png", help="--count 1 のときの保存先")
    # 連続撮影
    ap.add_argument("--count", type=int, default=1, help="撮影するポケモンの数(2以上で連続撮影)")
    ap.add_argument("--out-dir", default="panoramas", help="連続撮影の保存先フォルダ")
    ap.add_argument("--skip-existing", action="store_true", help="保存先に既にあるファイルは撮影せず飛ばす")
    ap.add_argument("--direction", choices=["left", "right"], default="left",
                    help="次のポケモンへ切り替える横スワイプの向き(left=指を右から左へ)")
    ap.add_argument("--hswipe-y", type=float, default=0.5, help="横スワイプを行う高さ(画面高さ比)")
    ap.add_argument("--hswipe-duration", type=int, default=200, help="横スワイプ時間ms")
    ap.add_argument("--switch-wait", type=float, default=1.2, help="切り替え後の待ち時間(秒)")
    ap.add_argument("--top-duration", type=int, default=200, help="一番上へ戻すフリックの時間ms")
    ap.add_argument("--same-thresh", type=float, default=0.003,
                    help="前のポケモンとの変化率がこれ未満なら切り替わっていないとみなす")
    ap.add_argument("--adb", default="adb", help="adb の実行ファイルのパス")
    a = ap.parse_args()
    ADB = a.adb

    w, h = screen_size()
    print(f"画面サイズ: {w}x{h}")
    if a.count > 1:
        os.makedirs(a.out_dir, exist_ok=True)

    fixed_tb = None   # 固定部分は最初に検出した値を使い回す
    prev_first = None
    started = time.time()
    done = 0

    for i in range(1, a.count + 1):
        path = a.out if a.count == 1 else os.path.join(a.out_dir, f"{i:04d}.png")

        if i > 1:
            hswipe(w, h, a.direction, a.hswipe_y, a.hswipe_duration)
            time.sleep(a.switch_wait)

        if a.skip_existing and os.path.exists(path):
            print(f"[{i}/{a.count}] スキップ(撮影済み): {path}")
            prev_first = None
            continue

        first = scroll_to_top(w, h, a) if a.count > 1 else capture()

        if prev_first is not None:
            ratio = diff_ratio(prev_first, first)
            if ratio < a.same_thresh:
                print(f"切り替わっていません(変化率 {ratio:.4f})。もう一度スワイプします")
                hswipe(w, h, a.direction, a.hswipe_y, a.hswipe_duration)
                time.sleep(a.switch_wait)
                first = scroll_to_top(w, h, a)
                ratio = diff_ratio(prev_first, first)
                if ratio < a.same_thresh:
                    print(f"次のポケモンがありません(変化率 {ratio:.4f})。{i - 1}匹で終了します")
                    break

        print(f"[{i}/{a.count}] 撮影中...")
        pano, tb = shoot(w, h, a, first, fixed_tb)
        if tb is not None:
            fixed_tb = tb
        cv2.imwrite(path, pano)
        print(f"保存しました: {path}  ({pano.shape[1]}x{pano.shape[0]})")
        prev_first = first
        done += 1

        if a.count > 1:
            elapsed = time.time() - started
            remain = (a.count - i) * elapsed / done
            print(f"  経過 {elapsed / 60:.1f}分 / 残り目安 {remain / 60:.1f}分")


if __name__ == "__main__":
    main()
