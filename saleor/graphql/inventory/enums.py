from typing import Final

import graphene

from ...graphql.core.enums import to_enum
from ...inventory.models import PurchaseOrderState
from ..core.doc_category import DOC_CATEGORY_PRODUCTS

PurchaseOrderStateEnum: Final[graphene.Enum] = to_enum(
    PurchaseOrderState, type_name="PurchaseOrderStateEnum"
)
PurchaseOrderStateEnum.doc_category = DOC_CATEGORY_PRODUCTS
