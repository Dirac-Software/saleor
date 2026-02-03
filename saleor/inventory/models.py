from django.conf import settings
from django.db import models
from django_countries.fields import CountryField

from ..core.db.fields import MoneyField
from ..product.models import ProductVariant
from ..warehouse.models import Warehouse
from . import PurchaseOrderItemStatus

"""
A PurchaseOrder is created when we confirm we will order some products from a supplier.
We create the corresponding PurchaseOrderItems at the same time.

The change to the PurchaseOrderItem table may in turn allow us to confirm some orders.
We can immediately update the Stock for that warehouse _before_ the products arrive
(this allows us to confirm and allocate products sooner), allowing us
to collect payment sooner, which is better for cash flow. It increases the chance of us issuing refunds if stock is
missing.

We can look at Stock.quantity and start allocating some stock. The algorithm is simple;
We take the oldest OrderLine for the ProductVariant the Stock refers to and then add an
OrderConsumption entry saying how much stock is confirmed and changing the
Stock.quantity to remove the allocated quantity, and adding to Stock.quantity_allocated
with this amount. If by doing this, some Order
has every OrderLine confirmed, we then can set that order status to UNFULLFILLED. This
confirms the order and takes payment.
If we cancel an order we can simply delete the OrderConfirmation (or perhaps archive it) and the products become available. If we cannot confirm some Order as
we have less stock than anticipated we can simply reduce the quantity on the order (and email them apologising and asking if this is acceptable).

If we are arranging the inbound shipment then we allocate each PurchasingOrderItem to the
correct shipment (almost always PurchaseOrder has 1 shipment). If we are not, we can
just set the shipment costs to be 0 (will this work with duties?).

When the goods are received the shipment is marked as received and the quantity received
is updated. At this point we can potentially fulfill some Orders. We look at all
UNFULFILLED and PARTIALLY_FULFILLED orders and see what orders are ready for pick and
pack. This work is TODO (marking when received and when fulfilled)


When the final invoice arrives we add an invoice with a fk from Invoice ->
PurchaseOrder with a Xero invoice id and ensure the sum of the unit costs
add up to what we expect from the PurchaseOrderItem. We can then see when and how much
of the invoice we have paid.

Currently we don't track VAT (it is all reclaimed anyway). This means the cash flow
isn't fully accurate.

TODO: add a celery task to check the Stock is as expected every evening.
"""


class PurchaseOrder(models.Model):
    """Products come into this world through a PurchaseOrder.

    An invoice from a supplier we have received for some products.

    The Invoice stores the one to one field to the deal and as such this doesn't
    really store that much information.

    If we have a final invoice (or any invoice from Xero) then we can judge the Deal
    to be somewhat finalised.

    The shipment this deal comes on is on the Unit. This is an easier way of doing
    the many-to-many relationship between Shipment <-> Deal as it already has the
    constraint that a Unit exists once.
    """

    # TODO: add a check constraint that the warehouse is_owned=False.

    # this must be a non-owned warehouse. A non-owned warehouse allows us to see the supplier. It
    # allows us to correctly reduce the Stock in a non-owned warehouse to prevent
    # double counting (the stock moves to an owned warehouse)

    # we can't null this because stock must come from somewhere, and we expect that
    # the ProductVariants already exist for the units, which means that the variants
    # should all exist in a non-owned warehouse _before_ a deal is ingested.
    source_warehouse = models.ForeignKey(
        Warehouse, on_delete=models.DO_NOTHING, related_name="source_purchase_orders"
    )

    # this has to be an owned warehouse, it is where the goods end up.
    destination_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.DO_NOTHING,
        related_name="destination_purchase_orders",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class PurchaseOrderItem(models.Model):
    """A variant + quantity on a PurchaseOrder. Like the invoice line item.

    This is not unique on the PurchaseOrder, ProductVariant which appears odd.
    We want to make an escape hatch for these 2 cases:
    1. A single purchase order item is in 2 different shipments
    2. A single purchase order item is for a different country of origin.

    """

    # lets keep this NOT unique on variant,order to account for country of origin
    # changes + different shipments
    order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    product_variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name="items"
    )
    quantity_ordered = models.PositiveIntegerField()  # Renamed from 'quantity'
    # for this to be > 0 then we must have the shipment received_at be not null.
    quantity_received = models.PositiveIntegerField()
    # Tracks how much of this batch has been allocated to customer orders
    # via AllocationSource
    quantity_allocated = models.PositiveIntegerField(default=0)

    @property
    def available_quantity(self):
        """Amount available for allocation (not yet allocated).

        Uses quantity_received if RECEIVED, otherwise quantity_ordered.
        """
        from . import PurchaseOrderItemStatus

        base = (
            self.quantity_received
            if self.status == PurchaseOrderItemStatus.RECEIVED
            else self.quantity_ordered
        )
        return max(0, base - self.quantity_allocated)

    unit_price_amount = models.DecimalField(
        max_digits=settings.DEFAULT_MAX_DIGITS,
        decimal_places=settings.DEFAULT_DECIMAL_PLACES,
    )
    unit_price = MoneyField(amount_field="unit_price_amount", currency_field="currency")

    currency = models.CharField(
        max_length=settings.DEFAULT_CURRENCY_CODE_LENGTH,
    )

    shipment = models.ForeignKey(
        "shipping.Shipment",
        on_delete=models.DO_NOTHING,
        related_name="purchase_order_items",
        null=True,
        blank=True,
    )

    country_of_origin = CountryField()

    status = models.CharField(
        max_length=32,
        choices=PurchaseOrderItemStatus.CHOICES,
        default=PurchaseOrderItemStatus.DRAFT,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When status changed to CONFIRMED (ordered from supplier)",
    )
