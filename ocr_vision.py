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
    Google Cloud で Cloud Vision API を有効にして、サービスアカウントのキー(JSON)を
    このスクリプトと同じフォルダに置く。"type": "service_account" の JSON を自動で探して使う。
    (別の場所に置く場合は --key-file。API キーを使う場合は環境変数 GOOGLE_API_KEY)
    キーの JSON は秘密情報なので、git に入れたり他人に渡したりしないこと。

使い方:
    python ocr_vision.py --dry-run          # API を呼ばずに、処理対象だけ表示する
    python ocr_vision.py --limit 3          # 最初の3枚だけ試す
    python ocr_vision.py                    # panoramas の全画像を処理する
    python ocr_vision.py --force --limit 1  # 保存済みでも上書きして取り直す

料金の目安: Vision API の文字検出は、月1000枚まで無料、以降は1000枚あたり約1.5ドル。
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"
HERE = Path(__file__).resolve().parent
FATAL_HTTP = (400, 401, 403)  # 設定ミス(キー・API の有効化・リクエスト不正)。続けても無駄なので中断する


class FatalError(RuntimeError):
    """再試行しても直らないエラー(認証・設定など)。"""


# ---- サービスアカウント認証(標準ライブラリだけで JWT に RS256 署名する) ----
SCOPE = "https://www.googleapis.com/auth/cloud-vision"
# SHA-256 の DigestInfo(PKCS#1 v1.5 署名の固定プレフィックス)
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _der(data, pos):
    """DER の1要素を読む。戻り値: (タグ, 中身の開始, 中身の終了)"""
    tag, first = data[pos], data[pos + 1]
    if first & 0x80:
        n = first & 0x7F
        length, start = int.from_bytes(data[pos + 2:pos + 2 + n], "big"), pos + 2 + n
    else:
        length, start = first, pos + 2
    return tag, start, start + length


def _children(data, start, end):
    pos = start
    while pos < end:
        tag, s, e = _der(data, pos)
        yield tag, s, e
        pos = e


def load_rsa_private_key(pem):
    """PEM(PKCS#8 / PKCS#1)の RSA 秘密鍵から (n, d) を取り出す。"""
    lines = [l.strip() for l in pem.strip().splitlines() if l.strip() and not l.startswith("-----")]
    der = base64.b64decode("".join(lines))
    _, s, e = _der(der, 0)  # 最上位の SEQUENCE
    kids = list(_children(der, s, e))
    if "BEGIN PRIVATE KEY" in pem:  # PKCS#8: [version, algorithm, OCTET STRING(PKCS#1)]
        _, s, e = kids[2]
        _, s, e = _der(der, s)
        kids = list(_children(der, s, e))
    # PKCS#1: [version, n, e, d, ...]
    n = int.from_bytes(der[kids[1][1]:kids[1][2]], "big")
    d = int.from_bytes(der[kids[3][1]:kids[3][2]], "big")
    return n, d


def rs256_sign(message, n, d):
    """RSASSA-PKCS1-v1_5 + SHA-256 で署名する。"""
    k = (n.bit_length() + 7) // 8
    t = SHA256_PREFIX + hashlib.sha256(message).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class ApiKeyAuth:
    def __init__(self, key):
        self.key = key
        self.label = "API キー"

    def headers(self):
        return {"X-Goog-Api-Key": self.key}

    def invalidate(self):
        pass


