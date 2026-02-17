# Partial Fulfillment & Deposit Workflow - Implementation Plan

## Overview

This document outlines the changes needed to support:
1. Deposit requests for non-web (draft) orders
2. Partial fulfillment with per-fulfillment invoicing
3. Blocking fulfillment progress until invoices are paid
4. Editing order quantities before fulfillment/invoicing
5. Auto-fulfillment only when all stock is received

## Current System Analysis

### Order Flow
- **Origins**: `CHECKOUT` (web) or `DRAFT` (non-web)
- **Status progression**: `DRAFT` → `UNCONFIRMED` → `UNFULFILLED` → `PARTIALLY_FULFILLED` → `FULFILLED`
- Draft orders complete to `UNCONFIRMED` status initially, then auto-confirm to `UNFULFILLED` when all allocations have sources

### Email Notifications
**Locations**: `saleor/order/notifications.py`, `saleor/order/actions.py`

Current emails:
- `send_order_confirmation()` - sent when order is placed (from checkout or draft)
- `send_order_confirmed()` - sent when order status changes from UNCONFIRMED to UNFULFILLED
- Uses `NotifyEventType.ORDER_CONFIRMATION` and `NotifyEventType.ORDER_CONFIRMED`

### Fulfillment System
**Location**: `saleor/order/models.py` (Fulfillment model)

- Fulfillments created per warehouse with status `FULFILLED` or `WAITING_FOR_APPROVAL`
- Pick document auto-created when fulfillment has `WAITING_FOR_APPROVAL` status
- Fulfillment approval requires (`saleor/graphql/order/mutations/fulfillment_approve.py`):
  - Pick status = `COMPLETED`
  - Shipment linked to fulfillment
  - Order fully paid (unless `site.settings.fulfillment_allow_unpaid=True`)

### Invoice System
**Location**: `saleor/invoice/models.py`

```python
class Invoice:
    order = ForeignKey(Order)  # Can be null
    purchase_order = OneToOneField(PurchaseOrder)  # OneToOne relationship
    type = CharField(choices=InvoiceType.CHOICES)  # FINAL or PROFORMA
    xero_invoice_id = CharField()  # For Xero integration
```

**Current limitations**:
- Invoices linked to entire Order, not to specific Fulfillments
- Cannot create invoice for DRAFT/UNCONFIRMED/EXPIRED orders
- No concept of partial invoicing

### Auto-Fulfillment Logic
**Location**: `saleor/inventory/stock_management.py:confirm_purchase_order_item()`

When POI confirmed (lines 188-254):
1. Checks if order can be auto-confirmed (`can_confirm_order()`)
2. If yes, changes status to `UNFULFILLED`
3. Creates fulfillments per warehouse with `auto_approved=False` (WAITING_FOR_APPROVAL)
4. Sends order confirmed email

**Current behavior**: Auto-confirms when allocations have AllocationSources (partial stock ok)

---

## Required Changes

## 1. Deposit Request for Non-Web Orders

### Database Changes

**Add field to Order model** (`saleor/order/models.py`):
```python
class Order:
    # ... existing fields ...
    requires_deposit = models.BooleanField(
        default=False,
        help_text="If True, deposit payment is required before placing order with supplier"
    )
```

**Migration**:
- Default `False` for existing orders
- Set based on `origin` during draft order creation

### Backend Changes

**1. Draft Order Creation** (`saleor/graphql/order/mutations/draft_order_create.py`):
- Set `requires_deposit=True` by default for new draft orders
- Add GraphQL field to allow toggling

**2. Draft Order Complete** (`saleor/graphql/order/mutations/draft_order_complete.py`):
- Pass `requires_deposit` flag through to `order_created()` action

**3. Order Created Action** (`saleor/order/actions.py:order_created()`):
- Modify to check `order.requires_deposit` and `order.origin == OrderOrigin.DRAFT`
- Call appropriate email notification based on this flag

**4. Email Notifications** (`saleor/order/notifications.py`):

