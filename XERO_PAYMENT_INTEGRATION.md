# Xero Payment Integration

## Overview

Payments are now synchronized from Xero via API validation. Users **cannot** manually add payments - all payments must be validated against the Xero API before being recorded in Saleor.

## Architecture

### Flow
1. **Xero Webhook** → External Xero integration receives payment notification from Xero
2. **External Integration** → Calls Saleor's `orderSyncXeroPayment` GraphQL mutation
3. **Saleor Validation** → Calls Xero API to validate payment and fetch amount
4. **Payment Creation** → If valid, creates Payment record in Saleor

### Why This Approach?
- **Security**: Cannot fake payments - must exist in Xero
- **Data Integrity**: Payment amounts come from Xero, not user input
- **Audit Trail**: All payments are validated against source of truth

## GraphQL API

### Query: `availableXeroPayments`

Lists the last 5 Xero payments for an order's customer (for dropdown selection).

```graphql
query AvailableXeroPayments($orderId: ID!) {
  availableXeroPayments(orderId: $orderId) {
    payments {
      paymentId
      amount
      date
      invoiceNumber
      status
    }
    errors {
      code
      message
    }
  }
}
```

**Requirements:**
- Order must have a customer (`order.user`)
- Customer must have `xero_contact_id` set (auto-populated on first payment sync)

**Returns:**
- Last 5 payments from Xero for that contact
- Ordered by date (most recent first)
- All payments regardless of whether already synced (frontend filters)

### Mutation: `orderSyncXeroPayment`

Syncs a Xero payment to a Saleor order by validating with Xero API.

```graphql
mutation SyncXeroPayment($orderId: ID!, $xeroPaymentId: String!, $isDeposit: Boolean) {
  orderSyncXeroPayment(
    orderId: $orderId
    xeroPaymentId: $xeroPaymentId
    isDeposit: $isDeposit
  ) {
    errors {
      field
      code
      message
    }
    payment {
      id
      gateway
      pspReference
      capturedAmount {
        amount
      }
    }
    order {
      totalDepositPaid
      depositThresholdMet
      depositPaidAt
    }
  }
}
```

**Arguments:**
- `orderId` (required): Saleor order ID
- `xeroPaymentId` (required): Xero payment ID to validate
- `isDeposit` (optional, default false): Whether this is a deposit payment

**Note:** Payment amount is **not** provided - it's fetched from Xero API.

## Xero API Integration

### Configuration

Required environment variables:
- `XERO_TENANT_ID`: Your Xero tenant/organization ID
- `XERO_ACCESS_TOKEN`: OAuth2 access token for Xero API

### Validation Logic

The mutation calls `validate_xero_payment()` which:

1. Calls Xero API: `GET https://api.xero.com/api.xro/2.0/Payments/{paymentId}`
2. Verifies payment exists
3. Checks status is `AUTHORISED`
4. Extracts:
   - Payment amount
   - Payment date
   - Associated invoice ID
   - Payment status
   - **Xero contact ID (customer)**

### Customer Validation

**Critical:** Payments must belong to the correct customer:

1. If order has a user with `xero_contact_id`:
   - **Validates** payment's contact ID matches user's contact ID
   - **Rejects** if mismatch (prevents wrong customer payments)

2. If order has a user WITHOUT `xero_contact_id`:
   - **Auto-populates** user's `xero_contact_id` from payment
   - Future payments will be validated against this ID

3. If order has no user (guest checkout):
   - Payment accepted without customer validation

### Error Handling

| Error | Code | Description |
|-------|------|-------------|
| Payment not found in Xero | `INVALID` | Payment ID doesn't exist |
| Payment status not AUTHORISED | `INVALID` | Payment not in valid state |
| Duplicate payment ID | `UNIQUE` | Payment already synced to this order |
| **Customer mismatch** | `INVALID` | Payment belongs to different Xero contact |
| Xero API error | `INVALID` | Network/API failure |
| Missing Xero config | `INVALID` | XERO_TENANT_ID or XERO_ACCESS_TOKEN not set |

## External Integration Requirements

Your external Xero integration app must:

1. **Listen to Xero webhooks** for payment events
2. **Map Xero invoice to Saleor order** (via invoice number, metadata, etc.)
3. **Call `orderSyncXeroPayment` mutation** with:
   - Saleor order ID
   - Xero payment ID
   - `isDeposit: true` if this is a deposit payment

### Example Integration Flow

```python
# Pseudocode for external Xero integration
@webhook_endpoint("/xero/payments")
def handle_xero_payment_webhook(data):
    payment_id = data["paymentId"]
    invoice_id = data["invoiceId"]

    # Map Xero invoice to Saleor order
    saleor_order_id = get_saleor_order_for_invoice(invoice_id)

    # Determine if deposit based on order state
    is_deposit = check_if_deposit_payment(saleor_order_id)

    # Call Saleor mutation
    saleor_graphql.mutate(
        """
        mutation {
          orderSyncXeroPayment(
            orderId: "%s"
            xeroPaymentId: "%s"
            isDeposit: %s
          ) {
            errors { message }
          }
        }
        """ % (saleor_order_id, payment_id, is_deposit)
    )
```

## Deposit Logic

When `isDeposit: true`:
- Payment is recorded with `metadata.is_deposit = True`
- If deposit threshold is met → `order.deposit_paid_at` is set
- Fulfillment creation is blocked until deposit threshold met

## Changes from Previous Implementation

### ❌ Removed: `orderAddXeroPayment`
- Accepted manual `amount` input
- No Xero validation
- Security risk - could fake payments

### ✅ New: `orderSyncXeroPayment`
- No `amount` argument - fetched from Xero
- Validates with Xero API before creating payment
- Only callable after Xero webhook notification

## Testing

Tests mock the Xero API validation:

```python
from unittest.mock import patch
from saleor.payment.xero import XeroPaymentData, Decimal

mock_data = XeroPaymentData(
    payment_id="XERO-PMT-123",
    amount=Decimal("100.00"),
    date="2024-01-15T10:00:00",
    invoice_id="INV-123",
    status="AUTHORISED",
)

with patch(
    "saleor.graphql.order.mutations.order_add_xero_payment.validate_xero_payment",
    return_value=mock_data,
):
    # Call mutation
    ...
```

## Database Changes

### User Model
Added field: `xero_contact_id` (CharField, nullable, indexed)
- Stores Xero contact/customer ID
- Auto-populated on first payment sync
- Used to validate future payments belong to correct customer
- Migration: `saleor/account/migrations/0096_add_xero_contact_id.py`

## Files Modified

- `saleor/account/models.py` - Added `xero_contact_id` field to User
- `saleor/account/migrations/0096_add_xero_contact_id.py` - Database migration
- `saleor/payment/xero.py` - Xero API validation module with customer validation
- `saleor/graphql/order/mutations/order_add_xero_payment.py` - Mutation with customer validation
- `saleor/graphql/order/schema.py` - Updated mutation export
- `saleor/graphql/order/tests/mutations/test_order_deposit_mutations.py` - Tests with customer validation

## Frontend Changes Required

Update GraphQL mutation calls:
- Change `orderAddXeroPayment` → `orderSyncXeroPayment`
- Remove `amount` argument
- Handle new validation errors from Xero API
