import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults

import app


class ReferenceSearchTest(unittest.TestCase):
    def test_reference_is_literal_and_results_are_account_scoped(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            app, "DB", Path(directory) / "payments.sqlite3"
        ):
            app.initialize()
            token = app.add_user("customer-a")
            app.add_user("customer-b")
            reference = "Ali's 50%_discount + gift"
            payments = (
                ("a" * 32, "customer-a", reference, 1250),
                ("b" * 32, "customer-b", reference, 2400),
                ("c" * 32, "customer-a", "Ali's 50XXdiscount + gift", 1500),
            )
            with app.closing(app.connect()) as db, db:
                db.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, 'TRY', 'created')", payments)

            attack = "' OR 1=1 -- "
            cases = (
                (reference, ["a" * 32]),
                ("SIP-MISSING", []),
                (attack, []),
            )
            for candidate, expected in cases:
                with self.subTest(reference=candidate):
                    env = {}
                    setup_testing_defaults(env)
                    env.update(
                        PATH_INFO="/payments",
                        QUERY_STRING=urlencode({"reference": candidate}),
                        HTTP_AUTHORIZATION="Bearer " + token,
                    )
                    statuses = []
                    result = json.loads(b"".join(app.app(env, lambda status, _: statuses.append(status))))
                    self.assertEqual(statuses, ["200 OK"])
                    self.assertEqual([row["id"] for row in result], expected)