Add new function:
```python
def send_order_confirmation_with_deposit_request(order_info, redirect_url, manager):
    """Send order confirmation email requesting deposit payment."""

    def _generate_payload():
        payload = {
            "order": get_default_order_payload(order_info.order, redirect_url),
            "recipient_email": order_info.customer_email,
            "deposit_percentage": 10,  # Could be configurable
            "deposit_amount": quantize_price(
                order_info.order.total_gross_amount * Decimal("0.1"),
                order_info.order.currency
            ),
            **get_site_context(),
        }
        return payload

    handler = NotifyHandler(_generate_payload)
    manager.notify(
        NotifyEventType.ORDER_CONFIRMATION_DEPOSIT_REQUEST,
        payload_func=handler.payload,
        channel_slug=order_info.channel.slug,
    )
```

Update `order_created()`:
```python
def order_created(...):
    # ... existing code ...

    # Determine which confirmation email to send
    if order.origin == OrderOrigin.DRAFT and order.requires_deposit:
        send_order_confirmation_with_deposit_request(order_info, redirect_url, manager)
    else:
        # Send standard confirmation
        send_order_confirmation(order_info, redirect_url, manager)
```

**5. Email Templates**:
- Add new template for deposit request email
- Template should highlight deposit amount (10% of total) and payment instructions

### Frontend Changes (GraphQL)

**1. Add field to DraftOrderCreate mutation**:
```graphql
input DraftOrderCreateInput {
  # ... existing fields ...
  requiresDeposit: Boolean = true
}
```

**2. Add field to DraftOrderUpdate mutation**:
```graphql
input DraftOrderUpdateInput {
  # ... existing fields ...
  requiresDeposit: Boolean
}
```

**3. Add field to Order type**:
```graphql
type Order {
  # ... existing fields ...
  requiresDeposit: Boolean!
}
```

---

## 2. Partial Fulfillment Support

### Conceptual Change

**Current**: Invoice → Order (entire order)
**New**: Invoice → Fulfillment (part of order)

### Database Changes

**Modify Invoice model** (`saleor/invoice/models.py`):

```python
class Invoice:
    order = ForeignKey(Order, null=True)  # Keep for backwards compatibility
    fulfillment = ForeignKey(Fulfillment, null=True, related_name="invoices")  # NEW
    purchase_order = OneToOneField(PurchaseOrder, null=True)
    type = CharField(choices=InvoiceType.CHOICES)
    # ... existing fields ...

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(order__isnull=False) |
                    models.Q(fulfillment__isnull=False) |
                    models.Q(purchase_order__isnull=False)
                ),
                name="invoice_must_link_to_something"
            )
        ]
```

**Migration considerations**:
- Add `fulfillment` field (nullable)
- Existing invoices keep `order` link
- New invoices for partial fulfillments use `fulfillment` link

### Backend Changes

**1. Invoice Creation** (`saleor/graphql/invoice/mutations/invoice_create.py`):

Add new mutation for fulfillment invoices:
```python
class InvoiceCreateForFulfillment(BaseMutation):
    """Create invoice for a specific fulfillment (partial order)."""

    class Arguments:
        fulfillment_id = graphene.ID(required=True)
        input = InvoiceCreateInput(required=True)

    @classmethod
    def clean_fulfillment(cls, fulfillment):
        # Validate fulfillment status
        if fulfillment.status not in [
            FulfillmentStatus.FULFILLED,
            FulfillmentStatus.WAITING_FOR_APPROVAL
        ]:
            raise ValidationError("Invalid fulfillment status for invoicing")

        # Check if invoice already exists for this fulfillment
        if hasattr(fulfillment, 'invoices') and fulfillment.invoices.exists():
            raise ValidationError("Invoice already exists for this fulfillment")

        return fulfillment
```

**2. Update existing InvoiceCreate**:
- Keep for backwards compatibility
- Mark as legacy approach (whole-order invoicing)
- Continue to validate order status

**3. Fulfillment model helpers** (`saleor/order/models.py`):

```python
class Fulfillment:
    # ... existing fields ...

    @property
    def has_invoice(self):
        """Check if this fulfillment has an associated invoice."""
        return hasattr(self, 'invoices') and self.invoices.exists()

    @property
    def invoice_is_paid(self):
        """Check if the fulfillment's invoice has been marked as paid."""
        if not self.has_invoice:
            return False
        # Integration point for Xero or manual payment tracking
        # For now, check if invoice has xero_invoice_id or a paid flag
        invoice = self.invoices.first()
        return invoice.metadata.get('paid', False) if invoice else False
```

