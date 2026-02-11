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

## Shipment
An order or a purchase order both require shipments. We need a shipment to receive a purchase order. We need a shipment to fulfill an order. A shipment is a tracking number and a cost.

## Order
When an Order is created it is in an `UNCONFIRMED` state (we can make a `DRAFT` before hand), which means stock has been allocated to that order. If all stock is in an owned warehouse we can immediately confirm it, the state is then `UNFULFILLED`. Once we move to state `UNFULFILLED` we generate a `Fulfillment` with status `WAITING_FOR_APPROVAL`. This means it ready for pick and pack. Once it has been pick and packed, we can arrange a shipment and move to `FULFILLED`, which means the Shipment exists for some `Order`.


## Allocation
An `Allocation` is reserving stock to be used for some order. The second we move some order into the `UNCONFIRMED` state we allocate stock. If we allocate to an owned `Warehouse` then we need to track which allocation links to which `PurchaseOrderItem`, we do this with an `AllocationSource`. `POI.quantity_allocated` tracks how many units we have assigned to an order from some purchase order item. For a nonowned warehouse an `Allocation` doesn't need `AllocationSource`, but we cannot confirm an order if all the `Allocation`s don't have `AllocationSource`s (which is the same as saying all stock is in an owned warehouse).

## Fulfillment
When we have confirmed an `Order`, that means all products are either in a shipment for an owned warehouse or they are in an owned warehouse. At this point we create a `Fullfillment` for each owned warehouse in the `Order`, with the `WAITING_FOR_APPROVAL` state. In order to actually fulfill, we need:
1. A `Shipment` to exist.
2. A `Pick` to have been completed
We have a queue of `Order`s needing shipments and a queue for `Order`s needing `Pick`s. We check at each completion whether we can move status to `FULFLLED`.

Once the shipment is picked up, we mark the `departed_at`.


## Receipt
Some Shipment that has some `PurchaseOrderItem` arrives ( we can use the tracking number to find when it happens). We start a `Receipt` which checks in the stock. If we are short we create a `PurchaseOrderItemAdjustment` which accounts for a difference in `quantity_ordered` and the quantity we have available.

## Pick
When we have all of our products for an order in some owned warehouse (for now ignore partial fulfillments as the exception) we need to pick and pack. A `Pick` is generated in the ready state when we create a `Fulfillment`. The warehouse team then can start a `Pick` and mark as completed. In the future we might want the relationship between pick + pack and a `Fulfillment` to be different - because `Fullfillment` has an fk to a `Pick` we can change the way we handle pick + pack in future if necessaty.



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
