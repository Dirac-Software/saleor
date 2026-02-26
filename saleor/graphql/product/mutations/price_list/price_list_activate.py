"""PriceList activate mutation."""

from typing import cast

import graphene
from django.core.exceptions import ValidationError

from .....permission.enums import ProductPermissions
from .....product.error_codes import ProductErrorCode
from .....product.models import PriceList
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import ProductError


class PriceListActivate(BaseMutation):
    price_list = graphene.Field(
        "saleor.graphql.product.types.price_list.PriceList",
        description="The price list being activated.",
    )

    class Arguments:
        id = graphene.ID(required=True, description="ID of the price list to activate.")

    class Meta:
        description = (
            "Trigger activation of a processed price list. "
            "Multiple price lists can be active simultaneously for the same warehouse. "
            "Runs asynchronously."
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

        if not price_list.processing_completed_at:
            raise ValidationError(
                {
                    "id": ValidationError(
                        "Price list has not completed processing.",
                        code=ProductErrorCode.INVALID.value,
                    )
                }
            )

        if price_list.warehouse.is_owned:
            raise ValidationError(
                {
                    "id": ValidationError(
                        "Cannot activate a price list for an owned warehouse.",
                        code=ProductErrorCode.OWNED_WAREHOUSE.value,
                    )
                }
            )

        price_list.activate()
        return PriceListActivate(price_list=price_list)
