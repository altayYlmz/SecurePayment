"""Local-only payment simulator. Run with Python 3.10 or newer."""
import argparse
import hashlib
import json
import re
import secrets
import sqlite3
from contextlib import closing
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4
from wsgiref.simple_server import make_server

DB = Path(__file__).with_name("sandbox.sqlite3")
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS store_accounts (
    name TEXT PRIMARY KEY REFERENCES accounts(name)
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(name),
    reference TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    currency TEXT NOT NULL CHECK(currency = 'TRY'),
    status TEXT NOT NULL CHECK(status IN ('created','authorized','paid','refunded')),
    UNIQUE(owner, reference)
);
CREATE TABLE IF NOT EXISTS orders (
    owner TEXT NOT NULL REFERENCES accounts(name),
    checkout_id TEXT NOT NULL,
    payment_id TEXT NOT NULL UNIQUE REFERENCES payments(id),
    items_json TEXT NOT NULL,
    PRIMARY KEY(owner, checkout_id)
);
"""
CATALOG = {
    "notebook": {"name": "Keten kapaklı defter", "description": "A5, çizgisiz, 160 sayfa", "unit_minor": 24990},
    "pencil": {"name": "Mekanik kalem", "description": "0,7 mm, metal gövde", "unit_minor": 15990},
    "case": {"name": "Kanvas kalemlik", "description": "Fermuarlı, doğal kanvas", "unit_minor": 12990},
}
STEPS = {"authorize": ("created", "authorized"), "simulate": ("authorized", "paid"),
         "refund": ("paid", "refunded")}


class ApiError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def initialize():
    with closing(connect()) as db:
        db.executescript(SCHEMA)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def add_user(name, role="customer"):
    token = secrets.token_urlsafe(32)
    with closing(connect()) as db, db:
        db.execute("INSERT INTO accounts VALUES (?, ?)", (name, digest(token)))
        if role == "store":
            db.execute("INSERT INTO store_accounts VALUES (?)", (name,))
    return token


def body(env):
    try:
        size = int(env.get("CONTENT_LENGTH") or 0)
        if not 0 <= size <= 4096:
            raise ApiError(413, "Request too large")
        if not size:
            return {}
        if env.get("CONTENT_TYPE", "").split(";")[0].strip() != "application/json":
            raise ApiError(415, "Use application/json")
        obj = json.loads(env["wsgi.input"].read(size))
        if isinstance(obj, dict):
            return obj
    except (ValueError, RecursionError):
        pass
    raise ApiError(400, "Expected a JSON object")


def create_order(db, owner, obj):
    if set(obj) != {"checkout_id", "quantities"}:
        raise ApiError(400, "Expected checkout_id and quantities")
    checkout_id, quantities = obj["checkout_id"], obj["quantities"]
    if not isinstance(checkout_id, str) or not re.fullmatch(r"[0-9a-f]{32}", checkout_id):
        raise ApiError(400, "Invalid checkout ID")
    if not isinstance(quantities, dict) or not quantities or quantities.keys() - CATALOG.keys():
        raise ApiError(400, "Select at least one known product")
    if any(type(qty) is not int or not 1 <= qty <= 20 for qty in quantities.values()):
        raise ApiError(400, "Each quantity must be between 1 and 20")
    db.execute("BEGIN IMMEDIATE")
    existing = db.execute(
        "SELECT payments.*, orders.items_json FROM payments "
        "JOIN orders ON orders.payment_id=payments.id "
        "WHERE orders.owner=? AND orders.checkout_id=?", (owner, checkout_id),
    ).fetchone()
    if existing:
        result = dict(existing)
        result["items"] = json.loads(result.pop("items_json"))
        if {item["sku"]: item["quantity"] for item in result["items"]} != quantities:
            raise ApiError(409, "Checkout already used for a different cart")
        return 200, result
    items = [
        {"sku": sku, "name": CATALOG[sku]["name"], "quantity": qty,
         "unit_minor": CATALOG[sku]["unit_minor"]}
        for sku, qty in quantities.items()
    ]
    amount = sum(item["unit_minor"] * item["quantity"] for item in items)
    payment_id = uuid4().hex
    reference = "SIP-" + payment_id.upper()
    db.execute("INSERT INTO payments VALUES (?,?,?,?,?,'created')",
               (payment_id, owner, reference, amount, "TRY"))
    db.execute("INSERT INTO orders VALUES (?,?,?,?)",
               (owner, checkout_id, payment_id, json.dumps(items)))
    payment = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    return 201, {**dict(payment), "items": items}


def dispatch(db, env):
    method, path = env["REQUEST_METHOD"], env["PATH_INFO"]
    header = env.get("HTTP_AUTHORIZATION", "")
    account = db.execute("SELECT name, EXISTS(SELECT 1 FROM store_accounts "
                         "WHERE store_accounts.name=accounts.name) AS is_store "
                         "FROM accounts WHERE digest=?",
                         (digest(header[7:]),)).fetchone() if header.startswith("Bearer ") else None
    if account is None:
        raise ApiError(401, "Valid bearer token required")
    owner = account["name"]
    is_store = bool(account["is_store"])
    # Store staff share the checkout queue. Manual payments stay private to their owner.
    visible = "(owner=? OR (? AND EXISTS(SELECT 1 FROM orders WHERE payment_id=payments.id)))"
    scope = (owner, is_store)
    if method == "GET" and path == "/session":
        return 200, {"name": owner, "role": "store" if is_store else "customer"}
    if method == "GET" and path == "/catalog":
        return 200, [{"sku": sku, **product} for sku, product in CATALOG.items()]
    if method == "POST" and path == "/orders":
        if is_store:
            raise ApiError(403, "Checkout requires a customer account")
        return create_order(db, owner, body(env))
    if method == "GET" and path == "/payments":
        query = parse_qs(env["QUERY_STRING"], keep_blank_values=True)
        if not query:
            rows = db.execute(f"SELECT * FROM payments WHERE {visible} ORDER BY rowid DESC", scope)
        elif set(query) == {"reference"} and len(query["reference"]) == 1:
            rows = db.execute(f"SELECT * FROM payments WHERE {visible} AND reference=?",
                              (*scope, query["reference"][0]))
        else:
            raise ApiError(400, "Expected a single reference filter")
        return 200, [dict(row) for row in rows]
    if method == "POST" and path == "/payments":
        if not is_store:
            raise ApiError(403, "Manual payments require a store account")
        obj = body(env)
        if set(obj) != {"reference", "amount_minor", "currency"}:
            raise ApiError(400, "Expected reference, amount_minor and currency")
        ref, amount = obj["reference"], obj["amount_minor"]
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 80:
            raise ApiError(400, "Reference must contain 1 to 80 characters")
        if type(amount) is not int or not 1 <= amount <= 100000000 or obj["currency"] != "TRY":
            raise ApiError(400, "Expected 1 to 100000000 kurus and currency TRY")
        pid = uuid4().hex
        inserted = db.execute("INSERT INTO payments VALUES (?,?,?,?,?,'created') "
                              "ON CONFLICT(owner,reference) DO NOTHING",
                              (pid, owner, ref, amount, "TRY")).rowcount
        row = db.execute("SELECT * FROM payments WHERE owner=? AND reference=?", (owner, ref)).fetchone()
        if row["amount_minor"] != amount:
            raise ApiError(409, "Reference already used for a different payment")
        return (201 if inserted else 200), dict(row)
    match = re.fullmatch(r"/payments/([0-9a-f]{32})(?:/(authorize|simulate|refund))?", path)
    if not match:
        raise ApiError(404, "Unknown route")
    pid, action = match.groups()
    if method != ("POST" if action else "GET"):
        raise ApiError(405, "Unsupported method")
    if action:
        if not is_store:
            raise ApiError(403, "Payment actions require a store account")
        if body(env):
            raise ApiError(400, "Action accepts no parameters")
        before, after = STEPS[action]
        changed = db.execute(f"UPDATE payments SET status=? WHERE {visible} AND id=? AND status=?",
                             (after, *scope, pid, before)).rowcount
    row = db.execute(f"SELECT * FROM payments WHERE {visible} AND id=?", (*scope, pid)).fetchone()
    if row is None:
        raise ApiError(404, "Payment not found")
    if action and not changed:
        raise ApiError(409, "Action requires status " + before)
    result = dict(row)
    if not action:
        order = db.execute("SELECT items_json FROM orders WHERE owner=? AND payment_id=?",
                           (result["owner"], pid)).fetchone()
        if order:
            result["items"] = json.loads(order["items_json"])
    return 200, result


def app(env, start_response):
    if env["REQUEST_METHOD"] == "GET" and env["PATH_INFO"] == "/":
        page = Path(__file__).with_name("index.html").read_bytes()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"),
                                  ("Content-Length", str(len(page))), ("Cache-Control", "no-store")])
        return [page]
    try:
        with closing(connect()) as db, db:
            code, result = dispatch(db, env)
    except ApiError as err:
        code, result = err.code, {"error": err.message}
    output = json.dumps(result).encode()
    start_response(f"{code} {HTTPStatus(code).phrase}", [("Content-Type", "application/json"),
                   ("Content-Length", str(len(output))), ("Cache-Control", "no-store")])
    return [output]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["add-user", "serve"])
    parser.add_argument("name", nargs="?")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--role", choices=["customer", "store"], default="customer")
    args = parser.parse_args()
    initialize()
    if args.command == "add-user":
        if not args.name:
            parser.error("Usage: python app.py add-user NAME")
        try:
            print(add_user(args.name, args.role))
        except sqlite3.IntegrityError:
            parser.exit(1, "Account already exists. Choose a new name.\n")
    else:
        with make_server("127.0.0.1", args.port, app) as server:
            print(f"SecurePayment sandbox listening at http://127.0.0.1:{server.server_port}", flush=True)
            server.serve_forever()
