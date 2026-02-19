# Fulfillment Workflow - Gap Analysis

## Overview

Analysis of what's implemented vs. what's still needed for the complete deposit + partial fulfillment + proforma invoice workflow.

---

## ✅ Backend: Already Implemented

### **Order-Level Deposit Management**
- ✅ `orderSetDepositRequired(id, required, percentage)` - Set deposit requirement
- ✅ `orderSyncXeroPayment(orderId, xeroPaymentId, isDeposit)` - Link Xero payment
- ✅ `availableXeroPayments(orderId)` - Query available payments from Xero
- ✅ Order fields: `depositRequired`, `depositPercentage`, `totalDepositPaid`, `depositThresholdMet`

### **Pick Workflow**
- ✅ `pickStart(id)` - Start picking
- ✅ `pickUpdateItem(pickId, lineId, quantityPicked)` - Update picked quantities
- ✅ `pickComplete(id)` - Mark pick as complete
- ✅ Pick model with status tracking (NOT_STARTED, IN_PROGRESS, COMPLETED)

### **Shipment Workflow**
- ✅ `shipmentCreate(...)` - Create shipment
- ✅ `fulfillmentLinkToShipment(fulfillmentId, shipmentId)` - Link fulfillment to shipment
- ✅ `shipmentMarkDeparted(id)` - Mark as departed

### **Proforma Invoice Workflow**
- ✅ Auto-generation on fulfillment approval
- ✅ `orderFulfillmentMarkProformaPaid(id, paidAt)` - Mark proforma as paid
- ✅ Webhook: `FULFILLMENT_PROFORMA_INVOICE_GENERATED`
- ✅ Deposit allocation calculation (FIFO)

### **Auto-Approval Logic**
- ✅ Automatic status change to FULFILLED when all requirements met
- ✅ Requirements checking: pick complete + shipment linked + proforma paid + deposit allocated

### **Final Invoice**
- ✅ `invoiceCreateFinal(fulfillmentId, xeroInvoiceId, invoiceNumber)` - Link Xero final invoice

---

## 🔴 Frontend: Major Gaps

### **1. Order Deposit Management UI** ⚠️ HIGH PRIORITY

**Missing:**
- [ ] UI to set `depositRequired` on an order
- [ ] UI to set `depositPercentage` (e.g., "50% deposit required")
- [ ] Display of deposit status on order page
- [ ] Display of `totalDepositPaid` vs required
- [ ] Display of `depositThresholdMet` status

**Where:** Order Details Page

**Example UI Needed:**
```
┌─────────────────────────────────────────┐
│ Deposit Requirements                    │
├─────────────────────────────────────────┤
│ Deposit Required: [Yes ▼] [No]         │
│ Deposit Percentage: [50%]               │
│                                          │
│ Status:                                  │
│ Required: $500.00 (50%)                 │
│ Paid: $500.00          ✅ Threshold Met │
│                                          │
│ [Add Xero Payment]                      │
└─────────────────────────────────────────┘
```

**Mutations Needed:**
```graphql
mutation OrderSetDepositRequired($id: ID!, $required: Boolean!, $percentage: Decimal) {
  orderSetDepositRequired(id: $id, required: $required, percentage: $percentage) {
    order {
      id
      depositRequired
      depositPercentage
      totalDepositPaid
      depositThresholdMet
    }
    errors { field message }
  }
}
```

---

### **2. Xero Payment Linking UI** ⚠️ HIGH PRIORITY

**Missing:**
- [ ] Dialog to select Xero payment for deposit
- [ ] Query to fetch `availableXeroPayments(orderId)`
- [ ] UI to display payment amount/date from Xero
- [ ] Button to sync payment to order

**Where:** Order Details Page (Payments section)

**Example UI Needed:**
```
┌─────────────────────────────────────────┐
│ Link Deposit Payment                    │
├─────────────────────────────────────────┤
│ Select payment from Xero:               │
│                                          │
│ ○ Payment #12345                        │
│   Amount: $500.00                       │
│   Date: 2026-02-15                      │
│   Status: Completed                     │
│                                          │
│ ○ Payment #12346                        │
│   Amount: $250.00                       │
│   Date: 2026-02-14                      │
│   Status: Completed                     │
│                                          │
│ [Cancel] [Link Payment]                 │
└─────────────────────────────────────────┘
```

**Query + Mutation Needed:**
```graphql
query AvailableXeroPayments($orderId: ID!) {
  availableXeroPayments(orderId: $orderId) {
    payments {
      xeroPaymentId
      amount
      date
      status
    }
  }
}

mutation OrderSyncXeroPayment($orderId: ID!, $xeroPaymentId: String!, $isDeposit: Boolean!) {
  orderSyncXeroPayment(orderId: $orderId, xeroPaymentId: $xeroPaymentId, isDeposit: $isDeposit) {
    order {
      id
      totalDepositPaid
      depositThresholdMet
    }
    errors { field message }
  }
}
```

