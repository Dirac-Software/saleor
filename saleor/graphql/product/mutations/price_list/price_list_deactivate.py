"""PriceList deactivate mutation."""

import graphene

from .....permission.enums import ProductPermissions
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import ProductError


class PriceListDeactivate(BaseMutation):
    price_list = graphene.Field(
        "saleor.graphql.product.types.price_list.PriceList",
        description="The price list being deactivated.",
    )

    class Arguments:
        id = graphene.ID(
            required=True, description="ID of the price list to deactivate."
        )

    class Meta:
        description = (
            "Trigger deactivation of an active price list. "
            "Zeros warehouse stock for all items. Runs asynchronously."
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
        price_list.deactivate()
        return PriceListDeactivate(price_list=price_list)
