# Xero Integration Service Architecture

## Overview

A separate service that bridges Xero ↔ Saleor by handling webhooks from both systems.

```
┌──────────────────────────────────────────────────────┐
│                  Xero (Cloud)                        │
│  - Invoices created                                  │
│  - Payments received                                 │
└──────────────┬───────────────────────────────────────┘
               │ Webhooks (payments, invoices)
               ↓
┌──────────────────────────────────────────────────────┐
│         External Integration Service                 │
│  - Receives Xero webhooks                           │
│  - Receives Saleor webhooks                         │
│  - Maps data between systems                        │
│  - Calls APIs bidirectionally                       │
└──────────────┬───────────────────────────────────────┘
               │ GraphQL mutations
               ↓
┌──────────────────────────────────────────────────────┐
│              Saleor (Your Backend)                   │
│  - Orders                                            │
│  - Fulfillments                                      │
│  - Payments                                          │
└──────────────────────────────────────────────────────┘
```

## Required Webhooks

### 1. Xero → Service (Payment Received)

**Xero Webhook Event:** `PAYMENT` or `INVOICE.PAID`

**Service Endpoint:** `POST /webhooks/xero/payment`

**Xero Payload Example:**
```json
{
  "events": [{
    "eventType": "CREATE",
    "eventCategory": "PAYMENT",
    "resourceId": "abcd-1234-payment-id",
    "resourceUrl": "https://api.xero.com/api.xro/2.0/Payments/abcd-1234",
    "tenantId": "your-tenant-id"
  }]
}
```

**Your Service Logic:**
```python
@app.route('/webhooks/xero/payment', methods=['POST'])
def handle_xero_payment():
    data = request.json

    for event in data['events']:
        if event['eventCategory'] == 'PAYMENT':
            payment_id = event['resourceId']

            # 1. Get payment details from Xero
            payment = xero_client.get_payment(payment_id)
            invoice_id = payment['Invoice']['InvoiceID']

            # 2. Map Xero invoice → Saleor order
            saleor_order_id = get_saleor_order_from_xero_invoice(invoice_id)

            # 3. Determine if deposit or final payment
            is_deposit = check_if_deposit_payment(saleor_order_id)

            # 4. Call Saleor mutation
            saleor_client.mutate("""
                mutation {
                  orderSyncXeroPayment(
                    orderId: "%s"
                    xeroPaymentId: "%s"
                    isDeposit: %s
                  ) {
                    payment { id }
                    errors { message }
                  }
                }
            """ % (saleor_order_id, payment_id, is_deposit))

    return jsonify({"status": "ok"})
```

### 2. Saleor → Service (Proforma Invoice Generated)

**Saleor Webhook Event:** `FULFILLMENT_PROFORMA_INVOICE_GENERATED`

**Service Endpoint:** `POST /webhooks/saleor/proforma`

**Saleor Payload Example:**
```json
{
  "fulfillment": {
    "id": "fulfillment-123",
    "order": {
      "id": "order-456",
      "number": "ORDER-001"
    },
    "lines": [
      {
        "orderLine": {
          "productName": "Widget",
          "quantity": 5,
          "unitPrice": { "gross": { "amount": 10.00 } }
        },
        "quantity": 5
      }
    ],
    "depositAllocatedAmount": 15.00
  }
}
```

**Your Service Logic:**
```python
@app.route('/webhooks/saleor/proforma', methods=['POST'])
def handle_saleor_proforma():
    data = request.json
    fulfillment = data['fulfillment']

    # 1. Calculate invoice amount
    total = calculate_fulfillment_total(fulfillment['lines'])
    deposit = fulfillment['depositAllocatedAmount']
    amount_due = total - deposit

    # 2. Create draft invoice in Xero (optional)
    xero_invoice = xero_client.create_draft_invoice({
        'Type': 'ACCREC',
        'Contact': get_xero_contact_for_order(fulfillment['order']['id']),
        'LineItems': convert_fulfillment_to_line_items(fulfillment['lines']),
        'Reference': f"Saleor Order {fulfillment['order']['number']}",
        'Status': 'DRAFT'
    })

    # 3. Generate PDF
    pdf_url = generate_proforma_pdf(fulfillment, amount_due, deposit)

    # 4. Send email to customer (optional)
    send_proforma_email(fulfillment['order'], pdf_url)

    return jsonify({"status": "ok"})
```

