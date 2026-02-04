import graphene

from .mutations import PurchaseOrderConfirm, PurchaseOrderCreate


class InventoryMutations(graphene.ObjectType):
    create_purchase_order = PurchaseOrderCreate.Field()
    confirm_purchase_order = PurchaseOrderConfirm.Field()