**4. Order helper for partial fulfillment status**:

```python
class Order:
    # ... existing methods ...

    def get_fulfillment_status_summary(self):
        """Get summary of fulfillment status per line.

        Returns dict mapping order line IDs to fulfillment info:
        {
            line_id: {
                'quantity_ordered': int,
                'quantity_fulfilled': int,
                'quantity_pending_fulfillment': int,
                'has_invoice': bool,
                'invoice_paid': bool
            }
        }
        """
        from collections import defaultdict

        summary = defaultdict(lambda: {
            'quantity_ordered': 0,
            'quantity_fulfilled': 0,
            'quantity_pending_fulfillment': 0,
            'fulfillments': []
        })

        # Populate from order lines
        for line in self.lines.all():
            summary[line.id]['quantity_ordered'] = line.quantity
            summary[line.id]['quantity_fulfilled'] = line.quantity_fulfilled

        # Populate from fulfillments
        for fulfillment in self.fulfillments.all():
            for ff_line in fulfillment.lines.all():
                line_id = ff_line.order_line_id
                summary[line_id]['fulfillments'].append({
                    'fulfillment_id': fulfillment.id,
                    'quantity': ff_line.quantity,
                    'status': fulfillment.status,
                    'has_invoice': fulfillment.has_invoice,
                    'invoice_paid': fulfillment.invoice_is_paid if fulfillment.has_invoice else None
                })

        return dict(summary)
```

### Frontend Changes (GraphQL)

**1. Add Fulfillment invoice fields**:
```graphql
type Fulfillment {
  # ... existing fields ...
  invoices: [Invoice!]!
  hasInvoice: Boolean!
  invoicePaid: Boolean!
}
```

**2. Add Order fulfillment status query**:
```graphql
type OrderLineFulfillmentStatus {
  orderLine: OrderLine!
  quantityOrdered: Int!
  quantityFulfilled: Int!
  quantityPending: Int!
  fulfillments: [FulfillmentInfo!]!
}

type FulfillmentInfo {
  fulfillment: Fulfillment!
  quantity: Int!
  hasInvoice: Boolean!
  invoicePaid: Boolean
}

extend type Order {
  fulfillmentStatusSummary: [OrderLineFulfillmentStatus!]!
}
```

**3. New mutation**:
```graphql
type InvoiceCreateForFulfillment {
  invoice: Invoice
  errors: [InvoiceError!]!
}

input InvoiceCreateForFulfillmentInput {
  fulfillmentId: ID!
  number: String!
  url: String!
  # ... other invoice fields
}
```

---

## 3. Block Pick/Shipment Until Invoice Paid

### Conceptual Requirements

After creating an invoice for a fulfillment:
1. Block Pick from being started if invoice not paid
2. Block Shipment from being created if invoice not paid
3. Allow manual "mark invoice as paid" action (or Xero integration later)

### Database Changes

**Add field to Invoice model** (`saleor/invoice/models.py`):

```python
class Invoice:
    # ... existing fields ...
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_amount = MoneyField(null=True)  # Track actual amount paid (for partial payments)

    @property
    def is_paid(self):
        """Check if invoice has been marked as paid."""
        return self.paid_at is not None
```

**Add metadata tracking**:
- Use existing `metadata` field for Xero integration data
- Store Xero payment status, payment date, etc.

### Backend Changes

**1. Pick Start Validation** (`saleor/graphql/order/mutations/pick_start.py` or similar):

```python
def validate_can_start_pick(pick):
    """Validate that pick can be started."""
    fulfillment = pick.fulfillment

    # Check if fulfillment has invoice that requires payment
    if fulfillment.has_invoice:
        invoice = fulfillment.invoices.first()
        if not invoice.is_paid:
            raise ValidationError(
                "Cannot start pick - fulfillment invoice must be paid first. "
                f"Invoice #{invoice.number} is pending payment.",
                code=OrderErrorCode.INVOICE_NOT_PAID
            )
```