### 3. Saleor → Service (Fulfillment Completed - Generate Final Invoice)

**Saleor Webhook Event:** `FULFILLMENT_APPROVED` or custom event

**Service Endpoint:** `POST /webhooks/saleor/final-invoice`

**Your Service Logic:**
```python
@app.route('/webhooks/saleor/final-invoice', methods=['POST'])
def handle_final_invoice():
    data = request.json
    fulfillment = data['fulfillment']

    # 1. Create final invoice in Xero
    xero_invoice = xero_client.create_invoice({
        'Type': 'ACCREC',
        'Contact': get_xero_contact_for_order(fulfillment['order']['id']),
        'LineItems': convert_picked_quantities_to_line_items(fulfillment),
        'Reference': f"Order {fulfillment['order']['number']} - Fulfillment {fulfillment['id']}",
        'Status': 'AUTHORISED',  # Ready for payment
        'InvoiceNumber': generate_invoice_number(fulfillment)
    })

    # 2. Store Xero invoice ID in Saleor
    saleor_client.mutate("""
        mutation {
          invoiceUpdate(
            id: "%s"
            input: {
              metadata: [
                { key: "xero_invoice_id", value: "%s" }
              ]
            }
          ) {
            invoice { id }
          }
        }
    """ % (fulfillment['invoice']['id'], xero_invoice['InvoiceID']))

    return jsonify({"status": "ok"})
```

## Data Mapping

### How to Map Xero Invoice → Saleor Order

**Option 1: Store Saleor Order ID in Xero Invoice**
```python
# When creating invoice in Xero from Saleor order:
xero_invoice = {
    'Reference': f"Saleor-{saleor_order_id}",  # Store in reference field
    # or
    'LineItems': [{
        'Description': f"Order {order_number} (Saleor ID: {saleor_order_id})"
    }]
}

# When receiving webhook:
def get_saleor_order_from_xero_invoice(xero_invoice_id):
    invoice = xero_client.get_invoice(xero_invoice_id)
    reference = invoice['Reference']
    # Extract: "Saleor-ORDER-ID-HERE"
    return reference.replace("Saleor-", "")
```

**Option 2: Maintain mapping table in your service**
```sql
CREATE TABLE xero_saleor_mapping (
    xero_invoice_id VARCHAR PRIMARY KEY,
    saleor_order_id VARCHAR NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### How to Determine if Payment is Deposit vs Final

```python
def check_if_deposit_payment(saleor_order_id):
    """
    Check if payment should be marked as deposit.

    Logic:
    - If order has deposit_required = True
    - And deposit_threshold_met = False
    - Then it's a deposit payment
    """
    order = saleor_client.query("""
        query {
          order(id: "%s") {
            depositRequired
            depositThresholdMet
            totalDepositPaid
            depositPercentage
            totalGrossAmount
          }
        }
    """ % saleor_order_id)['data']['order']

    if not order['depositRequired']:
        return False

    if order['depositThresholdMet']:
        return False  # Deposit already paid, this is final payment

    return True  # This is a deposit payment
```

## Webhook Setup

### In Xero Developer Portal:

1. Go to https://developer.xero.com/app/manage
2. Select your app
3. Go to "Webhooks"
4. Add webhook URL: `https://your-service.com/webhooks/xero/payment`
5. Select events: `PAYMENT`, `INVOICE`
6. Copy **Webhook Key** (for signature verification)

### In Saleor Dashboard:

1. Go to Settings → Webhooks
2. Create new webhook
3. Target URL: `https://your-service.com/webhooks/saleor/proforma`
4. Events:
   - `FULFILLMENT_PROFORMA_INVOICE_GENERATED`
   - `FULFILLMENT_APPROVED` (optional, for final invoice)
