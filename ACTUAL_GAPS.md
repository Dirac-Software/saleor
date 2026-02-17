# Actual Gaps - Updated Analysis

After reviewing the dashboard codebase, here's what's **actually** missing vs. what **already exists**.

---

## ✅ Already Implemented (Frontend + Backend)

### **1. Order Deposit Management** ✅
- **Backend:** `orderSetDepositRequired(id, required, percentage)` ✅
- **Frontend:** `OrderDepositSettingsCard` ✅
- Shows deposit required toggle, percentage input, total paid, threshold status

### **2. Xero Payment Linking** ✅
- **Backend:** `orderSyncXeroPayment(orderId, xeroPaymentId, isDeposit)` ✅
- **Backend:** `availableXeroPayments(orderId)` query ✅
- **Frontend:** `OrderXeroPaymentDialog` ✅
- Fetches available payments from Xero, allows selection, marks as deposit

### **3. Pick Workflow** ✅
- **Backend:** `pickStart`, `pickUpdateItem`, `pickComplete` ✅
- **Frontend:** `PickDetail` page with full UI ✅
- Start pick, update quantities, complete pick

### **4. Shipment Linking** ✅
- **Backend:** `fulfillmentLinkToShipment(fulfillmentId, shipmentId)` ✅
- **Frontend:** `LinkShipmentDialog` ✅
- Query available shipments, link to fulfillment

### **5. Fulfillment Requirements** ✅
- **Backend:** Auto-approval logic ✅
- **Frontend:** `FulfillmentRequirementsCard` ✅
- Shows visual checklist of pick/shipment/proforma/deposit

---

## 🔴 Actually Missing

### **1. Proforma Invoice "Mark as Paid" UI** ⚠️ **HIGHEST PRIORITY**

**Backend:** ✅ `orderFulfillmentMarkProformaPaid(id, paidAt)` exists

**Frontend:** ❌ **Missing UI component**

The `FulfillmentRequirementsCard` shows whether proforma is paid, but there's **no button to mark it as paid**.

**What's needed:**
- Proforma Invoice Card (similar to OrderDepositSettingsCard)
- Display proforma invoice details
- "Mark as Paid" button that calls the mutation
- Show paid timestamp when complete

**Where:** Fulfillment Detail Page

---

### **2. Remove Old Invoice Generation Button** ⚠️ **CLEANUP**

**What's needed:**
- Remove `invoiceRequestMutation` from `src/orders/mutations.ts`
- Remove `orderInvoiceRequest` from `OrderOperations.tsx`
- Remove "Generate Invoice" button from order details
- Update GraphQL fragments to include `type`, `xeroInvoiceId`, `fulfillment`

**Where:**
- `src/orders/mutations.ts:483-500`
- `src/orders/containers/OrderOperations.tsx`
- `src/orders/views/OrderDetails/OrderNormalDetails/index.tsx:281-285`
- `src/fragments/orders.ts`

---

### **3. Display Invoices Per Fulfillment** ⚠️ **IMPORTANT**

**What's needed:**
- Move invoice display from order level to fulfillment level
- Show invoice type badges (PROFORMA, FINAL)
- Show deposit allocation per fulfillment
- Link to proforma invoice PDF

**Where:** Fulfillment Detail Page

Current invoice list is at order level. Need to show:
```
Fulfillment #1
├─ Proforma Invoice (auto-generated)
│  ├─ Amount: $350 (after $150 deposit credit)
│  └─ [Mark as Paid] or ✅ Paid
└─ Final Invoice #001 (from Xero)
   └─ [View in Xero]
```

---

### **4. Multiple Partial Fulfillments UI** ⚠️ **NICE TO HAVE**

**What exists:**
- Can create fulfillments with partial quantities
- Backend handles multiple fulfillments per order

**What's missing:**
- Visual breakdown of which items are in which fulfillment
- Show remaining unfulfilled items
- Better UI for creating partial fulfillments

**Where:** Order Details Page

Current UI shows all fulfillments, but could be improved to show:
```
Order Items:
• Blue Widget: 5 ordered
  - Fulfillment #1: 3 shipped ✅
  - Fulfillment #2: 2 waiting for approval
• Red Gadget: 3 ordered
  - Fulfillment #2: 3 waiting for approval
```

---

## 📊 Priority Summary

| Component | Backend | Frontend | Status | Priority |
|-----------|---------|----------|--------|----------|
| Deposit Settings | ✅ | ✅ | DONE | - |
| Xero Payments | ✅ | ✅ | DONE | - |
| Pick Workflow | ✅ | ✅ | DONE | - |
| Shipment Linking | ✅ | ✅ | DONE | - |
| Requirements Card | ✅ | ✅ | DONE | - |
| **Proforma Payment Button** | ✅ | ❌ | **BLOCKED** | **🔴 CRITICAL** |
| **Remove Old Invoice UI** | N/A | ❌ | **CLEANUP** | **🟡 HIGH** |
| **Fulfillment Invoices** | ✅ | ❌ | **MISSING** | **🟡 HIGH** |
| Multiple Fulfillments UI | ✅ | Partial | WORKS | 🟢 LOW |

---

## 🎯 Minimal Implementation (This Week)

To have a **fully working workflow**, you only need:

### **Task 1: Proforma Invoice Card** (2-3 hours)
Create `src/fulfillment/components/ProformaInvoiceCard.tsx`:
```typescript
interface ProformaInvoiceCardProps {
  fulfillment: FulfillmentFragment;
  onMarkPaid: () => void;
}

// Display:
// - Invoice number
// - Created date
// - Deposit allocated
// - [Mark as Paid] button (if not paid)
// - Paid timestamp (if paid)
```

Add mutation hook:
```graphql
mutation OrderFulfillmentMarkProformaPaid($id: ID!, $paidAt: DateTime) {
  orderFulfillmentMarkProformaPaid(id: $id, paidAt: $paidAt) {
    fulfillment {
      id
      proformaInvoicePaid
      proformaInvoicePaidAt
      status
    }
    errors { field message }
  }
}
```

Add to `FulfillmentDetail` page next to `FulfillmentRequirementsCard`.

---

### **Task 2: Remove Old Invoice Mutations** (1 hour)
1. Delete `invoiceRequestMutation` from `mutations.ts`
2. Remove `orderInvoiceRequest` from `OrderOperations.tsx`
3. Remove `onInvoiceGenerate` prop from `OrderInvoiceList`
4. Remove "Generate" button from order invoice section

---

### **Task 3: Update Invoice Display** (2-3 hours)
1. Update `Invoice` fragment to include `type`, `xeroInvoiceId`, `fulfillment`
2. Update `OrderInvoiceList` to show invoice type badges
3. Show invoices grouped by fulfillment (optional)
4. Show deposit allocation (optional)

---

## ✅ Total Implementation Time

- **Critical Path:** 3-4 hours (Proforma button)
- **Cleanup:** 1 hour (Remove old mutations)
- **Nice-to-have:** 2-3 hours (Better invoice display)

**Total: 6-8 hours** to have fully working deposit → pick → ship → proforma → approval workflow!

---

## 🚀 Ready to Start?

Since you already have 90% of the UI built, you're **very close** to done. The main blocker is just the proforma payment button.

Want me to help you implement the `ProformaInvoiceCard` component now?
