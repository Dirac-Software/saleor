# Frontend Changes Required for Fulfillment-Level Invoices

## Overview

Invoices have moved from **Order-level** to **Fulfillment-level**. The frontend needs to be updated to reflect this architectural change.

---

## 🔴 Breaking Changes

### **1. Remove Order-Level Invoice Generation**

**File:** `src/orders/mutations.ts:483-500`

**OLD - Remove this mutation:**
```typescript
export const invoiceRequestMutation = gql`
  mutation InvoiceRequest($orderId: ID!) {
    invoiceRequest(orderId: $orderId) {
      errors {
        ...InvoiceError
      }
      invoice {
        ...Invoice
      }
      order {
        id
        invoices {
          ...Invoice
        }
      }
    }
  }
`;
```

**Why:** The backend mutation `invoiceRequest(orderId)` no longer exists. Invoices are now auto-generated when fulfillments are approved.

---

### **2. Remove "Generate Invoice" Button from Order Page**

**File:** `src/orders/views/OrderDetails/OrderNormalDetails/index.tsx:281-285`

**OLD - Remove this:**
```typescript
onInvoiceGenerate={() =>
  orderInvoiceRequest.mutate({
    orderId: id,
  })
}
```

**NEW - Remove the button entirely:**
- Invoices are now **automatically generated** when fulfillments are approved
- No manual generation needed at order level

---

## ✅ Required UI Changes

### **1. Move Invoice Display to Fulfillment Level**

**Current Behavior:**
- Invoices shown in order details page
- One invoice list for entire order

**New Behavior:**
- Show invoices **per fulfillment** in the fulfillment card/section
- Each fulfillment can have multiple invoices (proforma + final)

---

### **2. Display Invoice Types**

**Update Invoice Display to Show Type:**

```typescript
// Add to OrderInvoiceList component or create FulfillmentInvoiceList

<TableCell>
  {invoice.type === "PROFORMA" ? (
    <Chip label="Proforma" color="info" />
  ) : invoice.type === "FINAL" ? (
    <Chip label="Final" color="success" />
  ) : (
    <Chip label="Standard" color="default" />
  )}
</TableCell>
```

**Why:** Users need to distinguish between:
- **Proforma invoices** (customer-facing, for payment before shipment)
- **Final invoices** (Xero invoices, after shipment)

---

### **3. Update GraphQL Fragments**

**File:** `src/fragments/orders.ts`

**Add invoice type and fulfillment link:**
```graphql
fragment Invoice on Invoice {
  id
  number
  createdAt
  url
  status
  type            # NEW: PROFORMA or FINAL
  xeroInvoiceId   # NEW: Link to Xero invoice
  fulfillment {   # NEW: Link to specific fulfillment
    id
    fulfillmentOrder
  }
}
```

**Update Order fragment to include fulfillment invoices:**
```graphql
fragment OrderDetails on Order {
  # ...existing fields
  fulfillments {
    id
    fulfillmentOrder
    status
    proformaInvoice {    # NEW: Proforma invoice link
      ...Invoice
    }
    depositAllocatedAmount  # NEW: Deposit applied to this fulfillment
  }
}
```

---

### **4. Update Fulfillment Details Component**

**Create or update:** `src/fulfillment/components/FulfillmentInvoices.tsx`

```typescript
interface FulfillmentInvoicesProps {
  fulfillment: FulfillmentFragment;
  onInvoiceClick: (invoiceId: string) => void;
  onInvoiceSend: (invoiceId: string) => void;
}

const FulfillmentInvoices = ({ fulfillment, onInvoiceClick, onInvoiceSend }: FulfillmentInvoicesProps) => {
  const proformaInvoice = fulfillment.proformaInvoice;
  const finalInvoices = fulfillment.order.invoices.filter(
    inv => inv.fulfillment?.id === fulfillment.id && inv.type === "FINAL"
  );

  return (
    <DashboardCard>
      <DashboardCard.Header>
        <DashboardCard.Title>
          Fulfillment Invoices
        </DashboardCard.Title>
      </DashboardCard.Header>
      <DashboardCard.Content>
        {proformaInvoice && (
          <InvoiceRow
            invoice={proformaInvoice}
            type="PROFORMA"
            depositAllocated={fulfillment.depositAllocatedAmount}
            onClick={onInvoiceClick}
            onSend={onInvoiceSend}
          />
        )}
        {finalInvoices.map(invoice => (
          <InvoiceRow
            key={invoice.id}
            invoice={invoice}
            type="FINAL"
            onClick={onInvoiceClick}
            onSend={onInvoiceSend}
          />
        ))}
      </DashboardCard.Content>
    </DashboardCard>
  );
};
```

---

### **5. Show Deposit Allocation**

**Add to fulfillment display:**