**2. Shipment Creation Validation** (`saleor/graphql/shipping/mutations/outbound_shipment_create.py`):

```python
def validate_fulfillment_for_shipment(fulfillment):
    """Validate that fulfillment can have shipment created."""

    # Existing validations...

    # Check invoice payment status
    if fulfillment.has_invoice:
        invoice = fulfillment.invoices.first()
        if not invoice.is_paid:
            raise ValidationError(
                "Cannot create shipment - fulfillment invoice must be paid first. "
                f"Invoice #{invoice.number} is pending payment.",
                code=ShippingErrorCode.INVOICE_NOT_PAID
            )
```

**3. FulfillmentApprove validation update** (`saleor/graphql/order/mutations/fulfillment_approve.py`):

Already checks `order.is_fully_paid()` unless `site.settings.fulfillment_allow_unpaid=True`.

Modify to also check fulfillment-specific invoice:

```python
@classmethod
def clean_input(cls, info, fulfillment):
    # ... existing validations ...

    # Check fulfillment invoice payment
    if fulfillment.has_invoice:
        invoice = fulfillment.invoices.first()
        if not invoice.is_paid:
            raise ValidationError(
                f"Invoice #{invoice.number} for this fulfillment must be paid before approval.",
                code=OrderErrorCode.INVOICE_NOT_PAID
            )
```

**4. New Invoice Mark as Paid mutation** (`saleor/graphql/invoice/mutations/invoice_mark_as_paid.py`):

```python
class InvoiceMarkAsPaid(BaseMutation):
    """Mark an invoice as paid (manual process or Xero webhook)."""

    invoice = graphene.Field(Invoice)

    class Arguments:
        id = graphene.ID(required=True)
        paid_amount = graphene.Decimal(required=True)
        paid_at = graphene.DateTime()  # Optional, defaults to now

    @classmethod
    def perform_mutation(cls, root, info, **data):
        invoice = cls.get_node_or_error(info, data['id'], only_type=Invoice)

        # Validate amount
        expected_amount = invoice.fulfillment.total_price_gross_amount if invoice.fulfillment else invoice.order.total_gross_amount
        if data['paid_amount'] < expected_amount:
            raise ValidationError("Paid amount is less than invoice total")

        invoice.paid_amount = data['paid_amount']
        invoice.paid_at = data.get('paid_at', timezone.now())
        invoice.save(update_fields=['paid_amount', 'paid_at'])

        # Log event
        events.invoice_paid_event(
            invoice=invoice,
            user=info.context.user,
            app=get_app_promise(info.context).get()
        )

        return InvoiceMarkAsPaid(invoice=invoice)
```

### Frontend Changes (GraphQL)

**1. Add Invoice payment fields**:
```graphql
type Invoice {
  # ... existing fields ...
  paidAt: DateTime
  paidAmount: Money
  isPaid: Boolean!
}
```

**2. New mutation**:
```graphql
type InvoiceMarkAsPaid {
  invoice: Invoice
  errors: [InvoiceError!]!
}

input InvoiceMarkAsPaidInput {
  id: ID!
  paidAmount: Decimal!
  paidAt: DateTime
}
```

**3. Error codes**:
```python
class OrderErrorCode:
    # ... existing codes ...
    INVOICE_NOT_PAID = "invoice_not_paid"

class ShippingErrorCode:
    # ... existing codes ...
    INVOICE_NOT_PAID = "invoice_not_paid"
```

---

## 4. Allow Changing Order Quantities Before Fulfillment

### Current State

**Location**: `saleor/graphql/order/mutations/order_lines_create.py`, `order_line_update.py`, `order_line_delete.py`

Order lines can be edited when:
- Order status in `ORDER_EDITABLE_STATUS` = (`DRAFT`, `UNCONFIRMED`)
- Once order is `UNFULFILLED` or beyond, lines cannot be edited

### Required Changes

**Extend editable window** to allow changes before invoicing/fulfillment.

**Safe conditions for editing**:
- Order line has NOT been invoiced yet
- Order line has NO fulfillments (not even WAITING_FOR_APPROVAL)

### Backend Changes

