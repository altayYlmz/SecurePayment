# SecurePayment

T3 Secure Payment Access is a local REST API for simulated TRY payments. Each sandbox account can create, authorize, settle, search and refund its own payments. Amounts are integer kuruş. The server determines the owner and status. A payment reference is unique per account, so retrying creation with the same reference and amount returns the original payment.

## Run on Windows

Use Python 3.10 or newer. In PowerShell, from this directory:

```powershell
python app.py serve
```

In a second PowerShell window in the same directory, create a demo account once and keep the token in that window:

```powershell
$token = python app.py add-user demo
$headers = @{Authorization = "Bearer $token"}
$base = "http://127.0.0.1:8000/payments"
$body = @{reference="demo-001"; amount_minor=2590; currency="TRY"} | ConvertTo-Json
$payment = Invoke-RestMethod $base -Method Post -Headers $headers -ContentType application/json -Body $body
Invoke-RestMethod "$base/$($payment.id)/authorize" -Method Post -Headers $headers
Invoke-RestMethod "$base/$($payment.id)/simulate" -Method Post -Headers $headers
Invoke-RestMethod "${base}?reference=demo-001" -Headers $headers
Invoke-RestMethod "$base/$($payment.id)/refund" -Method Post -Headers $headers
```

The example simulates ₺25.90 and then refunds it. The reference filter performs an exact, parameterized lookup scoped to the authenticated account. Run `python -m unittest -v` for the integration checks. Tests use a temporary database.