```typescript
{fulfillment.depositAllocatedAmount > 0 && (
  <InfoRow label="Deposit Allocated">
    <Money money={{
      amount: fulfillment.depositAllocatedAmount,
      currency: order.currency
    }} />
  </InfoRow>
)}
```

**Why:** Users need to see how much deposit was applied to each fulfillment.

---

## 🔄 Migration Strategy

### **Phase 1: Backward Compatible (Temporary)**

Keep order-level invoice list but add warning:

```typescript
<OrderInvoiceList invoices={order.invoices}>
  {order.invoices.some(inv => inv.fulfillment) && (
    <Alert severity="info">
      Some invoices are now linked to specific fulfillments.
      Check each fulfillment for its invoices.
    </Alert>
  )}
</OrderInvoiceList>
```

### **Phase 2: New UI**

1. Remove order-level "Generate" button
2. Add invoice sections to each fulfillment card
3. Show invoice type badges (Proforma/Final)

### **Phase 3: Cleanup**

1. Remove `invoiceRequestMutation` from `mutations.ts`
2. Remove `orderInvoiceRequest` from `OrderOperations.tsx`
3. Remove `InvoiceRequestMutation` types from generated types
4. Remove old order-level invoice list (optional, or keep for read-only view)

---

## 📋 Files to Update

### **Must Update:**

1. ✅ `src/orders/mutations.ts` - Remove `invoiceRequestMutation`
2. ✅ `src/orders/containers/OrderOperations.tsx` - Remove `orderInvoiceRequest`
3. ✅ `src/orders/views/OrderDetails/OrderNormalDetails/index.tsx` - Remove `onInvoiceGenerate`
4. ✅ `src/fragments/orders.ts` - Add `type`, `xeroInvoiceId`, `fulfillment` to Invoice fragment
5. ✅ `src/fulfillment/components/` - Add fulfillment invoice display

### **Optional Updates:**

6. `src/orders/components/OrderInvoiceList/` - Update to show invoice types
7. `src/orders/components/OrderDetailsPage/` - Add info alert about new behavior
8. `src/graphql/` - Regenerate types after fragment updates

---

## 🧪 Testing Checklist

- [ ] Order without fulfillments shows no invoices
- [ ] Each fulfillment shows its proforma invoice (if generated)
- [ ] Final invoices appear after fulfillment is shipped
- [ ] Invoice type badges display correctly (Proforma/Final)
- [ ] Deposit allocation displays correctly per fulfillment
- [ ] Clicking invoice still opens it in new tab
- [ ] Send invoice button still works
- [ ] No errors in console related to invoice mutations

---

## 📊 Visual Changes

### **Before:**
```
Order #123
├─ Invoices
│  ├─ [Generate Button]
│  └─ Invoice #001
└─ Fulfillments
   ├─ Fulfillment #1
   └─ Fulfillment #2
```

### **After:**
```
Order #123
├─ Fulfillments
│  ├─ Fulfillment #1
│  │  └─ Invoices
│  │     ├─ [Proforma] Invoice (auto-generated)
│  │     │   Deposit allocated: $150
│  │     └─ [Final] Invoice #001 (from Xero)
│  └─ Fulfillment #2
│     └─ Invoices
│        ├─ [Proforma] Invoice (auto-generated)
│        │   Deposit allocated: $100
│        └─ [Final] Invoice #002 (from Xero)
```

---

## 🔗 Backend Webhook Integration

**Important:** The frontend doesn't generate proforma invoices anymore. The backend sends a webhook `FULFILLMENT_PROFORMA_INVOICE_GENERATED` to your integration service, which:

1. Generates the PDF
2. Sends it to the customer
3. (Optionally) Creates a draft in Xero

The dashboard just displays invoices that already exist in the database.

---

## ❓ FAQ

**Q: Can users still manually generate invoices?**
A: No. Proforma invoices are auto-generated when fulfillments are approved. Final invoices are created by the Xero integration service.

**Q: What if an order has no fulfillments yet?**
A: No invoices will be shown (correct behavior - you can't invoice unfulfilled orders).

**Q: Can we show all invoices at order level too?**
A: Yes, optionally keep a read-only invoice list at order level for convenience, but remove the "Generate" button.

**Q: What about old orders with order-level invoices?**
A: They'll still display, but won't have a `fulfillment` link. Handle this in the UI with conditional rendering.

---

## 🚀 Quick Start

```bash
# 1. Update fragments
cd src/fragments
# Edit orders.ts to add new invoice fields

# 2. Regenerate GraphQL types
npm run generate-types

# 3. Remove old mutation
# Delete invoiceRequestMutation from src/orders/mutations.ts

# 4. Update UI
# Remove onInvoiceGenerate from OrderNormalDetails
# Add invoice display to fulfillment components

# 5. Test
npm run test
```
