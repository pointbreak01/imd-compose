#!/usr/bin/env python3
"""imd-compose: compose, validate, quote, pay for and follow IMD swarm requests.

Serves index.html on 127.0.0.1 and proxies the control plane, because the paid
routes of api.imd.fun refuse cross-origin browsers. Standard library only.

  python3 server.py            # http://127.0.0.1:8790
  PORT=8791 python3 server.py

Orders (with the bearer token that reads them back) are kept in
~/.config/imd-compose/orders.json, mode 0600. The token is not a payment key:
payment is always a wallet signature made in the browser.
"""
import json, os, re, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", int(os.environ.get("PORT", "8790"))
API = os.environ.get("IMD_API", "https://api.imd.fun").rstrip("/")
EXPLORER = "https://explorer.imd.fun"
HOME = os.environ.get("IMD_COMPOSE_HOME", os.path.expanduser("~/.config/imd-compose"))
ORDERS = os.path.join(HOME, "orders.json")
UA = "imd-compose/1 (+local)"
_lock = threading.Lock()

# public reads the page may ask for, relative to API (or EXPLORER for /x/...)
READ_OK = re.compile(r"^/(version|health|services|skills|swarm|workers|contributors|sites|ens|launches|launch/policies"
                     r"|requests/capabilities|research/panels|fuzz/results|publications|seats/records"
                     r"|jobs|jobs/[0-9a-f-]{36}(/(submissions|result|panel|fuzz|records|assessments))?"
                     r"|workflows|workflows/[0-9a-f-]{36}"
                     r"|oracle/requests|oracle/requests/[0-9a-f-]{36}(/(attestation|pools))?"
                     r"|launches/[0-9a-f-]{36}|seats/\d+(/standing)?|reads/[a-z-]+/[^/]+)$")


def load_orders():
    try:
        with open(ORDERS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_orders(rows):
    os.makedirs(HOME, exist_ok=True)
    tmp = ORDERS + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rows, f, indent=1)
    os.replace(tmp, ORDERS)


def upstream(method, url, body=None, headers=None, timeout=30):
    """Call the control plane. Returns (status, headers dict lowercased, body bytes)."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 502, {}, json.dumps({"error": "upstream_unreachable", "detail": str(e)}).encode()


def wrap(status, hdrs, body):
    """Everything the page needs from an upstream reply, as one JSON object."""
    try:
        parsed = json.loads(body or b"null")
    except ValueError:
        parsed = {"raw": body.decode("utf-8", "replace")[:4000]}
    keep = {k: v for k, v in hdrs.items() if k in ("payment-required", "payment-response", "retry-after", "x-payment-response")}
    return {"status": status, "headers": keep, "body": parsed}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def local_host(self):
        """Refuse DNS rebinding: a page on another name pointed at 127.0.0.1 still sends its own Host."""
        host = (self.headers.get("Host") or "").strip().lower()
        name = host[:host.find("]") + 1] if host.startswith("[") else host.rsplit(":", 1)[0]
        return name in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self):
        if not self.local_host():
            return self.send(403, {"error": "forbidden: open the page as 127.0.0.1 or localhost"})
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/local/orders":
            with _lock:
                rows = load_orders()
            # the page gets the token only as a fingerprint; reads go through /local/order/<id>
            return self.send(200, [{k: v for k, v in r.items() if k != "token"} | {"tokenTail": r.get("token", "")[-6:]} for r in rows])
        m = re.match(r"^/local/order/([0-9a-f-]{36})$", path)
        if m:
            row = self._order(m.group(1))
            if not row:
                return self.send(404, {"error": "unknown_local_order"})
            st, hd, bd = upstream("GET", f"{API}/requests/{row['orderId']}", headers={"Authorization": "Bearer " + row["token"]})
            res = wrap(st, hd, bd)
            if st == 200 and isinstance(res["body"], dict):
                with _lock:
                    rows = load_orders()
                    for r in rows:
                        if r["orderId"] == row["orderId"]:
                            r["status"] = res["body"].get("status")
                            adm = (res["body"].get("admission") or {}).get("result")
                            if adm:
                                r["admission"] = adm
                            r["checkedAt"] = int(time.time())
                    save_orders(rows)
            return self.send(200, res)
        if path.startswith("/api/"):
            sub = path[4:]
            base = API
            if sub.startswith("/x/"):  # explorer JSON routes
                base, sub = EXPLORER, sub[2:]
                if not re.match(r"^/api/(activity|agents/\d+)$", sub):
                    return self.send(403, {"error": "route_not_proxied"})
            elif not READ_OK.match(sub):
                return self.send(403, {"error": "route_not_proxied", "detail": sub})
            st, hd, bd = upstream("GET", base + sub + ("?" + query if query else ""))
            return self.send(200, wrap(st, hd, bd))
        return self.send(404, {"error": "not_found"})

    def _order(self, order_id):
        with _lock:
            return next((r for r in load_orders() if r["orderId"] == order_id), None)

    def do_POST(self):
        # loopback + custom header: a cross-site page cannot drive these
        if not self.local_host() or self.headers.get("X-Compose") != "1" or self.client_address[0] != "127.0.0.1":
            return self.send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            return self.send(413, {"error": "too_large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad_json"})
        path = self.path.split("?", 1)[0]

        if path == "/local/quote":
            # body: {action, input, label?} -> new token + requestKey, POST /requests/quote
            import secrets, uuid
            token, key = secrets.token_hex(32), str(uuid.uuid4())
            payload = json.dumps({"requestKey": key, "action": body.get("action"), "input": body.get("input")}).encode()
            if len(payload) > 16 * 1024:
                return self.send(200, {"status": 413, "headers": {}, "body": {"error": "body_too_large", "detail": f"{len(payload)} bytes, limit 16384"}})
            st, hd, bd = upstream("POST", f"{API}/requests/quote", payload, {"Authorization": "Bearer " + token})
            res = wrap(st, hd, bd)
            order = (res["body"] or {}).get("order") if isinstance(res["body"], dict) else None
            if st in (200, 201) and order and order.get("id"):
                with _lock:
                    rows = load_orders()
                    rows.insert(0, {"orderId": order["id"], "token": token, "requestKey": key,
                                    "action": body.get("action"), "input": body.get("input"),
                                    "label": (body.get("label") or "")[:120], "quote": order.get("quote"),
                                    "status": order.get("status"), "createdAt": int(time.time())})
                    save_orders(rows)
            return self.send(200, res)

        m = re.match(r"^/local/submit/([0-9a-f-]{36})$", path)
        if m:
            # body: {} -> the 402 challenge; {paymentSignature, quoteSignature} -> the paid submit
            row = self._order(m.group(1))
            if not row:
                return self.send(404, {"error": "unknown_local_order"})
            hdr = {"Authorization": "Bearer " + row["token"]}
            data = None
            if body.get("paymentSignature"):
                hdr["PAYMENT-SIGNATURE"] = body["paymentSignature"]
                data = json.dumps({"quoteSignature": body.get("quoteSignature")}).encode()
            st, hd, bd = upstream("POST", f"{API}/requests/{row['orderId']}/submit", data, hdr, timeout=90)
            return self.send(200, wrap(st, hd, bd))

        m = re.match(r"^/local/forget/([0-9a-f-]{36})$", path)
        if m:
            with _lock:
                save_orders([r for r in load_orders() if r["orderId"] != m.group(1)])
            return self.send(200, {"ok": True})

        return self.send(404, {"error": "no_such_action"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"imd-compose on http://{HOST}:{PORT}  (upstream {API}, orders in {ORDERS})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
