# SecurePayment

T3 Secure Payment Access is a local REST API for simulated TRY payments. Customers place orders and track their own payments. Staff of the demo store share the checkout queue and can authorize, settle and refund payments. Amounts are integer kuruş.

## Run on Windows

Use Python 3.10 or newer. In PowerShell, from this directory:

```powershell
python app.py serve
```

Create one account for each role in another terminal. Use new account names if these already exist.

```powershell
python app.py add-user musteri-demo
python app.py add-user magaza-demo --role store
```

Open http://127.0.0.1:8000 in two browser tabs. Enter the customer key in one tab and the store key in the other. Existing keys open customer accounts.
Choose quantities from the stationery catalog and submit the cart as the customer. The server calculates the total and assigns the reference.
Use **Yenile** on the store screen to see and process the order. The customer can refresh **Siparişlerim** to see its current status.
Select a reference to view the original order items.
Refreshing the page or choosing **Çıkış** clears the token from the page.

The store account can also create a manual payment through the API. Create a separate store account for this example:

```powershell
$token = python app.py add-user api-magaza-demo --role store
$headers = @{Authorization = "Bearer $token"}
$base = "http://127.0.0.1:8000/payments"
$body = @{reference="demo-001"; amount_minor=2590; currency="TRY"} | ConvertTo-Json
$payment = Invoke-RestMethod $base -Method Post -Headers $headers -ContentType application/json -Body $body
Invoke-RestMethod "$base/$($payment.id)/authorize" -Method Post -Headers $headers
Invoke-RestMethod "$base/$($payment.id)/simulate" -Method Post -Headers $headers
Invoke-RestMethod "${base}?reference=demo-001" -Headers $headers
Invoke-RestMethod "$base/$($payment.id)/refund" -Method Post -Headers $headers
```

The example simulates ₺25.90 and then refunds it. The payment list supports searching by reference and restricts records according to the signed-in account. Run `python -m unittest -v` for the integration checks. Security regression tests are in `tests/security` and use a temporary database.
