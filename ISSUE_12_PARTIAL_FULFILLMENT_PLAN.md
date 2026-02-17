# Issue #12: Support Partial Fulfillment with Deposit & Invoice Workflow

## Goal

Enable partial fulfillment with deposit tracking and dual invoice system (proforma + final), where:
1. Orders can require deposits before fulfillment creation
2. Deposits are tracked via Xero payment IDs
3. Each fulfillment gets a proforma invoice (not in Xero) for customer payment
4. Final invoices (in Xero) are generated after shipment using picked quantities
5. Fulfillments can only be auto-created when all PO items have receipts
6. Payment status gates pick/shipment operations

We expect to require a deposit for orders. This will be requested at order confirmation time via an amended email. Changing the emails is outside the scope of this task. We need to beble to mark deposit requested (and potentially the percentage required)?

We then need to enable people to add accounting payment ids to the order, which correspond to the deposit. We use the accounting app (which will be Xero) to check the payment is valid and get the amount paid.
We need to BLOCK orders being fulfilled (as in creating WAITING_FOR_APPROVAL state) until the deposit is paid iff we have deposit_required set.

Once deposit is paid, we allow partial fulfillments. We need to send something (if not done) via GraphQL allowing us to see which parts of the order have arrived in the warehouse. Any products which are in the warehouse can be fulfilled (with WAITING_FOR_APPROVAL state). If all products are in the warehouse and we have the necessary deposit we can create a fulfillment with WAITING_FOR_APPROVAL state.

When we create a fulfillment, we need to:
1. Generate a proforma invoice using an external app (generates PDF)
2. Store it in the Invoice table with type='proforma' and link to the fulfillment
3. Calculate the invoice amount accounting for deposit allocation (FIFO, spread equally across fulfillments to avoid overcharging)
4. Send webhook event with the proforma invoice PDF attached for email delivery

We have a button 'mark proforma invoice as paid'. Note this flag is on the FULFILLMENT. We track the proforma invoice amount in the Invoice record.

Once all 3 of:
1. Pick is complete
2. Shipment is planned
3. Proforma invoice is paid
Then we can move the status to FULFILLED automatically.

---

## Implementation Plan

### Phase 1: Data Model Changes

#### 1.1 Order Model Extensions
- Add `deposit_required` (BooleanField, default=False)
- Add `deposit_percentage` (DecimalField, nullable, for tracking requested %)
- Add `deposit_payment_id` (CharField, nullable, stores Xero payment ID)
- Add `deposit_amount` (DecimalField, nullable, cached from Xero)
- Add `deposit_paid_at` (DateTimeField, nullable)
- Add `remaining_deposit` (calculated property - `deposit_amount` minus sum of `deposit_allocated` from all related fulfillments)

#### 1.2 Invoice Model Extensions
- Add `invoice_type` field with choices: ['STANDARD', 'PROFORMA', 'FINAL']
- Add `fulfillment` foreign key (nullable, for proforma invoices)
- Existing `external_url` can store the PDF location

#### 1.3 Fulfillment Model Extensions
- Add `proforma_invoice` (OneToOne to Invoice, nullable)
- Add `proforma_invoice_paid` (BooleanField, default=False)
- Add `proforma_invoice_paid_at` (DateTimeField, nullable)
- Add `deposit_allocated` (DecimalField, default=0, tracks portion of deposit applied to this fulfillment)
- Add calculated property for checking if pick/ship/payment all complete

### Phase 2: Deposit Management

#### 2.1 GraphQL Mutations
- `orderSetDepositRequired(orderId, required, percentage)` - Mark order as requiring deposit
- `orderAttachDepositPayment(orderId, paymentId)` - Link Xero payment to order
- `orderValidateDeposit(orderId)` - Trigger Xero validation and cache amount

#### 2.2 Xero Integration
- Extend accounting plugin to fetch payment details by ID
- Validate payment amount and status
- Cache amount and timestamp on Order model
- Calculate `remaining_deposit` = `deposit_amount` - sum of all `deposit_allocated` from related fulfillments

#### 2.3 Fulfillment Creation Guards
- Check `deposit_required` flag before allowing fulfillment creation
- If `deposit_required=True`, validate `deposit_payment_id` exists and `deposit_amount > 0`
- Return clear error message if deposit not satisfied

### Phase 3: Warehouse Receipt Visibility

#### 3.1 GraphQL Schema Extension
- Add `OrderLine.warehouseStock` field showing available quantity per warehouse
- Add `OrderLine.canFulfillQuantity` calculated field (min of ordered vs available)
- Add `Order.fulfillableLines` filtered list of lines with stock available

#### 3.2 Stock Tracking Integration
- Query stock allocations/reservations for order lines
- Factor in existing fulfillment allocations
- Return available quantity per line for partial fulfillment UI

### Phase 4: Proforma Invoice Generation

#### 4.1 Invoice Calculation Logic
- Calculate fulfillment total (sum of line items in fulfillment)
- Calculate deposit allocation:
  - Total order value across all fulfillments (completed + pending)
  - Allocate deposit FIFO: spread equally across fulfillments in creation order
  - For current fulfillment: `deposit_credit = min(remaining_deposit, fulfillment_total * (deposit_amount / order_total))`
  - Proforma amount = `fulfillment_total - deposit_credit`
