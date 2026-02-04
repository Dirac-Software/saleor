from typing import Final

import graphene

from ...graphql.core.enums import to_enum
from ...inventory import PurchaseOrderItemStatus
from ..core.doc_category import DOC_CATEGORY_PRODUCTS

PurchaseOrderItemStatusEnum: Final[graphene.Enum] = to_enum(
    PurchaseOrderItemStatus, type_name="PurchaseOrderItemStatusEnum"
)
PurchaseOrderItemStatusEnum.doc_category = DOC_CATEGORY_PRODUCTS
