"""Quick live-site QA for scmaglev.onrender.com"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://scmaglev.onrender.com"
TIMEOUT = 60
MAX_ATTEMPTS = 8


def get(path):
    req = urllib.request.Request(f"{BASE}{path}", headers={"User-Agent": "scmaglev-qa/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def get_with_retry(path):
    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return get(path)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (502, 503) or attempt >= MAX_ATTEMPTS - 1:
                raise
        except Exception as exc:
            last_error = exc
            if attempt >= MAX_ATTEMPTS - 1:
                raise
        time.sleep(15 * (attempt + 1))
    raise last_error


def check_page(path):
    status, body, headers = get_with_retry(path)
    ct = headers.get("Content-Type", "")
    title_m = re.search(rb"<title>(.*?)</title>", body, re.I)
    title = title_m.group(1).decode("utf-8") if title_m else "(no title)"
    has_tokens_css = b"tokens.css" in body
    has_components_css = b"components.css" in body
    charset_ok = b'charset="utf-8"' in body.lower() or b"charset=utf-8" in body.lower()
    korean_ok = "관제" in title or "SCMAGLEV" in title
    mojibake = any(ch in title for ch in ("ì", "ê", "Ã"))
    return {
        "path": path,
        "status": status,
        "content_type": ct,
        "title": title,
        "charset_ok": charset_ok,
        "korean_title_ok": korean_ok,
        "mojibake": mojibake,
        "has_tokens_css": has_tokens_css,
        "has_components_css": has_components_css,
        "bytes": len(body),
    }


def check_api(path):
    status, body, _ = get_with_retry(path)
    data = json.loads(body.decode("utf-8"))
    return {"path": path, "status": status, "sample": str(data)[:180]}


def main():
    results = {"pages": [], "apis": [], "static": [], "errors": []}

    for path in ("/", "/dashboard"):
        try:
            results["pages"].append(check_page(path))
        except Exception as exc:
            results["errors"].append({"path": path, "error": str(exc)})

    for path in (
        "/api/health",
        "/api/scmaglev/dashboard/public-summary",
        "/api/scmaglev/dashboard/trains",
    ):
        try:
            results["apis"].append(check_api(path))
        except Exception as exc:
            results["errors"].append({"path": path, "error": str(exc)})

    for path in ("/static/css/tokens.css", "/static/css/components.css"):
        try:
            status, body, headers = get_with_retry(path)
            results["static"].append({
                "path": path,
                "status": status,
                "bytes": len(body),
                "content_type": headers.get("Content-Type", ""),
            })
        except Exception as exc:
            results["errors"].append({"path": path, "error": str(exc)})

    try:
        _, body, _ = get_with_retry("/api/scmaglev/dashboard/public-summary")
        summary = json.loads(body.decode("utf-8"))
        counts = summary.get("status_counts") or {}
        results["dashboard_summary"] = {
            "in_service": counts.get("in_service"),
            "waiting": counts.get("waiting"),
            "arrived": counts.get("arrived"),
            "tracked": summary.get("tracked_train_count"),
        }
    except Exception as exc:
        results["errors"].append({"path": "summary", "error": str(exc)})

    out = json.dumps(results, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    if results["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
