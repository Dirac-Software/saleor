
# Purchasing products
## Price Lists
we _necessarily_ receive a catalogue of products. Typically these products tell us explicitly which products and variants exist.
For sportswear, variants are exclusively different sizes of product.

## Sales models
1. Presale model: we split and process this price list and send to customers.
2. Standard model: we receive these goods, then start selling them.

The presale model is more complex, because we have more risk - if products are not as we expect or delays occur this _matters_.
The standard model is much worse for cash flow.

We expect to work as often as possible, but not exclusively, off the presale model.

## Order gathering
Before we even purchase stock from our supplier, we _may_ have some orders.

## Purchasing
We make a purchase because:
1. stock is worth taking a risk on
2. we have already sold stock to customers

Does a purchase order have a channel? Should it? One day we want 'channel' to mean trading group, so knowing which trading group owns what products may be useful for cash flow stuff.

Purchases are made based off price lists. We may end up wanting to collate multiple price lists from a single warehouse into a PO.
We typically will be purchasing off an order.

We can precompute after selecting a warehouse.

## MOQ Buckets
MOQ buckets are a useful feature for telling when we have hit some MOQ. We create one from some price lists and a value. We then can track for some PO whether we have hit our target. This is a small feature - something on the home screen showing each MOQ bucket.

## The draft PO
We could create a draft PO on creation of a price list.
We can use draft POs to do _so much_. On each order from a non-owned warehouse we could offer the user to add the order to a draft purchase order.
We can select a price list on some PO - this limits stock not _just_ to a warehouse but also to a price list - we can then be sure we are making a PO for some order.

We probably want to _loosely_ tie an order to a PO. We don't want an all out allocation as this is recreating logic and is excessively complex I think. But if we don't tie down the connection, people may mistakenly add some orders to several POs. We also probably only want to allow _one_ PO per warehouse. This should be a soft requirement.

We should be able to 'add' orders to a draft PO. In this case we maximally fulfill from the price list. There should be shortcut to take an entire line also.

## The confirmed PO
The confirmed PO should lose all connections to price lists - it is just stock at that point. When we receive a proforma + stock list from a supplier, we _may_ want to adjust at this point.

## Receipt
Receipt is completed. Maybe add an import / MCP for this.
We can use the `AllocationSource` table and rejig sizes in order to maximally fulfill orders. This is a weird problem and some FIFO logic is necessary - we need to think about balancing sizes also. The key here is that we only need to worry about `AllocationSource` here - we can make atomic swaps to confirmed orders for their sizes. There _may_ need to be some confirmation step here.

## The UI
MCP is the future here. We do not want to invest in a solution that only works for a GUI.
