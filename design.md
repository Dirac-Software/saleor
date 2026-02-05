# Models
## Warehouse Ownership
All inventory comes from somewhere. ERPs tend to represent the initial locations of products as just one type Warehouses.

At Dirac, we want to actually track and list products that are in the Warehouses. We introduce ownership. If a `Warehouse` is owned  (`saleor.warehouse.models.Warehouse.is_owned=True`) then the `Stock` in that `Warehouse` is exact. If a `Warehouse` is not owned (`saleor.warehouse.models.Warehouse.is_owned=False`) then the `Stock` is an upper bound on true available quantity.

When we buy some line from a supplier, this is a movement of `Stock` from a `Warehouse` that is not owned to a `Warehouse` that is owned. This can only happen when a corresponding `PurchaseOrder` is confirmed (more on this later).


## Purchase Order
Acquiring goods from some supplier has this sequential process:
1. A purchase order is drafted with the required Stock.
2. A purchase order is confirmed with the supplier. Some order slip of proforma invoice is issued.
3. `>=1` shipment(s) is created to move the goods to an owned warehouse
4. A shipment arrives, the part of the purchase order in the shipment is received. Stock is moved from the nonowned warehouse to the owned warehouse

This means that Products are in the Warehouse table _before_ they actually arrive. Once they are inbound they are considered part of that warehouse, but unreceived..

## Order
When an Order is created we check that we have enough stock in any warehouse to fulfill it. If we do not we error and don't let the order be created (given we only show available quantities on the web, this shouldn't happen). We can confirm the `Order` if we have enough stock in owned warehouses. If all of that stock has been received, we can pick + pack and fulfill the `Order`.


## Allocation
We allocate products prioritising owned warehouses. Both allocation strategies (`PRIORITIZE_HIGH_STOCK` and `PRIORITIZE_SORTING_ORDER`) enforce owned-warehouse-first ordering to ensure:
1. Customer allocations use confirmed inventory in owned warehouses
2. Batch tracking via `AllocationSources` links allocations to specific `PurchaseOrderItems`
3. `POI.quantity_allocated` accurately tracks how much of each batch is allocated

After exhausting owned warehouse stock, we then allocate from non-owned warehouses. We can allocate products in an owned warehouse before the Shipment containing the Purchase Order Item has been received. This is good for cash flow, we can send out invoices and take payment sooner, but it is **bad** if we end up receiving less than we expected. In this case, we will often have to issue a refund if the payment status.



# Warehouse Tasks
- When a shipment arrives the goods need to be checked in
- When an order is in the warehouse it needs to be picked and packed.

## Receipt
Some Shipment that has some `PurchaseOrderItem` arrives ( we can use the tracking number to find when it happens). Something calls graphql mutation `startReceiptCheckin`. We then have a graphQL mutation `checkin(product_variant, quantity)` ( note we error if the POI is not part of that shipment, also this allows us to add barcode scanning later). Then we have `finishReceiptCheckin` which will generate POIA for missing ones, set shipment received_at and change the POI status.


### Leakage
## Invoice Variance
Stock changes between Proforma (purchase order) and Final invoices for a purchase order

## Delivery Short / Receipt Shortage
Stock is missing from a delivery
The suppliers fault.

## Shrinkage
Stock missing from a warehouse


## Inbound Shipment Reception
Typically we scan in barcodes or similar
