"""
ocr_vision.py
panoramas フォルダの画像を1枚ずつ Google Cloud Vision API で OCR して、
結果を画像1枚につき1ファイル(ocr/0001.json など)で保存する。

特徴:
    - 追加インストール不要(Python 標準ライブラリだけで REST API を呼ぶ)
    - 保存するのは API の応答そのまま(文字の位置情報つき)。
      あとから取りたい項目が増えても、OCR をやり直さずに済む
    - 保存済みの画像は飛ばすので、途中で止まっても同じコマンドで続きから再開できる

準備:
    Google Cloud で Cloud Vision API を有効にして、API キーを作成する。
    キーは環境変数に入れる(ファイルには書かない)。
        $env:GOOGLE_API_KEY = '...'

使い方:
    python ocr_vision.py --dry-run          # API を呼ばずに、処理対象だけ表示する
    python ocr_vision.py --limit 3          # 最初の3枚だけ試す
    python ocr_vision.py                    # panoramas の全画像を処理する
    python ocr_vision.py --force --limit 1  # 保存済みでも上書きして取り直す

料金の目安: Vision API の文字検出は、月1000枚まで無料、以降は1000枚あたり約1.5ドル。
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
HERE = Path(__file__).resolve().parent
FATAL_HTTP = (400, 401, 403)  # 設定ミス(キー・API の有効化・リクエスト不正)。続けても無駄なので中断する


class FatalError(RuntimeError):
    """再試行しても直らないエラー(認証・設定など)。"""


def call_vision(image_bytes, api_key, feature, langs, timeout, retries):
    """画像1枚を Vision API に送って、その応答(dict)を返す。"""
    body = {
        "requests": [{
            "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
            "features": [{"type": feature}],
            "imageContext": {"languageHints": langs},
        }]
    }
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "X-Goog-Api-Key": api_key},
    )
    payload = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = json.loads(res.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            if e.code in FATAL_HTTP:
                raise FatalError(f"HTTP {e.code}: {detail}") from None
            if attempt < retries:  # 429(回数制限)や 5xx は待って再試行
                wait = 2 ** attempt
                print(f"  HTTP {e.code}。{wait}秒待って再試行します [{attempt + 1}/{retries}]")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries:
                wait = 2 ** attempt
                print(f"  通信エラー({e})。{wait}秒待って再試行します [{attempt + 1}/{retries}]")
                time.sleep(wait)
                continue
            raise RuntimeError(f"通信エラー: {e}") from None

    res0 = (payload.get("responses") or [{}])[0]
    if "error" in res0:
        raise RuntimeError(f"Vision API エラー: {res0['error']}")
    return res0


def save_json(path, obj):
    """途中で止まっても壊れたファイルが残らないよう、一時ファイルに書いてから置き換える。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default=str(HERE / "panoramas"), help="OCR する画像のフォルダ")
    ap.add_argument("--out-dir", default=str(HERE / "ocr"), help="結果(JSON)の保存先フォルダ")
    ap.add_argument("--pattern", default="*.png", help="対象にするファイル名のパターン")
    ap.add_argument("--api-key", default=None,
                    help="API キー。省略で環境変数 GOOGLE_API_KEY(コマンド履歴に残るので環境変数を推奨)")
    ap.add_argument("--feature", choices=["DOCUMENT_TEXT_DETECTION", "TEXT_DETECTION"],
                    default="DOCUMENT_TEXT_DETECTION", help="OCR の種類。画面全体の文字には前者が向く")
    ap.add_argument("--lang", default="ja", help="言語のヒント(カンマ区切りで複数可。例: ja,en)")
    ap.add_argument("--limit", type=int, help="処理する枚数の上限(新しく処理する分だけ数える)")
    ap.add_argument("--force", action="store_true", help="保存済みでも取り直して上書きする")
    ap.add_argument("--dry-run", action="store_true", help="API を呼ばずに、処理対象だけ表示する")
    ap.add_argument("--sleep", type=float, default=0.1, help="1枚ごとの待ち時間(秒)")
    ap.add_argument("--timeout", type=float, default=60, help="1回の通信のタイムアウト(秒)")
    ap.add_argument("--retries", type=int, default=5, help="一時的なエラーのときの再試行回数")
    a = ap.parse_args()

    in_dir, out_dir = Path(a.in_dir), Path(a.out_dir)
    if not in_dir.is_dir():
        sys.exit(f"入力フォルダがありません: {in_dir}")
    files = sorted(in_dir.glob(a.pattern))
    if not files:
        sys.exit(f"対象の画像がありません: {in_dir} / {a.pattern}")

    pending = [f for f in files if a.force or not (out_dir / f"{f.stem}.json").exists()]
    todo = pending[:a.limit] if a.limit is not None else pending
    print(f"画像 {len(files)} 枚 / 保存済み(スキップ) {len(files) - len(pending)} 枚 / 今回の対象 {len(todo)} 枚")

    if a.dry_run:
        for f in todo[:10]:
            print(f"  {f.name} -> {out_dir / (f.stem + '.json')}")
        if len(todo) > 10:
            print(f"  ... ほか {len(todo) - 10} 枚")
        print("(--dry-run のため API は呼んでいません)")
        return
    if not todo:
        print("処理するものはありません。")
        return

    api_key = a.api_key or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("API キーがありません。環境変数 GOOGLE_API_KEY を設定してください。")

    langs = [s.strip() for s in a.lang.split(",") if s.strip()]
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    ok, failed = 0, []

    for i, f in enumerate(todo, 1):
        try:
            resp = call_vision(f.read_bytes(), api_key, a.feature, langs, a.timeout, a.retries)
        except FatalError as e:
            sys.exit(f"[{i}/{len(todo)}] {f.name}: {e}\n"
                     "認証・設定のエラーとみられるため中断します。API キーと Cloud Vision API の有効化を確認してください。")
        except RuntimeError as e:
            failed.append(f.name)
            print(f"[{i}/{len(todo)}] {f.name}: 失敗 - {e}")
            continue
        save_json(out_dir / f"{f.stem}.json", {
            "source": f.name,
            "feature": a.feature,
            "languageHints": langs,
            "response": resp,
        })
        ok += 1
        text = (resp.get("fullTextAnnotation") or {}).get("text", "")
        print(f"[{i}/{len(todo)}] {f.name}: 保存しました(文字数 {len(text)})")
        time.sleep(a.sleep)

    print(f"完了: 成功 {ok} 枚 / 失敗 {len(failed)} 枚 / {(time.time() - started) / 60:.1f}分")
    if failed:
        print("失敗: " + ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
