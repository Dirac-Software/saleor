
# Intro
This doc assumes that the data model is completed. It lays out the features and applications required and priority to get them done.

# Ramble
We track payments by recording the Xero invoice we need to listen to. This is a huge simplification and ensures Xero is a source of truth.

We try and keep disagreggation of costs explicit and wherever it is a little vague we try to keep it at runtime (eg. for shipments when attributing costs by weight).

# Features
## Deal sheet ingestion
*** low priority ***
The ingestion of products into non-owned warehouses has been completed but we need to consider / ensure.
- Cannot ingest products to an Owned warehouse
- How will we account for multiple different sell prices in the ingest
- Require HS Code and UK tax code.

## Manual Order Creation
*** low priority ***
Sometimes the sell price will be very different to what has been given on the sheet. How do we make that less of a pain.


## Shipping Estimate Calculation
*** low priority ***
- Shipping calculator gives accurate estimates of shipping cost
- All products sell_prices given without shipping
- On export of price list shipping cost can be optionally calculated and added to the sell price per unit.
- Requires HTS code and Duties. Mostly manual work, how do we do this in a calculator reliably?

## Stock table is client to Unit model for an Owned Warehouse
- The Stock record for an owned warehouse has quantity updated via trigger to be equal to the sum of unconsumed Units for some Variant. Need to consider non_owned stock arriving at Dirac - need to allocate immediately to orders right.
- Any GraphQL query cannot allow mutation of Stock from an owned warehouse.

## Deal Invoice Ingestion
Given some Deal Invoice in Xero, get it (use the API) then  process it into the correct Variant and create the Units.

## Accepting stock at an Owned Warehouse
We expect a deal invoice must be ingested before the stock arrives.

## Order Invoice Creation
When an Order is confirmed an invoice should be generated using an app which connects to Xero.

## Order confirmation blocked until Units come.
We cannot confirm an order unless we can generate OrderLine.qty * OrderLineUnitfor all orders.
I _think_ this should happen automatically via first come first serve - if we can fulfill an order we should do it. We could have the problem of someone forgetting to add an order and then going 'hold on I need that stock for these guys' but that requires that  whoever ordered the stock didn't add their order in time for that invoice and didn't inform the person ordering the stock from the supplier that is was needed.


## Unit Allocation
Whenever an order changes or the unit model changes we need to recalculate allocations going first come first serve. If we have unconsumed units we can assign them to orders.

## Unit arrival
I don't _think_ that an inbound shipment needs to exist before the product arrives, or an outbound shipment for a product to be delivered.
- When products arrive at warehouse Units (which must exist) have `arrived_at` populated
- All orders which can be fulfilled automatically are fulfilled (the units are already consumed note).


## Shipment Invoice Ingestion
When we receive a shipping invoice we need to update the costs to a specific shipment. Can we allocate shipping cost per unit at runtime or is it too hard to get the duty and tariff from the hs code correct. Talk to Paul. Interface before automation.

## Fulfilled Orders for Shipment View created
- Prevent stock fk on FulfillmentLine going to a non-owned warehouse.
- All orders where all goods are in the warehouse.
- Block on shipping until payment accepted. (the Xero InvoiceId already exists so we just need to reconcile) - probably a Celery task.

## Auto-Reconcile Stripe payments to Xero invoices
*** low priority - we have no products on the web***
- Hopefully some functionality exists through Stripe for this...

## Record deliveries
*** low priority ***
I don't know if this can be automated. Talk to Paul.

## Record leakage
*** low priority ***
- If a unit is missing or is damaged on delivery to Dirac record it in the Unit table, if it happens to a customer also.

## Handle returns, expiry and cancellations
*** low priority ***
- If an Order is returned or partially_returned what happens

## Accounting Dashboard Interface
*** low priority ***
- Expost GraphQL queries that will allow users to see all required accounting figures

# Method
Write tests. use GraphQL. Use Django QuerySets. Don't rush to get it done. Follow codebase patterns.


# Roadmap
This is tricky as most of these tasks are dependent on each other. It makes sense to start at the first things that happen to a product, but we need the interfaces to all of the data model to be built first. Doing the frontend for everything last should make this not too slow. Working out dependencies for the backend and trying to separate this to modular tasks that can be completed sequentially is hard.

I expect for this task the best method is to get a first iteration out on everything with tested logic, then to get Claude going on frontend, then implement bug fixes.
