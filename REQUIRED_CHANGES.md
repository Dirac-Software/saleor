
# Requirements I didn't realise
1. There exist 2 types of invoice: PROFORMA or FINAL. We need to extend Saleor to handle both. PROFORMA invoices CANNOT be added to Xero.

2. We _must_ track the VAT we pay on a unit by unit basis. We reclaim UK, EU where we are VAT registered, EU where we are not, rest of world differently.

3. For received invoices, Dext handles categorising invoices. We upload an invoice to Dext then manually check the categorisation.

4. We recognise revenue when goods are collected, delivered

5. We sometimes are invoiced for duties on a shipment in a seaprate invoice, and sometimes the duties are aggregated over several shipments and are very hard to disaggregate.

6. Sometimes goods may be collected from our warehouse.

7. Go over VAT cases with James when writing.

8. Need country of origin for goods, can request EITHER at point of ingest OR at point of purchase, but this may affect sell price + shipping.

# Changes
- We can use the Proforma _or_ an order slip to add units. We can't rely on the invoice as much as I hoped, because it is subject to change (not final) (1).

- Store buy_price, buy_vat on the Unit. Until a Deal has a confirmed _final_ invoice attached these can change. (1,2)

- We will probably need extra steps when adding shipping invoices + deal invoices that make the process less ergonomic.

- IF we can map Dext invoice id -> Xero invoice ID we can make the process of adding invoices much more ergonomic (this is a future automation).

- When we receive the _final_ invoice on a deal, we need to do some logic checking the sum of the costs on the Unit are what we expect. This is because we will in all likelihood use a _proforma_ invoice to populate them and things could change.


# Knowledge Required
## Shipping
- Freight forwarding, who invoices us.
- Cost is (shipping,duties). Any others?
- How do we disaggregate duties and check they're correct?
What am I missing (need confirmation from Paul)


## Statement for Paul to check
We have Shipping cost + Duty
Shipping Cost is charged by the shipment provider (normally fedex) and several shipments appear in a single invoice. HOWEVER, one shipment is never split across invoices.
Duties _may_ occur in a different invoice, sometimes from a shipment provider and sometimes from a freight forwarder and show duties charged by HS code. We sometimes have to break down a Duties invoice using what shipments we expect to attribute the cost per shipment per HS code.

