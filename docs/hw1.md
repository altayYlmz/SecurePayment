# HW1 — SQL Injection

## 1. Vulnerable feature

The vulnerable feature is the payment reference search used from the normal payment list. An authenticated customer sends `GET /payments?reference=<value>`. At tag `hw1-vulnerable`, `app.py` builds this query by inserting the supplied reference into the SQL text:

```python
sql = f"SELECT * FROM payments WHERE {visible} AND reference='{reference}'"
```

The vulnerable statement is in `dispatch()` in `app.py` at the `hw1-vulnerable` tag. Record the exact line number after tagging with `git show hw1-vulnerable:app.py`.

## 2. Attacker

The attacker is an authenticated customer of the demo payment system. They want the reference-search feature to return payment records belonging to another customer. The attacker can submit a reference through the ordinary search interface and inspect the returned records.

Pairing-post threat description: **[PASTE THE PAIR'S THREE-SENTENCE DESCRIPTION HERE]**.

## 3. Exploit

Run the application locally with seeded fake customer accounts and fake payment records only. Sign in as customer A, then submit this value in the normal **Referansla ara** field:

```text
' OR 1=1 -- 
```

The space after `--` terminates the injected SQL comment. With the vulnerable query, the resulting condition is effectively `... AND reference='' OR 1=1`, so the search can return records outside the signed-in customer's account. Expected behavior is to return only customer A's matching record, or no records. Actual vulnerable behavior is to return records for both seeded customers.

Run the automated regression test from the repository root with:

```powershell
python -m unittest discover -v -s tests/security -p "hw*.py"
```

The test asserts that the injection input is treated as a literal reference and returns no rows. It passes on `hw1-fixed` and fails on `hw1-vulnerable`, where the seeded payment records are returned.

## 4. Impact

An authenticated customer can read other customers' payment records through the search feature. Exposed fields include account owner, reference, amount, currency, and status. The impact is **High** for confidentiality because it crosses account boundaries in a payment system. This demo uses synthetic data and moves no real money.

## 5. Fix

The fixed version keeps the SQL structure fixed and binds the search text as a SQLite parameter:

```python
rows = db.execute(
    f"SELECT * FROM payments WHERE {visible} AND reference=?",
    (*scope, query["reference"][0]),
)
```

The reference is therefore treated as a literal value, and the account visibility condition remains in effect. The server derives the account scope from the authenticated bearer token. The search also accepts exactly one `reference` filter. Input validation supplements parameter binding, while the security property comes from parameterized SQL and server-side account scoping.

## 6. Verification

The automated test in `tests/security/hw1_reference_search.py` uses a temporary SQLite database and two synthetic customers. It submits the injection payload as customer A and asserts that it returns no rows because it is not a real reference. The same test fails on `hw1-vulnerable`, which returns the seeded records, and passes on `hw1-fixed`.

The GitHub Actions workflow at `.github/workflows/security.yml` runs the application tests and security tests. Fixed-tag CI run: **[ADD THE GREEN GITHUB ACTIONS RUN LINK AFTER PUSHING]**.

## 7. Residual risk

This homework verifies SQL injection resistance in payment reference search and account isolation for that route. Other routes and future queries still require review when they accept user-controlled values or construct SQL dynamically. The demo does not assess production payment infrastructure, deployment configuration, or real payment processing.

## 8. Contribution

- **[PARTNER 1 NAME / STUDENT NUMBER]** — [describe actual work performed].
- **[PARTNER 2 NAME / STUDENT NUMBER]** — [describe actual work performed].
