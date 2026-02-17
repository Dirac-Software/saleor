"""PriceList activate mutation."""

import graphene

from .....permission.enums import ProductPermissions
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
            "If another price list is currently active for the same warehouse, "
            "it will be replaced automatically. Runs asynchronously."
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        price_list = cls.get_node_or_error(
            info, data["id"], field="id", only_type="PriceList"
        )
        price_list.activate()
        return PriceListActivate(price_list=price_list)
