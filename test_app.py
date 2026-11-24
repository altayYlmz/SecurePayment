from io import BytesIO
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class PaymentsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = patch.object(app, "DB", Path(tmp.name) / "test.sqlite3")
        db.start()
        self.addCleanup(db.stop)
        app.initialize()
        self.alice = app.add_user("alice")
        self.bob = app.add_user("bob")

    def request(self, method, path, token, expected, obj=None):
        payload = json.dumps(obj).encode() if obj is not None else b""
        route, _, query = path.partition("?")
        env = {"REQUEST_METHOD": method, "PATH_INFO": route, "QUERY_STRING": query,
               "HTTP_AUTHORIZATION": "Bearer " + token, "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(payload)), "wsgi.input": BytesIO(payload)}
        status = []
        data = json.loads(b"".join(app.app(env, lambda line, headers: status.append(line))))
        self.assertEqual(int(status[0].split()[0]), expected, data)
        return data

    def test_payment_lifecycle(self):
        body = {"reference": "order-01", "amount_minor": 2590, "currency": "TRY"}
        payment = self.request("POST", "/payments", self.alice, 201, body)
        path = "/payments/" + payment["id"]
        self.assertEqual(self.request("POST", "/payments", self.alice, 200, body), payment)
        self.request("POST", "/payments", self.alice, 409, dict(body, amount_minor=1))
        self.request("POST", path + "/simulate", self.alice, 409)
        for action, status in app.STEPS.items():
            result = self.request("POST", path + "/" + action, self.alice, 200)
            self.assertEqual(result["status"], status[1])
            self.request("POST", path + "/" + action, self.alice, 409)

    def test_scope_and_search(self):
        body = {"reference": "Ali's order", "amount_minor": 150, "currency": "TRY"}
        p = self.request("POST", "/payments", self.alice, 201, body)
        path = "/payments/" + p["id"]
        self.request("GET", path, "", 401)
        self.request("GET", path, self.bob, 404)
        self.request("POST", path + "/authorize", self.bob, 404)
        search = "/payments?reference=Ali%27s%20order"
        self.assertEqual(self.request("GET", search, self.alice, 200), [p])
        self.assertEqual(self.request("GET", search, self.bob, 200), [])
        self.request("POST", "/payments", self.alice, 400, dict(body, amount_minor=-1))
