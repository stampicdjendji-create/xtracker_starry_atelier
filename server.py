#!/usr/bin/env python3
"""XTracker Intelligence Console.

Zero-dependency local web server and same-origin proxy for the public
xtracker.polymarket.com API.

Usage:
    python server.py --open-browser
"""

from __future__ import annotations

import argparse
import json
import math
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

BASE_URL = "https://xtracker.polymarket.com/api"
GAMMA_URL = "https://gamma-api.polymarket.com"
ROOT = Path(__file__).resolve().parent
INDEX_FILE = ROOT / "index.html"
PREDICTIONS_FILE = ROOT / "predictions.json"
DEFAULT_HANDLE = "elonmusk"
REQUEST_TIMEOUT_SECONDS = 18
CACHE_TTL_SECONDS = 8
GAMMA_CACHE_TTL_SECONDS = 30
MAX_TRACKING_WORKERS = 8
MAX_POSTS = 6000
MAX_PREDICTIONS = 2000


@dataclass
class CacheEntry:
    expires_at: float
    payload: dict[str, Any]


_cache: dict[str, CacheEntry] = {}
_cache_lock = threading.Lock()
_predictions_lock = threading.Lock()


def fetch_gamma(path: str) -> Any:
    """Fetch from Polymarket gamma-api (events, markets)."""
    request = Request(
        f"{GAMMA_URL}{path}",
        headers={
            "User-Agent": "XTracker-Intelligence-Console/2.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and entry.expires_at > time.time():
            return entry.payload
    return None


def _cache_set(key: str, payload: Any, ttl: int = CACHE_TTL_SECONDS) -> None:
    with _cache_lock:
        _cache[key] = CacheEntry(expires_at=time.time() + ttl, payload=payload)


def get_event_by_slug(slug: str) -> dict[str, Any] | None:
    """Fetch Polymarket event metadata (incl. markets + prices) by slug, cached."""
    if not slug:
        return None
    cache_key = f"gamma:event:{slug}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        payload = fetch_gamma(f"/events/slug/{slug}")
    except HTTPError as exc:
        if exc.code == 404:
            _cache_set(cache_key, None, ttl=60)
            return None
        raise
    _cache_set(cache_key, payload, ttl=GAMMA_CACHE_TTL_SECONDS)
    return payload


def _safe_int(value: Any) -> int | None:
    try:
        n = int(value)
        return n if abs(n) < 10**12 else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _parse_json_list(value: Any) -> list[Any]:
    """Polymarket sometimes returns JSON-encoded lists as strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def market_yes_price(market: dict[str, Any]) -> dict[str, Any] | None:
    """Extract YES outcome price/bid/ask from a gamma market object."""
    outcomes = [str(o).strip().lower() for o in _parse_json_list(market.get("outcomes"))]
    prices = _parse_json_list(market.get("outcomePrices"))
    yes_index = next((i for i, o in enumerate(outcomes) if o == "yes"), 0)
    def _num(v: Any) -> float | None:
        try:
            n = float(v)
            return n if math.isfinite(n) else None
        except (TypeError, ValueError):
            return None
    price = _num(prices[yes_index]) if yes_index < len(prices) else None
    return {
        "yesPrice": price,
        "bestAsk": _num(market.get("bestAsk")),
        "bestBid": _num(market.get("bestBid")),
        "question": market.get("question") or market.get("title") or "",
        "slug": market.get("slug") or "",
        "volume": _num(market.get("volume")),
        "liquidity": _num(market.get("liquidity")),
    }


def _load_predictions() -> list[dict[str, Any]]:
    if not PREDICTIONS_FILE.exists():
        return []
    try:
        with PREDICTIONS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_predictions(records: list[dict[str, Any]]) -> None:
    tmp = PREDICTIONS_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(PREDICTIONS_FILE)


def upsert_prediction(record: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a prediction snapshot. Same (trackingId, hour) keeps the latest."""
    with _predictions_lock:
        records = _load_predictions()
        key = (str(record.get("trackingId") or ""), str(record.get("hourKey") or ""))
        # Replace existing snapshot for the same tracking+hour
        records = [r for r in records if (str(r.get("trackingId") or ""), str(r.get("hourKey") or "")) != key]
        records.append(record)
        # Sort newest first, keep bounded
        records.sort(key=lambda r: str(r.get("savedAt") or ""), reverse=True)
        records = records[:MAX_PREDICTIONS]
        _save_predictions(records)
        return {"ok": True, "count": len(records)}


def list_predictions(tracking_id: str | None, limit: int = 200) -> list[dict[str, Any]]:
    with _predictions_lock:
        records = _load_predictions()
    if tracking_id:
        records = [r for r in records if str(r.get("trackingId") or "") == tracking_id]
    records.sort(key=lambda r: str(r.get("savedAt") or ""), reverse=True)
    return records[: max(1, min(limit, 1000))]


def fetch_json(path: str) -> Any:
    request = Request(
        f"{BASE_URL}{path}",
        headers={
            "User-Agent": "XTracker-Intelligence-Console/2.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    if isinstance(payload, (dict, list)):
        return payload
    raise ValueError("XTracker API returned an unexpected payload")


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _iso(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return default


def normalize_tracking(tracking: dict[str, Any]) -> dict[str, Any]:
    detail = fetch_json(f"/trackings/{tracking['id']}?includeStats=true")
    if not isinstance(detail, dict):
        detail = {}
    stats = detail.get("stats") or {}
    if not isinstance(stats, dict):
        stats = {}

    cumulative = _to_int(_first(stats, "cumulative", "total", "totalBetweenStartAndEnd"))
    pct = _clamp(_to_float(stats.get("percentComplete")), 0, 100)
    pace = _to_int(stats.get("pace"))
    # XTracker exposes no top-level `target`; the over/under reference line is
    # `pace` (verified: cumulative / percentComplete * 100 == pace). Derive the
    # canonical target from the most authoritative source available.
    detail_target = _to_int(_first(detail, "target", default=tracking.get("target")))
    if detail_target > 0:
        target = detail_target
    elif pace > 0:
        target = pace
    elif pct > 0:
        target = int(round(cumulative / (pct / 100.0)))
    else:
        target = 0
    # `pace` is the reference line; only fall back to the derived target when
    # the API omits it (never silently equate pace with cumulative).
    if pace <= 0:
        pace = target
    days_remaining = max(0, _to_int(stats.get("daysRemaining")))

    daily = stats.get("daily") if isinstance(stats.get("daily"), list) else []
    return {
        "id": tracking.get("id"),
        "title": tracking.get("title") or detail.get("title") or "Untitled tracking",
        "marketLink": tracking.get("marketLink") or detail.get("marketLink") or "",
        "isActive": bool(tracking.get("isActive", detail.get("isActive", True))),
        "startDate": _iso(_first(detail, "startDate", default=tracking.get("startDate"))),
        "endDate": _iso(_first(detail, "endDate", default=tracking.get("endDate"))),
        "target": target,
        "stats": {
            "percentComplete": pct,
            "cumulative": cumulative,
            "pace": pace,
            "daysElapsed": max(0, _to_float(stats.get("daysElapsed"))),
            "daysRemaining": days_remaining,
            "daily": daily,
        },
    }


def _unwrap(payload: Any) -> Any:
    """XTracker wraps successful responses as {"success": true, "data": ...}.

    The console only consumes the inner payload, so unwrap it here. Gamma-api
    responses are NOT wrapped and must never pass through this helper.
    """
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def normalize_post(post: dict[str, Any]) -> dict[str, Any]:
    text = str(_first(post, "text", "content", "body", default="") or "")
    created = _iso(_first(post, "createdAt", "postedAt", "timestamp", "date"))
    imported = _iso(_first(post, "importedAt", "syncedAt", "updatedAt"))
    platform_post_id = str(_first(post, "platformPostId", "tweetId", "postId", default="") or "")
    url = str(_first(post, "url", "postUrl", "link", default="") or "")
    return {
        "id": str(_first(post, "id", default=platform_post_id) or platform_post_id),
        "platformPostId": platform_post_id,
        "text": text,
        "createdAt": created,
        "importedAt": imported,
        "url": url,
    }


def normalize_peer(user: dict[str, Any]) -> dict[str, Any]:
    trackings = user.get("trackings") if isinstance(user.get("trackings"), list) else []
    active = [item for item in trackings if item.get("isActive")]
    count = user.get("_count") if isinstance(user.get("_count"), dict) else {}
    return {
        "handle": user.get("handle") or "",
        "name": user.get("name") or user.get("handle") or "Unknown",
        "avatarUrl": user.get("avatarUrl") or "",
        "verified": bool(user.get("verified")),
        "platform": user.get("platform") or "X",
        "totalPosts": _to_int(_first(count, "posts", default=_first(user, "totalPosts", "postCount"))),
        "activeTrackings": len(active),
        "trackingCount": len(trackings),
    }


def _date_window(trackings: list[dict[str, Any]]) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    # Query enough source history to render 21 complete America/New_York
    # calendar days, including the current partial ET day.
    required_start = now - timedelta(days=23)
    floor = now - timedelta(days=35)
    starts: list[datetime] = []
    ends: list[datetime] = []
    for tracking in trackings:
        for key, sink in (("startDate", starts), ("endDate", ends)):
            value = tracking.get(key)
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                sink.append(parsed.astimezone(timezone.utc))
            except ValueError:
                pass
    tracking_start = min(starts) if starts else now
    start = min(tracking_start, required_start)
    start = max(start, floor)
    end = max([now, *ends])
    return start.isoformat(), end.isoformat()


def get_dashboard(handle: str, platform: str) -> dict[str, Any]:
    cache_key = f"{platform}:{handle.lower()}"
    now_mono = time.monotonic()
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and entry.expires_at > now_mono:
            return {**entry.payload, "cache": True}

    request_started = time.perf_counter()
    user = _unwrap(fetch_json(f"/users/{handle}?{urlencode({'platform': platform})}"))
    if not isinstance(user, dict):
        raise ValueError("User endpoint returned an invalid payload")

    raw_trackings = user.get("trackings") if isinstance(user.get("trackings"), list) else []
    active = [item for item in raw_trackings if item.get("isActive")]

    trackings: list[dict[str, Any]] = []
    errors: list[str] = []
    if active:
        worker_count = min(MAX_TRACKING_WORKERS, len(active))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {pool.submit(normalize_tracking, item): item for item in active}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    trackings.append(future.result())
                except Exception as exc:
                    errors.append(f"{source.get('title', source.get('id', 'tracking'))}: {exc}")

    order = {str(item.get("id")): index for index, item in enumerate(active)}
    trackings.sort(key=lambda item: order.get(str(item.get("id")), 10_000))

    start_date, end_date = _date_window(trackings)
    posts: list[dict[str, Any]] = []
    peers: list[dict[str, Any]] = []

    def get_posts() -> list[dict[str, Any]]:
        query = urlencode({
            "platform": platform,
            "startDate": start_date,
            "endDate": end_date,
            "timezone": "EST",
        })
        payload = _unwrap(fetch_json(f"/users/{handle}/posts?{query}"))
        raw = payload if isinstance(payload, list) else payload.get("posts", []) if isinstance(payload, dict) else []
        normalized = [normalize_post(item) for item in raw if isinstance(item, dict)]
        normalized.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return normalized[:MAX_POSTS]

    def get_peers() -> list[dict[str, Any]]:
        payload = _unwrap(fetch_json(f"/users?{urlencode({'platform': platform, 'includeInactive': 'false'})}"))
        raw = payload if isinstance(payload, list) else payload.get("users", []) if isinstance(payload, dict) else []
        result = [normalize_peer(item) for item in raw if isinstance(item, dict)]
        result.sort(key=lambda item: (item["activeTrackings"], item["totalPosts"]), reverse=True)
        return result[:24]

    with ThreadPoolExecutor(max_workers=2) as pool:
        post_future = pool.submit(get_posts)
        peer_future = pool.submit(get_peers)
        try:
            posts = post_future.result()
        except Exception as exc:
            errors.append(f"recent posts: {exc}")
        try:
            peers = peer_future.result()
        except Exception as exc:
            errors.append(f"tracked users: {exc}")

    count = user.get("_count") if isinstance(user.get("_count"), dict) else {}
    total_posts = _to_int(_first(count, "posts", default=_first(user, "totalPosts", "postCount")))
    if total_posts <= 0 and posts:
        total_posts = len(posts)

    elapsed_ms = round((time.perf_counter() - request_started) * 1000)
    payload = {
        "cache": False,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "queryWindow": {"startDate": start_date, "endDate": end_date},
        "user": {
            "id": user.get("id"),
            "name": user.get("name") or handle,
            "handle": user.get("handle") or handle,
            "bio": _first(user, "bio", "description", default="") or "",
            "avatarUrl": user.get("avatarUrl") or "",
            "verified": bool(user.get("verified")),
            "platform": user.get("platform") or platform,
            "platformId": user.get("platformId"),
            "lastSync": user.get("lastSync"),
            "syncError": _first(user, "syncError", "lastError", default="") or "",
            "totalPosts": total_posts,
            "activeCount": len(active),
            "trackingCount": len(raw_trackings),
        },
        "trackings": trackings,
        "posts": posts,
        "peers": peers,
        "health": {
            "requestMs": elapsed_ms,
            "postsLoaded": len(posts),
            "trackingsLoaded": len(trackings),
            "peerUsersLoaded": len(peers),
        },
        "partialErrors": errors,
    }

    with _cache_lock:
        _cache[cache_key] = CacheEntry(time.monotonic() + CACHE_TTL_SECONDS, payload)
    return payload

class AppHandler(BaseHTTPRequestHandler):
    server_version = "XTrackerConsole/2.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._serve_index()
            return
        if parsed.path == "/api/dashboard":
            self._serve_dashboard(parsed.query)
            return
        if parsed.path == "/api/market-prices":
            self._serve_market_prices(parsed.query)
            return
        if parsed.path == "/api/predictions":
            self._serve_predictions_get(parsed.query)
            return
        if parsed.path == "/health":
            self._send_json({"ok": True, "mode": "live"})
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/predictions":
            self._serve_predictions_post()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_market_prices(self, query: str) -> None:
        """Return Polymarket event markets (incl. YES prices) for a slug."""
        params = parse_qs(query)
        slug = (params.get("slug", [""])[0] or "").strip()
        if not slug or len(slug) > 200:
            self._send_json({"error": "Missing or invalid slug"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            event = get_event_by_slug(slug)
        except HTTPError as exc:
            self._send_json({"error": "gamma-api request failed", "status": exc.code}, status=HTTPStatus.BAD_GATEWAY)
            return
        except Exception as exc:
            self._send_json({"error": "gamma-api unavailable", "detail": str(exc)}, status=HTTPStatus.BAD_GATEWAY)
            return
        if event is None:
            self._send_json({"slug": slug, "found": False, "markets": []})
            return
        markets = event.get("markets") if isinstance(event, dict) else []
        if not isinstance(markets, list):
            markets = []
        priced = []
        for m in markets:
            if not isinstance(m, dict):
                continue
            entry = market_yes_price(m)
            if entry:
                priced.append(entry)
        self._send_json({
            "slug": slug,
            "found": True,
            "title": event.get("title") or "",
            "markets": priced,
        })

    def _serve_predictions_get(self, query: str) -> None:
        params = parse_qs(query)
        tracking_id = (params.get("trackingId", [""])[0] or "").strip() or None
        try:
            limit = int(params.get("limit", ["200"])[0])
        except ValueError:
            limit = 200
        records = list_predictions(tracking_id, limit=limit)
        self._send_json({"records": records, "count": len(records)})

    def _serve_predictions_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > 32_768:
            self._send_json({"error": "Invalid Content-Length"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            body = self.rfile.read(length)
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"error": "Invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload, dict):
            self._send_json({"error": "Payload must be an object"}, status=HTTPStatus.BAD_REQUEST)
            return
        tracking_id = str(payload.get("trackingId") or "").strip()[:80]
        hour_key = str(payload.get("hourKey") or "").strip()[:40]
        if not tracking_id or not hour_key:
            self._send_json({"error": "trackingId and hourKey are required"}, status=HTTPStatus.BAD_REQUEST)
            return
        record = {
            "trackingId": tracking_id,
            "hourKey": hour_key,
            "savedAt": datetime.now(timezone.utc).isoformat(),
            "handle": str(payload.get("handle") or "")[:40],
            "marketSlug": str(payload.get("marketSlug") or "")[:200],
            "current": _safe_int(payload.get("current")),
            "target": _safe_int(payload.get("target")),
            "remainingMean": _safe_float(payload.get("remainingMean")),
            "finalMean": _safe_float(payload.get("finalMean")),
            "rangeLow": _safe_float(payload.get("rangeLow")),
            "rangeHigh": _safe_float(payload.get("rangeHigh")),
            "breakProbability": _safe_float(payload.get("breakProbability")),
            "breakTarget": _safe_int(payload.get("breakTarget")),
            "mode": str(payload.get("mode") or "near")[:16],
        }
        result = upsert_prediction(record)
        self._send_json(result)

    def _serve_index(self) -> None:
        try:
            body = INDEX_FILE.read_bytes()
        except OSError as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self, query: str) -> None:
        params = parse_qs(query)
        handle = (params.get("handle", [DEFAULT_HANDLE])[0] or DEFAULT_HANDLE).strip().lstrip("@")
        platform = (params.get("platform", ["X"])[0] or "X").upper()
        if platform not in {"X", "TRUTH_SOCIAL"}:
            self._send_json({"error": "Invalid platform"}, status=HTTPStatus.BAD_REQUEST)
            return
        if not handle or len(handle) > 32 or not all(ch.isalnum() or ch == "_" for ch in handle):
            self._send_json({"error": "Invalid social handle"}, status=HTTPStatus.BAD_REQUEST)
            return

        try:
            payload = get_dashboard(handle, platform)
            self._send_json(payload)
        except HTTPError as exc:
            message = f"No XTracker user found for @{handle}" if exc.code == 404 else "XTracker API request failed"
            self._send_json({"error": message, "status": exc.code}, status=HTTPStatus.BAD_GATEWAY)
        except Exception as exc:
            self._send_json(
                {"error": "Unable to load XTracker data", "detail": str(exc)},
                status=HTTPStatus.BAD_GATEWAY,
            )

    def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {self.address_string()} {format % args}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the XTracker intelligence dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--open-browser", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    url_host = "localhost" if args.host in {"0.0.0.0", "127.0.0.1"} else args.host
    mode = "LIVE"
    url = f"http://{url_host}:{args.port}"
    print(f"XTracker Intelligence Console [{mode}] -> {url}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
