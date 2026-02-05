from django.conf import settings
from django.db import models
from django_countries.fields import CountryField
from prices import Money

from ..product.models import ProductVariant
from ..warehouse.models import Warehouse
from . import PurchaseOrderItemAdjustmentReason, PurchaseOrderItemStatus, ReceiptStatus

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
    quantity_ordered = models.PositiveIntegerField()
    # Tracks how much of this batch has been allocated to customer orders
    # via AllocationSource
    quantity_allocated = models.PositiveIntegerField(default=0)

    @property
    def available_quantity(self):
        """Amount available for allocation.

        Calculates: quantity_ordered + processed_adjustments - quantity_allocated

        Only processed adjustments (processed_at is set) are included in the calculation.
        This allows adjustments to be created but not applied until explicitly processed.
        """
        from django.db.models import Sum

        processed_adjustments = (
            self.adjustments.filter(processed_at__isnull=False).aggregate(
                total=Sum("quantity_change")
            )["total"]
            or 0
        )

        base = self.quantity_ordered + processed_adjustments
        return max(0, base - self.quantity_allocated)

    @property
    def quantity_received(self):
        """Total quantity received across all receipt lines.

        Sums the quantity_received from all ReceiptLine records
        associated with this purchase order item.
        """
        from django.db.models import Sum

        total = self.receipt_lines.aggregate(total=Sum("quantity_received"))["total"]
        return total or 0

    @property
    def unit_price_amount(self):
        """Unit price we actually pay (after invoice adjustments).

        Calculates unit price from total_price_amount adjusted for processed
        adjustments that affect what we owe the supplier.
        """
        # Start with base total from invoice
        total = self.total_price_amount

        # Adjust for processed adjustments that affect what we pay supplier
        # (invoice variance, delivery short)
        adjustment_value = sum(
            (adj.quantity_change * (self.total_price_amount / self.quantity_ordered))
            for adj in self.adjustments.filter(
                affects_payable=True, processed_at__isnull=False
            )
        )
        adjusted_total = total + adjustment_value

        # Calculate unit price from adjusted total
        received_qty = self.quantity_ordered + sum(
            adj.quantity_change
            for adj in self.adjustments.filter(
                affects_payable=True, processed_at__isnull=False
            )
        )

        if received_qty > 0:
            return adjusted_total / received_qty
        return (
            self.total_price_amount / self.quantity_ordered
            if self.quantity_ordered > 0
            else 0
        )

    @property
    def unit_price(self):
        """Unit price as Money object."""
        return Money(self.unit_price_amount, self.currency)

    total_price_amount = models.DecimalField(
        max_digits=settings.DEFAULT_MAX_DIGITS,
        decimal_places=settings.DEFAULT_DECIMAL_PLACES,
        help_text="Total invoice amount for this POI (quantity × unit price)",
    )

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


