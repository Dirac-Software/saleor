"""PriceList delete mutation."""

from typing import cast

import graphene
from django.core.exceptions import ValidationError

from .....permission.enums import ProductPermissions
from .....product import PriceListStatus
from .....product.error_codes import ProductErrorCode
from .....product.models import PriceList
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import ProductError


class PriceListDelete(BaseMutation):
    price_list_id = graphene.ID(description="ID of the deleted price list.")

    class Arguments:
        id = graphene.ID(required=True, description="ID of the price list to delete.")

    class Meta:
        description = (
            "Delete a price list. "
            "Only permitted when status is INACTIVE or processing never completed."
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        price_list = cast(
            PriceList,
            cls.get_node_or_error(info, data["id"], field="id", only_type="PriceList"),
        )
        if price_list.status == PriceListStatus.ACTIVE:
            raise ValidationError(
                {
                    "id": ValidationError(
                        "Cannot delete an active price list. Deactivate it first.",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )
        pk = price_list.pk
        price_list.delete()
        return PriceListDelete(price_list_id=pk)
