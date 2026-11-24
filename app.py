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
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(name),
    reference TEXT NOT NULL, amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
    currency TEXT NOT NULL CHECK(currency = 'TRY'),
    status TEXT NOT NULL CHECK(status IN ('created','authorized','paid','refunded')),
    UNIQUE(owner, reference)
);
"""
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


def add_user(name):
    token = secrets.token_urlsafe(32)
    with closing(connect()) as db, db:
        db.execute("INSERT INTO accounts VALUES (?, ?)", (name, digest(token)))
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


def dispatch(db, env):
    method, path = env["REQUEST_METHOD"], env["PATH_INFO"]
    header = env.get("HTTP_AUTHORIZATION", "")
    account = db.execute("SELECT name FROM accounts WHERE digest=?",
                         (digest(header[7:]),)).fetchone() if header.startswith("Bearer ") else None
    if account is None:
        raise ApiError(401, "Valid bearer token required")
    owner = account["name"]
    if method == "GET" and path == "/payments":
        query = parse_qs(env["QUERY_STRING"], keep_blank_values=True)
        if not query:
            rows = db.execute("SELECT * FROM payments WHERE owner=? ORDER BY rowid DESC", (owner,))
        elif set(query) == {"reference"} and len(query["reference"]) == 1:
            rows = db.execute("SELECT * FROM payments WHERE owner=? AND reference=?",
                              (owner, query["reference"][0]))
        else:
            raise ApiError(400, "Expected a single reference filter")
        return 200, [dict(row) for row in rows]
    if method == "POST" and path == "/payments":
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
        if body(env):
            raise ApiError(400, "Action accepts no parameters")
        before, after = STEPS[action]
        changed = db.execute("UPDATE payments SET status=? WHERE id=? AND owner=? AND status=?",
                             (after, pid, owner, before)).rowcount
    row = db.execute("SELECT * FROM payments WHERE id=? AND owner=?", (pid, owner)).fetchone()
    if row is None:
        raise ApiError(404, "Payment not found")
    if action and not changed:
        raise ApiError(409, "Action requires status " + before)
    return 200, dict(row)


def app(env, start_response):
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
    args = parser.parse_args()
    initialize()
    if args.command == "add-user":
        if not args.name:
            parser.error("Usage: python app.py add-user NAME")
        try:
            print(add_user(args.name))
        except sqlite3.IntegrityError:
            parser.exit(1, "Account already exists. Choose a new name.\n")
    else:
        with make_server("127.0.0.1", 8000, app) as server:
            print("SecurePayment sandbox listening at http://127.0.0.1:8000", flush=True)
            server.serve_forever()