class PurchaseOrderItemAdjustment(models.Model):
    """Audit trail for inventory adjustments (leakage).

    We create this whenever we identify a change in what we expected from
    quantity_ordered compared to what we currently have now.

    Because we need to account for where all products come from for accounting, we track
    these here.

    On creation we need to call increase_stock or decrease_stock - need to change these
    so that for an owned warehouse we will cancel / change orders.

    Processing these is painful because we may have confirmed some orders!
    The possible outcomes:
    1. the good: we can handle the shortage by simply removing from unused quantity (unlikely as
    we are dropshippers and
    we dont tend to buy unpromised stock)
    2. the bad: we have to unconfirm some orders that have not been paid for due to
    shortage.
    3. the ugly: we have to refund some orders that have been paid due to the shortage

    """

    purchase_order_item = models.ForeignKey(
        PurchaseOrderItem,
        on_delete=models.CASCADE,
        related_name="adjustments",
        help_text="Which POI batch this adjustment applies to",
    )

    quantity_change = models.IntegerField(
        help_text="Change in quantity (negative for losses, positive for gains)"
    )

    reason = models.CharField(
        max_length=32,
        choices=PurchaseOrderItemAdjustmentReason.CHOICES,
        help_text="Why this adjustment was made",
    )

    affects_payable = models.BooleanField(
        default=False,
        help_text=(
            "True if supplier credits us for this adjustment "
            "(invoice variance, delivery short). "
            "False if we eat the loss (shrinkage, damage)."
        ),
    )

    notes = models.TextField(
        blank=True, help_text="Additional details about the adjustment"
    )

    # if we have handled the change in stock. For the cases where orders are unpaid this
    # is easy. It is much harder when they are not!
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this adjustment was processed (stock updated, allocations adjusted)",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who recorded this adjustment",
    )

    @property
    def financial_impact(self):
        """Calculate the financial impact of this adjustment.

        Returns the cost impact in the POI's currency.
        Negative for losses, positive for gains.
        """
        return self.quantity_change * self.purchase_order_item.unit_price_amount

    @property
    def gl_account_type(self):
        """Determine which GL account type this adjustment affects.

        Returns:
            'accounts_payable': Supplier credits us (reduces what we owe)
            'operating_expense': We eat the loss (shrinkage, damage, etc.)

        """
        if self.affects_payable:
            return "accounts_payable"
        return "operating_expense"

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["purchase_order_item", "-created_at"]),
            models.Index(fields=["reason", "-created_at"]),
            models.Index(fields=["processed_at"]),
            models.Index(fields=["affects_payable", "-created_at"]),
        ]

    def __str__(self):
        direction = "loss" if self.quantity_change < 0 else "gain"
        return (
            f"POI #{self.purchase_order_item.id}: "
            f"{abs(self.quantity_change)} unit {direction} ({self.reason})"
        )


class Receipt(models.Model):
    """Document for receiving inbound shipments from suppliers.

    Tracks the physical receiving process for a shipment. When warehouse staff
    receive goods, they create a Receipt and add ReceiptLines as items are scanned.

    Workflow:
    1. Create Receipt for a Shipment (status=IN_PROGRESS)
    2. Add ReceiptLines as items are scanned/counted
    3. Complete Receipt → creates adjustments for discrepancies,
       sets Shipment.arrived_at, updates POI status to RECEIVED
    """

    shipment = models.OneToOneField(
        "shipping.Shipment",
        on_delete=models.CASCADE,
        related_name="receipt",
        help_text="Inbound shipment being received",
    )

    status = models.CharField(
        max_length=32,
        choices=ReceiptStatus.CHOICES,
        default=ReceiptStatus.IN_PROGRESS,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When receiving was completed and processed",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipts_created",
        help_text="Warehouse staff who started receiving",
    )

    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="receipts_completed",
        help_text="Warehouse staff who completed receiving",
    )

    notes = models.TextField(
        blank=True, help_text="Additional notes about this receipt"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["shipment", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self):
        return f"Receipt #{self.id} for Shipment #{self.shipment_id}"


class ReceiptLine(models.Model):
    """Individual line item in a goods receipt.

    Tracks what was actually received for each PurchaseOrderItem.
    Multiple ReceiptLines can exist for the same POI if items are
    scanned in separate batches during receiving.
    """

    receipt = models.ForeignKey(
        Receipt,
        on_delete=models.CASCADE,
        related_name="lines",
    )

    purchase_order_item = models.ForeignKey(
        PurchaseOrderItem,
        on_delete=models.CASCADE,
        related_name="receipt_lines",
        help_text="Which POI this line receives against",
    )

    quantity_received = models.PositiveIntegerField(
        help_text="Quantity physically received in this receipt line"
    )

    received_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this specific item/batch was scanned",
    )

    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Warehouse staff who scanned this item",
    )

    notes = models.TextField(
        blank=True, help_text="Notes about this specific line (damage, etc.)"
    )

    class Meta:
        ordering = ["received_at"]
        indexes = [
            models.Index(fields=["receipt", "purchase_order_item"]),
            models.Index(fields=["received_at"]),
        ]

    def __str__(self):
        return (
            f"ReceiptLine: {self.quantity_received}x "
            f"{self.purchase_order_item.product_variant.sku}"
        )