class ServiceAccountAuth:
    """サービスアカウントの JSON キーから、アクセストークンを取得して使い回す(有効期間は約1時間)。"""

    def __init__(self, info, timeout=30):
        self.info = info
        self.timeout = timeout
        self.n, self.d = load_rsa_private_key(info["private_key"])
        self.token = None
        self.expires = 0.0
        self.label = f"サービスアカウント {info.get('client_email')}"

    def invalidate(self):
        self.token = None

    def headers(self):
        if self.token is None or time.time() > self.expires - 300:  # 期限の5分前に更新する
            self._refresh()
        return {"Authorization": f"Bearer {self.token}"}

    def _refresh(self):
        now = int(time.time())
        token_uri = self.info.get("token_uri", "https://oauth2.googleapis.com/token")
        header = {"alg": "RS256", "typ": "JWT"}
        if self.info.get("private_key_id"):
            header["kid"] = self.info["private_key_id"]
        claims = {"iss": self.info["client_email"], "scope": SCOPE, "aud": token_uri,
                  "iat": now, "exp": now + 3600}
        signing_input = (_b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(claims).encode()))
        jwt = signing_input + "." + _b64url(rs256_sign(signing_input.encode("ascii"), self.n, self.d))
        form = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt,
        }).encode("ascii")
        req = urllib.request.Request(token_uri, data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                body = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            err = FatalError if e.code in FATAL_HTTP else RuntimeError
            raise err(f"トークン取得に失敗 HTTP {e.code}: {detail}") from None
        self.token = body["access_token"]
        self.expires = time.time() + int(body.get("expires_in", 3600))


def find_key_file(explicit=None):
    """サービスアカウントの JSON キーを探す。指定が無ければ、このスクリプトと同じフォルダから探す。"""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            sys.exit(f"キーファイルがありません: {p}")
        return p
    for p in sorted(HERE.glob("*.json")):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("type") == "service_account":
                return p
        except (OSError, ValueError, AttributeError):
            continue
    return None


def make_auth(api_key_arg, key_file_arg):
    """使う認証方式を決める。優先順位: --key-file > --api-key > 同じフォルダのキーJSON > 環境変数 GOOGLE_API_KEY"""
    if not key_file_arg and api_key_arg:
        return ApiKeyAuth(api_key_arg)
    path = find_key_file(key_file_arg)
    if path:
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            auth = ServiceAccountAuth(info)
        except (OSError, ValueError, KeyError, IndexError) as e:
            sys.exit(f"キーファイルを読み込めません: {path.name} ({type(e).__name__})")
        auth.source = path.name
        return auth
    if os.environ.get("GOOGLE_API_KEY"):
        return ApiKeyAuth(os.environ["GOOGLE_API_KEY"])
    sys.exit("認証情報がありません。サービスアカウントの JSON をこのフォルダに置くか、"
             "--key-file / --api-key / 環境変数 GOOGLE_API_KEY を指定してください。")


def call_vision(image_bytes, auth, feature, langs, timeout, retries):
    """画像1枚を Vision API に送って、その応答(dict)を返す。auth は ApiKeyAuth / ServiceAccountAuth。"""
    data = json.dumps({
        "requests": [{
            "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
            "features": [{"type": feature}],
            "imageContext": {"languageHints": langs},
        }]
    }).encode("utf-8")
    payload = None
    token_retried = False
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            ENDPOINT, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8", **auth.headers()},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = json.loads(res.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            if e.code == 401 and not token_retried:  # トークンの期限切れかもしれないので、1回だけ取り直す
                token_retried = True
                auth.invalidate()
                continue
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
    ap.add_argument("--key-file", default=None,
                    help="サービスアカウントの JSON キー。省略でこのスクリプトと同じフォルダから自動で探す")
    ap.add_argument("--api-key", default=None,
                    help="API キー(キー JSON が無いとき用)。省略で環境変数 GOOGLE_API_KEY。履歴に残るので環境変数を推奨")
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
        auth = make_auth(a.api_key, a.key_file)  # 認証情報が読めるかだけ確認する(通信はしない)
        print(f"認証: {auth.label}" + (f"(キー: {auth.source})" if hasattr(auth, "source") else ""))
        print("(--dry-run のため API は呼んでいません)")
        return
    if not todo:
        print("処理するものはありません。")
        return

    auth = make_auth(a.api_key, a.key_file)
    print(f"認証: {auth.label}" + (f"(キー: {auth.source})" if hasattr(auth, "source") else ""))

    langs = [s.strip() for s in a.lang.split(",") if s.strip()]
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    ok, failed = 0, []

    for i, f in enumerate(todo, 1):
        try:
            resp = call_vision(f.read_bytes(), auth, a.feature, langs, a.timeout, a.retries)
        except FatalError as e:
            sys.exit(f"[{i}/{len(todo)}] {f.name}: {e}\n"
                     "認証・設定のエラーとみられるため中断します。キーの権限と Cloud Vision API の有効化"
                     "(サービスアカウントのプロジェクトで有効か)を確認してください。")
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
