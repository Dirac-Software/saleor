
# Requirements I didn't realise
1. There exist 2 types of invoice: PROFORMA or FINAL. We need to extend Saleor to handle both. PROFORMA invoices CANNOT be added to Xero.

2. We _must_ track the VAT we pay on a unit by unit basis. We reclaim UK, EU where we are VAT registered, EU where we are not, rest of world differently.

3. For received invoices, Dext handles categorising invoices. We upload an invoice to Dext then manually check the categorisation.

4. We recognise revenue when goods are collected, delivered

5. We sometimes are invoiced for duties on a shipment in a seaprate invoice, and sometimes the duties are aggregated over several shipments and are very hard to disaggregate.

6. Sometimes goods may be collected

# Changes
- We can use the Proforma _or_ an order slip to add units. We can't rely on the invoice as much as I hoped (1).

- Store buy_price, buy_vat on the Unit. Until a Deal has a confirmed _final_ invoice attached these can change. (1,2)

- We will probably need extra steps when adding shipping invoices + deal invoices that make the process less ergonomic.

- IF we can map Dext invoice id -> Xero invoice ID we can make the process of adding invoices much more ergonomic (this is a future automation).

- When we receive the _final_ invoice on a deal, we need to do some logic checking the sum of the costs on the Unit are what we expect. This is because we will in all likelihood use a _proforma_ invoice to populate them and things could change.


# Knowledge Required
## Shipping
- Freight forwarding
- Cost is (shipping,duties). Any others?
- How do we disaggregate duties and check they're correct?
What am I missing (need confirmation from Paul)
