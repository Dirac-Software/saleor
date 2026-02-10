from collections import defaultdict

from ...inventory.models import PurchaseOrder
from ..core.dataloaders import DataLoader


class PurchaseOrderByIdLoader(DataLoader[int, PurchaseOrder]):
    context_key = "purchase_order_by_id"

    def batch_load(self, keys):
        purchase_orders = PurchaseOrder.objects.using(
            self.database_connection_name
        ).in_bulk(keys)
        return [purchase_orders.get(purchase_order_id) for purchase_order_id in keys]