**1. New validation helper** (`saleor/order/utils.py`):

```python
def order_line_is_editable(order_line):
    """Check if an order line can still be edited.

    Line is editable if:
    - Order is in DRAFT or UNCONFIRMED status, OR
    - Line has no fulfillments and no invoices
    """
    order = order_line.order

    # Always editable in draft/unconfirmed
    if order.status in ORDER_EDITABLE_STATUS:
        return True

    # Check if line has been fulfilled
    if order_line.quantity_fulfilled > 0:
        return False

    # Check if line is in any fulfillment (even WAITING_FOR_APPROVAL)
    if order_line.fulfillment_lines.exists():
        return False

    # Check if fulfillment with this line has invoice
    fulfillments_with_line = Fulfillment.objects.filter(
        lines__order_line=order_line
    )
    for fulfillment in fulfillments_with_line:
        if fulfillment.has_invoice:
            return False

    return True
```

**2. Update OrderLineUpdate mutation** (`saleor/graphql/order/mutations/order_line_update.py`):

```python
@classmethod
def clean_input(cls, info, order_line, data):
    # ... existing validations ...

    # Use new validation
    if not order_line_is_editable(order_line):
        raise ValidationError(
            "Cannot edit order line - it has been fulfilled or invoiced",
            code=OrderErrorCode.NOT_EDITABLE
        )
```

**3. Update OrderLineDelete mutation similarly**

**4. Handle allocation updates**:

When changing quantities on UNFULFILLED orders:
- Deallocate if reducing quantity
- Allocate more if increasing quantity (may fail if insufficient stock)

```python
@classmethod
def perform_mutation(cls, root, info, **data):
    # ... existing code ...

    old_quantity = order_line.quantity
    new_quantity = data.get('quantity', old_quantity)

    if order.status == OrderStatus.UNFULFILLED:
        quantity_diff = new_quantity - old_quantity

        if quantity_diff < 0:
            # Deallocate excess
            deallocate_for_order_line(order_line, abs(quantity_diff))
        elif quantity_diff > 0:
            # Try to allocate more
            try:
                allocate_for_order_line(order_line, quantity_diff, manager)
            except InsufficientStock as e:
                raise ValidationError(f"Cannot increase quantity: {e}")
```

### Frontend Changes (GraphQL)

**1. Add field to OrderLine**:
```graphql
type OrderLine {
  # ... existing fields ...
  isEditable: Boolean!
}
```

**2. Update mutation permissions**:
- `orderLineUpdate` - check `isEditable` before allowing
- `orderLineDelete` - check `isEditable` before allowing

---

## 5. Auto-Fulfillment Only When All Stock Received

### Current Behavior

**Location**: `saleor/inventory/stock_management.py:confirm_purchase_order_item()` (lines 188-254)

Currently:
1. When POI is confirmed, checks if order can auto-confirm
2. Auto-confirms if all allocations have AllocationSources (partial stock OK)
3. Creates fulfillments with WAITING_FOR_APPROVAL status

**Problem**: Auto-confirms even if only some stock received

### Required Behavior

Only auto-confirm and create fulfillments when **ALL** order lines have complete stock.

### Backend Changes

**1. Update can_confirm_order logic** (`saleor/warehouse/management.py`):

Current check in `Order.ready_to_fulfill_with_inventory()` (lines 100-139):
- Returns UNCONFIRMED orders where allocations have matching AllocationSources

Need additional check: all order lines have sufficient stock.

```python
def can_confirm_order(order):
    """Check if order can be auto-confirmed.

    Requirements:
    - Order status is UNCONFIRMED
    - ALL order lines have allocations in owned warehouses
    - ALL allocations have AllocationSources with matching quantities
    - ALL allocations link to POI shipments that have arrived
    """
    if order.status != OrderStatus.UNCONFIRMED:
        return False

    from django.db.models import Sum
    from ..warehouse.models import Allocation

    # Get all order lines
    order_lines = order.lines.all()

    for line in order_lines:
        # Check if line has allocations
        allocations = line.allocations.filter(stock__warehouse__is_owned=True)

        if not allocations.exists():
            return False  # No allocations yet

        # Check total allocated quantity matches line quantity
        total_allocated = sum(a.quantity_allocated for a in allocations)
        if total_allocated < line.quantity:
            return False  # Not fully allocated

        # Check each allocation has AllocationSources
        for allocation in allocations:
            total_sourced = allocation.allocation_sources.aggregate(
                total=Sum('quantity')
            )['total'] or 0

            if total_sourced != allocation.quantity_allocated:
                return False  # Sources don't match allocation

            # Check all sources link to arrived shipments
            for source in allocation.allocation_sources.all():
                poi = source.purchase_order_item
                if not poi.shipment or not poi.shipment.arrived_at:
                    return False  # Shipment not arrived

    return True  # All checks passed
```

