from django.conf import settings
from django.db.models import Sum

from .models import Allocation


def get_received_quantity_for_order_line(
    order_line, warehouse_id=None, database_connection_name=None
):
    if database_connection_name is None:
        database_connection_name = settings.DATABASE_CONNECTION_DEFAULT_NAME

    allocations = Allocation.objects.using(database_connection_name).filter(
        order_line=order_line
    )
    if warehouse_id:
        allocations = allocations.filter(stock__warehouse_id=warehouse_id)

    total_received = 0
    for allocation in allocations:
        sources = allocation.allocation_sources.using(
            database_connection_name
        ).annotate(
            poi_received=Sum("purchase_order_item__receipt_lines__quantity_received")
        )
        total_received += sum(s.poi_received or 0 for s in sources)

    return total_received


def get_fulfillable_quantity_for_order_line(
    order_line, warehouse_id=None, database_connection_name=None
):
    if database_connection_name is None:
        database_connection_name = settings.DATABASE_CONNECTION_DEFAULT_NAME

    quantity_ordered = order_line.quantity
    quantity_received = get_received_quantity_for_order_line(
        order_line, warehouse_id, database_connection_name
    )
    quantity_fulfilled = sum(
        fl.quantity
        for fl in order_line.fulfillment_lines.using(database_connection_name).all()
    )
    return max(0, min(quantity_received, quantity_ordered) - quantity_fulfilled)
