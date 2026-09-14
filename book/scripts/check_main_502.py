import time
import urllib.error
import urllib.request

BASE = "https://scmaglev.onrender.com"
PATHS = ["/", "/api/health", "/dashboard"]


def probe(path, timeout=90):
    started = time.time()
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            body = resp.read()
            elapsed = time.time() - started
            return {
                "path": path,
                "ok": True,
                "status": resp.status,
                "elapsed": round(elapsed, 1),
                "bytes": len(body),
            }
    except urllib.error.HTTPError as exc:
        elapsed = time.time() - started
        return {
            "path": path,
            "ok": False,
            "status": exc.code,
            "elapsed": round(elapsed, 1),
            "bytes": 0,
        }
    except Exception as exc:
        elapsed = time.time() - started
        return {
            "path": path,
            "ok": False,
            "status": type(exc).__name__,
            "elapsed": round(elapsed, 1),
            "bytes": 0,
            "error": str(exc)[:120],
        }


def main():
    for round_no in range(1, 4):
        print(f"=== round {round_no} ===")
        for path in PATHS:
            result = probe(path)
            print(result)
        time.sleep(5)


if __name__ == "__main__":
    main()