**2. Alternative: Use existing property**

The `Fulfillment.has_inventory_received` property (lines 937-976) already does similar checks.

Could refactor to order-level check:

```python
class Order:
    def has_all_inventory_received(self):
        """Check if ALL inventory for this order has been received.

        Returns True only if every order line has allocations with
        complete AllocationSources from arrived shipments.
        """
        for line in self.lines.all():
            allocations = line.allocations.filter(stock__warehouse__is_owned=True)

            if not allocations.exists():
                return False

            total_allocated = sum(a.quantity_allocated for a in allocations)
            if total_allocated < line.quantity:
                return False

            for allocation in allocations:
                total_sourced = allocation.allocation_sources.aggregate(
                    total=Sum('quantity')
                )['total'] or 0

                if total_sourced != allocation.quantity_allocated:
                    return False

                for source in allocation.allocation_sources.all():
                    poi = source.purchase_order_item
                    if not poi.shipment or not poi.shipment.arrived_at:
                        return False

        return True
```

**3. Update confirm_purchase_order_item**:

```python
def confirm_purchase_order_item(purchase_order_item, user=None, app=None):
    # ... existing code (lines 1-186) ...

    # Auto-confirm orders that now have ALL inventory received
    for order in orders_to_check:
        # NEW: Check if ALL inventory received (not just some)
        if order.has_all_inventory_received():
            order.status = OrderStatus.UNFULFILLED
            order.save(update_fields=["status", "updated_at"])

            # Create fulfillments...
            # ... rest of existing code (lines 194-254) ...
```

### Testing Considerations

**Test scenarios**:

1. **Partial stock received**:
   - Order has 2 lines (A: qty 10, B: qty 5)
   - POI for A confirmed (10 units)
   - POI for B not confirmed yet
   - ✅ Order should remain UNCONFIRMED

2. **All stock received**:
   - Order has 2 lines (A: qty 10, B: qty 5)
   - POI for A confirmed (10 units)
   - POI for B confirmed (5 units)
   - ✅ Order should auto-confirm to UNFULFILLED
   - ✅ Fulfillments should be created

3. **Multiple POIs per line**:
   - Order line A: qty 10
   - Split across 2 POIs (POI1: 6 units, POI2: 4 units)
   - POI1 confirmed
   - ✅ Order should remain UNCONFIRMED
   - POI2 confirmed
   - ✅ Order should auto-confirm

---

## Implementation Phases

### Phase 1: Deposit Request (Issue #10)
**Priority**: High
**Complexity**: Low
**Files to modify**:
- `saleor/order/models.py` - add `requires_deposit` field
- `saleor/order/notifications.py` - add deposit request email
- `saleor/order/actions.py` - update `order_created()`
- `saleor/graphql/order/types.py` - expose field
- GraphQL mutations for draft orders
- Email templates

**Testing**:
- Draft order creates with `requires_deposit=True`
- Correct email sent based on flag
- Checkout orders don't request deposit

### Phase 2: Edit Orders Before Fulfillment (Issue #13, partial)
**Priority**: Critical
**Complexity**: Medium
**Files to modify**:
- `saleor/order/utils.py` - add `order_line_is_editable()`
- `saleor/graphql/order/mutations/order_line_update.py`
- `saleor/graphql/order/mutations/order_line_delete.py`
- Allocation management when changing quantities

**Testing**:
- Can edit UNFULFILLED order lines before fulfillment
- Cannot edit lines with fulfillments
- Cannot edit lines with invoices
- Allocations update correctly