5. Copy **Secret Key** (for signature verification)

## Security: Webhook Verification

### Verify Xero Webhooks:

```python
import hmac
import hashlib
import base64

def verify_xero_webhook(payload, signature, webhook_key):
    """Verify Xero webhook signature."""
    expected = base64.b64encode(
        hmac.new(
            webhook_key.encode(),
            payload.encode(),
            hashlib.sha256
        ).digest()
    ).decode()

    return hmac.compare_digest(signature, expected)

@app.route('/webhooks/xero/payment', methods=['POST'])
def handle_xero_payment():
    signature = request.headers.get('X-Xero-Signature')
    payload = request.get_data(as_text=True)

    if not verify_xero_webhook(payload, signature, XERO_WEBHOOK_KEY):
        return jsonify({"error": "Invalid signature"}), 401

    # Process webhook...
```

### Verify Saleor Webhooks:

```python
import hmac
import hashlib

def verify_saleor_webhook(payload, signature, secret_key):
    """Verify Saleor webhook signature."""
    expected = hmac.new(
        secret_key.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)

@app.route('/webhooks/saleor/proforma', methods=['POST'])
def handle_saleor_proforma():
    signature = request.headers.get('X-Saleor-Signature')
    payload = request.get_data(as_text=True)

    if not verify_saleor_webhook(payload, signature, SALEOR_WEBHOOK_SECRET):
        return jsonify({"error": "Invalid signature"}), 401

    # Process webhook...
```

## Example Service Structure

```
xero-saleor-integration/
├── app.py                      # Main Flask/FastAPI app
├── config.py                   # Configuration
├── requirements.txt
├── webhooks/
│   ├── xero_handler.py        # Xero webhook handlers
│   └── saleor_handler.py      # Saleor webhook handlers
├── clients/
│   ├── xero_client.py         # Xero API wrapper
│   └── saleor_client.py       # Saleor GraphQL client
├── mappers/
│   ├── order_mapper.py        # Map Xero ↔ Saleor orders
│   └── invoice_mapper.py      # Map invoices
├── database/
│   ├── models.py              # Mapping tables
│   └── migrations/
└── utils/
    ├── pdf_generator.py       # Generate proforma PDFs
    └── email_sender.py        # Send emails
```

## Deployment Options

1. **Cloud Functions** (AWS Lambda, Google Cloud Functions)
   - Pay per invocation
   - Auto-scales
   - Good for low-medium traffic

2. **Container** (Docker + ECS/Cloud Run)
   - Full control
   - Can run background jobs
   - Good for high traffic

3. **Serverless Framework** (Vercel, Netlify)
   - Easy deployment
   - Built-in HTTPS
   - Good for simple integrations

## Testing

### Test Xero Webhooks Locally:

```bash
# Use ngrok to expose local service
ngrok http 5000

# Update Xero webhook URL to ngrok URL
# Trigger test webhook from Xero portal
```

### Mock Xero in Development:

```python
# Create fake payment in Xero sandbox
# Or mock the Xero API responses
from unittest.mock import patch

@patch('xero_client.get_payment')
def test_payment_webhook(mock_get_payment):
    mock_get_payment.return_value = {
        'PaymentID': 'test-123',
        'Amount': 100.00,
        'Invoice': {'InvoiceID': 'inv-456'}
    }
    # Test your webhook handler
```

## Next Steps

1. **Choose your tech stack** (Python/Flask, Node.js/Express, etc.)
2. **Set up Xero OAuth** (for API access)
3. **Set up Saleor webhooks** (in dashboard)
4. **Implement webhook handlers** (start with payment sync)
5. **Test with sandbox accounts** (Xero + Saleor dev)
6. **Deploy to production**
7. **Monitor and log** (webhook deliveries, errors)

## Questions?

Let me know if you need:
- Example code for specific tech stack
- Help with Xero OAuth flow
- Webhook payload examples
- Error handling strategies