---

### **3. Pick Workflow UI** ⚠️ HIGH PRIORITY

**Missing:**
- [ ] Pick detail page
- [ ] UI to start pick (`pickStart`)
- [ ] UI to update picked quantities per line
- [ ] UI to complete pick (`pickComplete`)
- [ ] Display pick status in fulfillment details

**Where:** New Page: `/orders/:orderId/fulfillments/:fulfillmentId/pick`

**Example UI Needed:**
```
┌─────────────────────────────────────────────────────────┐
│ Pick #1 - Fulfillment #1             [Status: IN_PROGRESS]│
├─────────────────────────────────────────────────────────┤
│ Started: 2026-02-16 10:00 by John Doe                  │
│                                                         │
│ Items to Pick:                                          │
│ ┌─────────────────────────────────────────────────────┐│
│ │ Product         │ Ordered │ Picked │ Location       ││
│ │─────────────────┼─────────┼────────┼────────────────││
│ │ Blue Widget     │ 5       │ [5▼]   │ Aisle A-12     ││
│ │ Red Gadget      │ 3       │ [3▼]   │ Aisle B-05     ││
│ └─────────────────────────────────────────────────────┘│
│                                                         │
│ [Save Progress]  [Complete Pick]                       │
└─────────────────────────────────────────────────────────┘
```

**Mutations Needed:**
```graphql
mutation PickStart($id: ID!) {
  pickStart(id: $id) {
    pick { id status startedAt }
    errors { field message }
  }
}

mutation PickUpdateItem($pickId: ID!, $lineId: ID!, $quantityPicked: Int!) {
  pickUpdateItem(pickId: $pickId, lineId: $lineId, quantityPicked: $quantityPicked) {
    pick { id items { lineId quantityPicked } }
    errors { field message }
  }
}

mutation PickComplete($id: ID!) {
  pickComplete(id: $id) {
    pick { id status completedAt }
    errors { field message }
  }
}
```

---

### **4. Shipment Linking UI** ⚠️ MEDIUM PRIORITY

**Missing:**
- [ ] UI to link fulfillment to existing shipment
- [ ] Shipment selector dropdown
- [ ] Display linked shipment info in fulfillment

**Where:** Fulfillment Details Page

**Example UI Needed:**
```
┌─────────────────────────────────────────┐
│ Shipment                                │
├─────────────────────────────────────────┤
│ Linked Shipment: Shipment #42          │
│ Tracking: 1Z999AA10123456784            │
│ Carrier: UPS                            │
│                                          │
│ [Change Shipment]                       │
└─────────────────────────────────────────┘

OR (if not linked yet):

┌─────────────────────────────────────────┐
│ Shipment                                │
├─────────────────────────────────────────┤
│ Not linked to shipment yet              │
│                                          │
│ [Link to Shipment]                      │
└─────────────────────────────────────────┘
```

**Mutation Needed:**
```graphql
mutation FulfillmentLinkToShipment($fulfillmentId: ID!, $shipmentId: ID!) {
  fulfillmentLinkToShipment(fulfillmentId: $fulfillmentId, shipmentId: $shipmentId) {
    fulfillment {
      id
      shipment {
        id
        trackingNumber
        carrier
      }
    }
    errors { field message }
  }
}
```

---

### **5. Fulfillment Requirements Card** ⚠️ HIGH PRIORITY

**Missing:**
- [ ] Visual checklist of approval requirements
- [ ] Real-time status updates
- [ ] Links to complete each requirement

**Where:** Fulfillment Details Page

*(Already covered in previous response - see FulfillmentRequirementsCard)*

---

### **6. Proforma Invoice Card** ⚠️ HIGH PRIORITY

**Missing:**
- [ ] Proforma invoice display
- [ ] "Mark as Paid" button
- [ ] Deposit allocation display

**Where:** Fulfillment Details Page

*(Already covered in previous response - see ProformaInvoiceCard)*

---

### **7. Multiple Fulfillments Management** ⚠️ MEDIUM PRIORITY

**Missing:**
- [ ] UI to create partial fulfillments (select which lines to fulfill)
- [ ] Display multiple fulfillments per order
- [ ] Show which items are in which fulfillment
- [ ] Show remaining unfulfilled items

**Where:** Order Details Page