### Phase 3: Partial Fulfillment & Invoicing (Issue #12)
**Priority**: High
**Complexity**: High
**Files to modify**:
- `saleor/invoice/models.py` - add `fulfillment` FK
- `saleor/order/models.py` - add fulfillment status helpers
- `saleor/graphql/invoice/mutations/` - new mutation
- `saleor/graphql/order/types.py` - fulfillment status summary
- UI for selecting which lines to fulfill

**Testing**:
- Can create fulfillment for subset of order
- Invoice created for specific fulfillment
- Order can have multiple fulfillments with separate invoices
- Fulfillment status tracked per line

### Phase 4: Block Pick/Shipment Until Paid (Issue #13, partial)
**Priority**: High
**Complexity**: Medium
**Dependencies**: Phase 3
**Files to modify**:
- `saleor/invoice/models.py` - add payment tracking
- Pick start validation
- Shipment creation validation
- `FulfillmentApprove` validation
- New `InvoiceMarkAsPaid` mutation

**Testing**:
- Cannot start pick if invoice unpaid
- Cannot create shipment if invoice unpaid
- Can proceed once invoice marked paid
- Fulfillment approval blocked if invoice unpaid

### Phase 5: Auto-Fulfillment Only When Complete (Issue #12, partial)
**Priority**: Medium
**Complexity**: Medium
**Files to modify**:
- `saleor/order/models.py` - `has_all_inventory_received()`
- `saleor/warehouse/management.py` - `can_confirm_order()`
- `saleor/inventory/stock_management.py` - update auto-confirm logic

**Testing**:
- Order stays UNCONFIRMED with partial stock
- Order auto-confirms only when all stock received
- Works with split POs per line
- Works with multiple lines

### Phase 6: Xero Integration (Future)
**Priority**: Low
**Complexity**: High
**Dependencies**: Phases 3 & 4
**Scope**:
- Webhook from Xero on payment
- Auto-update `invoice.paid_at` based on Xero status
- Sync invoice creation to Xero
- Handle payment reconciliation

---

## Database Migration Strategy

### Order of migrations:

1. **Add `Order.requires_deposit`**
   - Nullable initially
   - Set default based on origin
   - Make non-null

2. **Add `Invoice.fulfillment`**
   - Add FK (nullable)
   - Keep `order` FK for backwards compatibility
   - Add constraint (one of order/fulfillment/purchase_order must be set)

3. **Add `Invoice.paid_at` and `Invoice.paid_amount`**
   - Both nullable
   - Existing invoices remain unpaid unless manually marked

4. **Add indexes**
   - `Invoice.fulfillment_id`
   - `Invoice.paid_at`
   - `Order.requires_deposit`

### Backwards compatibility:

- Keep existing `Invoice.order` FK
- Existing code that creates full-order invoices continues to work
- New code for partial fulfillments uses `Invoice.fulfillment`
- GraphQL returns both for transition period

---

## UI/UX Considerations

### Draft Order Creation
- Checkbox: "Request deposit before order placement" (default checked)
- Show deposit amount (10% of total) dynamically
- Clear messaging about deposit workflow

### Order Fulfillment View
**For each order line, show**:
- Total quantity ordered
- Quantity fulfilled (with breakdown by fulfillment)
- Quantity available to fulfill
- Invoice status per fulfillment
- Payment status per invoice

**Example**:
```
Order #1234
├─ Line: Widget A (Qty: 20)
│  ├─ Fulfillment #1 (Qty: 10) - Invoice #INV-001 ✅ Paid
│  ├─ Fulfillment #2 (Qty: 6)  - Invoice #INV-002 ⏳ Unpaid
│  └─ Unfulfilled: 4 units
└─ Line: Widget B (Qty: 15)
   └─ Unfulfilled: 15 units
```

### Partial Fulfillment Creation
**UI flow**:
1. Select order lines to fulfill (with quantity selectors)
2. System groups by warehouse automatically
3. Confirm fulfillment creation
4. Prompt to create invoice immediately
5. Show payment requirement before pick can start

### Invoice Payment Tracking
- Manual "Mark as Paid" button
- Date picker for payment date
- Amount confirmation
- Notes field for reference number
- Later: Xero integration badge showing sync status

