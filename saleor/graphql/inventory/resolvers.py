"""Resolvers for inventory queries."""

from ...inventory import models
from ..core.context import get_database_connection_name


def resolve_purchase_orders(info):
    """Resolve list of purchase orders."""
    database_connection_name = get_database_connection_name(info.context)
    return models.PurchaseOrder.objects.using(database_connection_name).all()


def resolve_purchase_order(info, id):
    """Resolve a single purchase order by ID."""
    database_connection_name = get_database_connection_name(info.context)
    return (
        models.PurchaseOrder.objects.using(database_connection_name)
        .filter(id=id)
        .first()
    )


def resolve_receipts(info):
    """Resolve list of receipts."""
    database_connection_name = get_database_connection_name(info.context)
    return models.Receipt.objects.using(database_connection_name).all()


def resolve_receipt(info, id):
    """Resolve a single receipt by ID."""
    database_connection_name = get_database_connection_name(info.context)
    return models.Receipt.objects.using(database_connection_name).filter(id=id).first()