**Example UI Needed:**
```
┌─────────────────────────────────────────────────────────┐
│ Fulfillments (2 of 3 items fulfilled)                   │
├─────────────────────────────────────────────────────────┤
│ ┌─────────────────────────────────────────────────────┐│
│ │ Fulfillment #1 - FULFILLED                          ││
│ │ Items: Blue Widget x3                               ││
│ │ Shipped: 2026-02-15                                 ││
│ │ [View Details]                                      ││
│ └─────────────────────────────────────────────────────┘│
│                                                         │
│ ┌─────────────────────────────────────────────────────┐│
│ │ Fulfillment #2 - WAITING_FOR_APPROVAL               ││
│ │ Items: Red Gadget x2                                ││
│ │ Requirements: 2/4 Complete                          ││
│ │ [View Details]                                      ││
│ └─────────────────────────────────────────────────────┘│
│                                                         │
│ Unfulfilled Items:                                      │
│ • Blue Widget x2 (5 ordered, 3 fulfilled)              │
│                                                         │
│ [Create Fulfillment]                                    │
└─────────────────────────────────────────────────────────┘
```

---

### **8. Deposit Allocation Display** ⚠️ MEDIUM PRIORITY

**Missing:**
- [ ] Show how deposit is split across fulfillments
- [ ] Display remaining deposit available
- [ ] Visual breakdown of deposit usage

**Where:** Order Details Page

**Example UI Needed:**
```
┌─────────────────────────────────────────┐
│ Deposit Allocation                      │
├─────────────────────────────────────────┤
│ Total Deposit Paid: $500.00             │
│                                          │
│ Allocated:                               │
│ • Fulfillment #1: $300.00               │
│ • Fulfillment #2: $200.00               │
│                                          │
│ Remaining: $0.00                        │
└─────────────────────────────────────────┘
```

---

## 📋 Priority Implementation Order

### **Phase 1: Critical Path** (Block fulfillment approval)
1. ✅ Fulfillment Requirements Card
2. ✅ Proforma Invoice Card with "Mark as Paid" button
3. ⚠️ Order Deposit Management UI (set deposit required/percentage)
4. ⚠️ Xero Payment Linking UI (link deposit payment)

### **Phase 2: Warehouse Operations** (Block pick/ship)
5. ⚠️ Pick Workflow UI (start, update, complete)
6. ⚠️ Shipment Linking UI

### **Phase 3: User Experience** (Nice to have)
7. ⚠️ Multiple Fulfillments Management
8. ⚠️ Deposit Allocation Display
9. ✅ Remove old invoice generation UI (cleanup)

---

## 🧪 Missing Tests

### **Backend:**
- ✅ Most backend logic is tested

### **Frontend:**
- [ ] E2E test: Complete deposit + fulfillment workflow
- [ ] E2E test: Partial fulfillment with deposit allocation
- [ ] E2E test: Auto-approval when all requirements met
- [ ] Unit test: Requirement status calculation
- [ ] Unit test: Deposit allocation display

---

## 📝 Documentation Gaps

### **Missing Docs:**
- [ ] User guide: How to set up deposit requirements
- [ ] User guide: Warehouse pick workflow
- [ ] User guide: Link Xero payments to orders
- [ ] Developer guide: Fulfillment state machine
- [ ] API docs: New mutations and fields

---

## 🔗 Integration Service Gaps

### **Webhook Handlers:**
- ✅ `FULFILLMENT_PROFORMA_INVOICE_GENERATED` - Generate PDF and email
- ⚠️ `FULFILLMENT_APPROVED` or `FULFILLMENT_FULFILLED` - Create final Xero invoice
- ⚠️ Error handling and retry logic
- ⚠️ Webhook signature verification
- ⚠️ Logging and monitoring

---

## 🎯 Summary

### **Critical Gaps (Block Core Workflow):**
1. **Order Deposit UI** - Can't set deposit requirements
2. **Xero Payment UI** - Can't link deposit payments
3. **Fulfillment Requirements UI** - Can't see approval blockers
4. **Proforma Payment UI** - Can't mark proforma as paid

### **Important Gaps (Block Warehouse):**
5. **Pick Workflow UI** - Can't complete picks
6. **Shipment Linking UI** - Can't link shipments

### **Nice-to-Have Gaps:**
7. Multiple fulfillments management
8. Deposit allocation visualization

---

## 🚀 Recommended Next Steps

1. **Week 1:** Implement Order Deposit Management + Xero Payment UI
2. **Week 2:** Implement Fulfillment Requirements + Proforma Payment UI
3. **Week 3:** Implement Pick Workflow UI
4. **Week 4:** Implement Shipment Linking + Polish

This will give you a **minimum viable workflow** by week 2, with full warehouse integration by week 4.