---

## Risk Analysis

### High Risk Areas

**1. Concurrent fulfillment creation**
- **Risk**: Multiple users create overlapping fulfillments
- **Mitigation**: Row-level locking on OrderLine, quantity validation

**2. Partial fulfillment + order editing**
- **Risk**: User edits order while fulfillment in progress
- **Mitigation**: Strict `is_editable` checks, prevent editing fulfilled lines

**3. Invoice-fulfillment relationship**
- **Risk**: Breaking existing invoice functionality
- **Mitigation**: Keep `Invoice.order` FK, dual-path support

**4. Auto-fulfillment timing**
- **Risk**: Race conditions when confirming multiple POIs
- **Mitigation**: Transaction isolation, db-level checks in `can_confirm_order()`

### Medium Risk Areas

**1. Email template complexity**
- **Risk**: Different email types get confusing
- **Mitigation**: Clear naming, template inheritance

**2. Xero integration future-proofing**
- **Risk**: Design doesn't support Xero well
- **Mitigation**: Use metadata field, flexible payment tracking

### Low Risk Areas

**1. Deposit percentage**
- **Risk**: Hardcoded 10% may need to change
- **Mitigation**: Make configurable via site settings later

**2. UI complexity**
- **Risk**: Partial fulfillment UI gets cluttered
- **Mitigation**: Good UX design, progressive disclosure

---

## Open Questions

1. **Deposit percentage**: Hardcode 10% or make configurable?
   - **Recommendation**: Start with hardcoded, add `site.settings.deposit_percentage` later

2. **Partial payments**: Support invoices paid in installments?
   - **Recommendation**: Not in v1, track single `paid_at` date

3. **Invoice numbering**: Auto-generate or manual?
   - **Recommendation**: Keep manual for now (matches current system)

4. **Fulfillment cancellation**: What happens to invoice if fulfillment cancelled?
   - **Recommendation**: Mark invoice as void, require new invoice creation

5. **Mixed fulfillment status**: Can warehouse pick while waiting for invoice?
   - **Recommendation**: No - invoice must be paid before pick starts

6. **Refunds**: How do partial fulfillment refunds work?
   - **Recommendation**: Refund against specific fulfillment invoice

7. **Overpayment**: What if customer pays more than deposit?
   - **Recommendation**: Track as credit, apply to final invoice (Phase 2)

---

## Testing Strategy

### Unit Tests
- `order_line_is_editable()` logic
- `has_all_inventory_received()` calculations
- Invoice payment validation
- Email routing logic

### Integration Tests
- Complete partial fulfillment workflow
- Concurrent POI confirmation
- Invoice creation for fulfillments
- Payment blocking pick/shipment

### E2E Tests
- Draft order with deposit → email sent
- Partial fulfillment → invoice → payment → pick → ship
- Edit order quantities before fulfillment
- Cannot edit after fulfillment started

### Performance Tests
- Large orders with many lines
- Many concurrent fulfillments
- Invoice query performance with new FK

---

## Success Criteria

### Phase 1 (Deposit Request)
✅ Draft orders can request deposits
✅ Correct email sent based on order origin
✅ Deposit amount calculated correctly

### Phase 2 (Order Editing)
✅ Can edit order lines before fulfillment
✅ Cannot edit after fulfillment/invoice created
✅ Allocations update correctly when quantities change

### Phase 3 (Partial Fulfillment)
✅ Can create fulfillment for subset of order
✅ Invoice created per fulfillment
✅ Order shows fulfillment status per line
✅ Multiple fulfillments per order supported

### Phase 4 (Payment Blocking)
✅ Pick cannot start if invoice unpaid
✅ Shipment cannot be created if invoice unpaid
✅ Manual "mark as paid" works
✅ Fulfillment approval blocked until payment

### Phase 5 (Auto-Fulfillment)
✅ Order stays UNCONFIRMED with partial stock
✅ Order auto-confirms when all stock received
✅ Fulfillments created only when complete

### Overall
✅ All existing tests pass
✅ No performance regression
✅ Backwards compatible with existing orders
✅ Documentation updated
