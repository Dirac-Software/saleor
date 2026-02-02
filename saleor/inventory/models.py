from django.db import models

from ..order.models import OrderLine
from ..product.models import ProductVariant
from ..shipping.models import Shipment
from ..warehouse.models import Warehouse
from ..core.db.fields import MoneyField

"""
# Order Context

We introduce a Unit - which is a physical good. It comes into existence from an invoice
from a supplier, which necessarily correlates to Stock in a non-owned
Warehouse. Stock records for a non-owned warheouse are estimates, and as such can only
be confirmed when a  Unit exists for the Variant.

Non-Owned Warehouses:
- Units must exist before an Order Line can be fulfilled. This means for each OrderLine
  OrderLine.quantity OrderLineUnit records must exist.


saleor.warehouse.models.Stock has warehouse, quantity and quantity_allocated fields.

Stock can be available, reserved, allocated, consumed. The meaning of this for the
saleor.warehouse.models.Stock mode is:

Reserved: temporary hold stock in a basket before checkout. We don't use this fature. A
record is inserted in saleor.warehouse.models.Reservation and when displaying quantities
on the web we would decrease the shown available quantity by this amount.

Allocated: Stock has been assigned to an order in the UNCONFIRMED, UNFULFILLED
or it is in the unfulfilled portion of a PARTIALLY_FULFILLED order. quantity_allocated for the Stock record is increased accordingly with the ProductVariant, Warehouse, order
quantity. Stock from an owned warehouse will never stay in this state as we will just
automatically allocate the Units and create a OrderLineUnit entry and fulfill at least
some of the order.

Consumed: An order is UNFILFILLED, PARTIALLY_FULFILLED or FULFILLED and so we decrease the quantity and quantity_allocated
on the Stock table so that the stock cannot be consumed by anything else. We create an
OrderLineUnit for the stock, which means that we have a Unit which will be allocated to
that Order.

Fulfilled: An order where the products are at our warehouse. CHECK this.

TODO: Orders being Fulfilled has little meaning at the moment.

# Payment Context
_charge here means buy_price, sell_price, tariff_cost,shipping_cost etc._
By linking an inventory / accountancy object (Deal,Shipment,Order) to a Xero InvoiceId
we can access the real-time payment information for that object.
payment status.
This mirrors the real world accountancy requirements: We reconcile invoices to payments in Xero and all invoices
MUST exist in  Xero.

# Charge Context
How do we take some invoice and allocate the correct charge to each unit? Let us run
through each type of Charge a Unit is associated with.

## Orders
We manually add orders to Saleor, and then use a plugin via Xero to generate an order
invoice.
This means we know the correct prices on the OrderItems because _we_ generated the
invoice in this codebase! We can correctly attribute the charge among the Units in an
order as we own the generation.

## Deals
We receive an invoice and add it (maybe via Dext) to Xero. We need to add the units to be
tracked in Unit model, making them available for consumption. We should design this to work via Saleor. We need units entered
in the Unit model as soon as invoices
are received, so when the shipment arrives we can account for which products arrived.
Ideally we can automate this so that Xero fires a webhook, we recognise it as a deal and
auto allocate Units, but best to do this manually for now.

## Shipments
We receive an invoice and need to allocate it to a set of shipments that already exist.
We need to allocate the correct costs to each shipment. This is in all likelihood a
manual task.

# Shipping Context
When the bill for a shipment comes in, we firstly allocate the duties and tariffs to each individual
item using its HS code.
Then we take the weight of each item and allocate shipment cost by weight.


# System Design Thoughts
- If people need to do things in 2 places they won't
- If we don't require country_of_origin, hs_code at ingest then they won't get done (but
  this slows us down to getting a sale)

"""


class Deal(models.Model):
    """Products come into this world through a deal, which is an invoice from a supplier we
    have received for some products.

    The Invoice stores the one to one field to the deal and as such this doesn't
    really store that much information.

    If we have a final invoice (or any invoice from Xero) then we can judge the Deal
    to be somewhat finalised.

    The shipment this deal comes on is on the Unit. This is an easier way of doing
    the many-to-many relationship between Shipment <-> Deal as it already has the
    constraint that a Unit exists once.
    """

    # TODO: add a check constraint that the warehouse is_owned=False.

    # this must be a non-owned warehouse. This is the same thing as the supplier. It
    # allows us to correctly reduce the Stock in a non-owned warehouse to prevent
    # double counting (the stock moves to an owned warehouse)

    # we can't null this because stock must come from somewhere, and we expect that
    # the ProductVariants already exist for the units, which means that the variants
    # should all exist in a non-owned warehouse _before_ a deal is ingested.
    warehouse = models.ForeignKey(Warehouse, on_delete=models.DO_NOTHING)


class Unit(models.Model):
    """the inbound shipment cost is on the Shipment.
    the outbound shipment cost is on the Shipment.
    the sell cost is on the order line item, the sell invoice on the order.
    the buy cost is on the deal item??, the buy invoice on the deal.

    the tariff cost and duty cost i am as of yet unsure on where to place.
    """

    product_variant = models.ForeignKey(
        ProductVariant, null=False, on_delete=models.CASCADE, related_name="units"
    )
    # TODO: add a check constraint that the warehouse is_owned=True.

    # Null as it can
    # be in transit. DO_NOTHING as we can NEVER rewrite the history of a Unit. We
    # shouldn't be deleting them
    warehouse = models.ForeignKey(Warehouse, null=True, on_delete=models.DO_NOTHING)

    # deal indicates the supplier they came from. It can't be null as otherwise how
    # do we see buy price.
    deal = models.ForeignKey(Deal, on_delete=models.DO_NOTHING)

    # we get these from the deal invoice at point of ingestion into our system. They are
    # corresponsing to the deal. If the deal is not finalised (invoice is not final)
    # then these are subject to change
    # the mapping of Invoice -> Unit is currently manual, but we don't use the
    # intermediate step of InvoiceUnit - this is because it just recreates the Unit
    # table and we don't have a standardised method of breaking down invoices.
    buy_price = MoneyField(amount_field="buy_price_amount", currency_field="currency")
    # TODO: this could be stored on the deal right? How do we measure if it has been
    # reclaimed. Can we assume it is always reclaimed?
    buy_price_vat = MoneyField(
        amount_field="buy_price_vat_amount", currency_field="currency"
    )

    # we keep the shipment a unit is in here. This is because both orders and deals
    # have a many to many relationship with shipments, but a unit may be in one
    # inbound shipment and one outbound shipment, so it's actually easier to just
    # record which units refer to which shipment.
    outbound_shipment = models.ForeignKey(
        Shipment, null=True, on_delete=models.DO_NOTHING, related_name="outbound_units"
    )
    # consider the edge case where the products arrive but this is zero.
    inbound_shipment = models.ForeignKey(
        Shipment, null=True, on_delete=models.DO_NOTHING, related_name="inbound_units"
    )

    # the order may be None if it is not consumed yet (available as floor stock).
    # If this is null then we can display in the Stock table.
    # should have OrderLine.qty = sum(Unit.order_line==OrderLine)
    order_line = models.ForeignKey(OrderLine, null=True, on_delete=models.DO_NOTHING)

    # TODO:fullfillment - when pick and packed, map to the order fulfillment.
