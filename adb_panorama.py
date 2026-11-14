"""
adb_panorama.py
adb だけで「撮影 → 自動スワイプ → 撮影…」を繰り返し、縦長のパノラマ画像を作る。

準備:
    pip install opencv-python numpy
    スマホ側で USBデバッグ を有効にし、`adb devices` で認識されていること。

使い方:
    スクロールしたい画面の一番上を表示した状態で
        python adb_panorama.py
    最後までスクロールし終わると自動で止まり、panorama.png を保存します。

オプション例:
    python adb_panorama.py --swipe 0.3 --duration 1200 --max-steps 80
    固定部分の自動検出がうまくいかない場合は --top / --bottom で指定(実機のpx)。
"""
import argparse
import re
import subprocess
import time

import cv2
import numpy as np


def adb(*args, binary=False):
    out = subprocess.run(["adb", *args], capture_output=True, check=True).stdout
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
    x = w // 2
    y1 = int(h * (0.5 + ratio / 2))
    y2 = int(h * (0.5 - ratio / 2))
    adb("shell", "input", "swipe", str(x), str(y1), str(x), str(y2), str(duration))


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


def main():
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
    ap.add_argument("-o", "--out", default="panorama.png")
    a = ap.parse_args()

    w, h = screen_size()
    print(f"画面サイズ: {w}x{h}")

    first = capture()
    H = first.shape[0]
    swipe(w, h, a.swipe, a.duration)
    time.sleep(a.wait)
    second = capture()

    fixed = detect_fixed(first, second)
    if fixed is None:
        print("スクロールしませんでした。1枚だけ保存します。")
        cv2.imwrite(a.out, first)
        return
    # 固定部分が存在する側にだけ余白を足す(固定部分が無い側は 0 のまま)
    top = a.top if a.top is not None else (fixed[0] + a.margin if fixed[0] > 0 else 0)
    bottom = a.bottom if a.bottom is not None else (fixed[1] + a.margin if fixed[1] > 0 else 0)
    print(f"固定部分: 上 {top}px / 下 {bottom}px")

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
    cv2.imwrite(a.out, pano)
    print(f"保存しました: {a.out}  ({pano.shape[1]}x{pano.shape[0]})")


if __name__ == "__main__":
    main()