- Store `deposit_allocated` on Fulfillment record

#### 4.2 External PDF Generation
- Create webhook event `FULFILLMENT_PROFORMA_INVOICE_GENERATE`
- Webhook payload includes:
  - Fulfillment details (lines, quantities, prices)
  - Calculated totals (subtotal, deposit credit, net amount)
  - Order and customer details
- External app generates PDF and returns URL/file
- Store PDF in Invoice record `external_url` or file field

#### 4.3 Invoice Record Creation
- Create Invoice with:
  - `invoice_type = 'PROFORMA'`
  - `fulfillment = <current_fulfillment>`
  - `order = <parent_order>`
  - `external_url = <pdf_location>`
- Update Fulfillment with:
  - `deposit_allocated = <calculated_amount>`
  - `proforma_invoice = <created_invoice>`

#### 4.4 Email Notification
- Send webhook event `FULFILLMENT_PROFORMA_INVOICE_READY`
- Payload includes PDF URL/attachment
- External email service sends to customer

### Phase 5: Proforma Payment Tracking

#### 5.1 GraphQL Mutation
- `fulfillmentMarkProformaInvoicePaid(fulfillmentId, paidAt)`
- Sets `proforma_invoice_paid = True`
- Sets `proforma_invoice_paid_at` timestamp
- Triggers auto-transition check (Phase 6)

#### 5.2 GraphQL Queries
- Add `Fulfillment.proformaInvoice` field
- Add `Fulfillment.proformaInvoicePaid` field
- Add `Fulfillment.canTransitionToFulfilled` calculated field

### Phase 6: Auto-Transition to FULFILLED

#### 6.1 Transition Conditions Check
- Create helper: `can_auto_fulfill(fulfillment)` checking:
  1. Pick complete: All fulfillment lines have `quantity_picked >= quantity`
  2. Shipment planned: `tracking_number` is set OR `shipping_method` assigned
  3. Proforma paid: `proforma_invoice_paid = True`

#### 6.2 Auto-Transition Logic
- Hook into existing fulfillment status update flow
- After any field update (pick, shipment, payment), check conditions
- If all 3 met and status is `WAITING_FOR_APPROVAL`, transition to `FULFILLED`
- Trigger existing fulfillment completion webhooks

### Phase 7: GraphQL API Surface

#### 7.1 New Queries
```graphql
type Order {
  depositRequired: Boolean!
  depositPercentage: Decimal
  depositPaymentId: String
  depositAmount: Money
  remainingDeposit: Money
  fulfillableLines: [OrderLine!]!
}

type OrderLine {
  warehouseStock: Int!
  canFulfillQuantity: Int!
}

type Fulfillment {
  proformaInvoice: Invoice
  proformaInvoicePaid: Boolean!
  depositAllocated: Money!
  canTransitionToFulfilled: Boolean!
}

type Invoice {
  invoiceType: InvoiceTypeEnum!
  fulfillment: Fulfillment
}
```

#### 7.2 New Mutations
```graphql
orderSetDepositRequired(orderId, required, percentage)
orderAttachDepositPayment(orderId, paymentId)
orderValidateDeposit(orderId)
fulfillmentMarkProformaInvoicePaid(fulfillmentId, paidAt)
```

### Phase 8: Testing Strategy

#### 8.1 Unit Tests
- Deposit allocation calculation logic (FIFO, equal spread)
- Auto-transition condition checking
- Fulfillment creation guards (deposit validation)

#### 8.2 Integration Tests
- Full workflow: Order → Deposit → Partial Fulfillment → Proforma → Payment → Auto-fulfill
- Multiple partial fulfillments with deposit allocation
- Edge cases: zero deposit, 100% deposit, multiple fulfillments

#### 8.3 Xero Integration Tests
- Mock Xero payment validation
- Test payment ID lookup and caching

---

## Implementation Order

1. **Phase 1**: Data model migrations (all database changes upfront)
2. **Phase 2**: Deposit management (order-level blocking)
3. **Phase 3**: Warehouse visibility (enables UI for partial fulfillment)
4. **Phase 4**: Proforma invoice generation (core complexity)
5. **Phase 5**: Payment tracking (simple flag updates)
6. **Phase 6**: Auto-transition logic (ties everything together)
7. **Phase 7**: GraphQL API exposure (frontend integration)
8. **Phase 8**: Comprehensive testing

---

## Key Design Decisions

- **Deposit Allocation**: FIFO with equal spread prevents overcharging and is simple to calculate
- **Deposit Tracking**: `deposit_allocated` lives on Fulfillment (not Invoice) - keeps Invoice as presentation record, Fulfillment as business logic container
- **Invoice Storage**: Reuse existing Invoice model with type discriminator rather than new table
- **PDF Generation**: External webhook keeps Saleor lightweight, allows custom templates
- **Auto-Transition**: Event-driven checks after any state change ensure immediate updates
- **Proforma vs Final**: Proforma tracks deposit allocation, Final invoices come later (out of scope)
