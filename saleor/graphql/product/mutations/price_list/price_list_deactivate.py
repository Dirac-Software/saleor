"""PriceList deactivate mutation."""

import graphene
from django.core.exceptions import ValidationError

from .....permission.enums import ProductPermissions
from .....product.error_codes import ProductErrorCode
from .....product.tasks import (
    _count_draft_unconfirmed_orders,
    deactivate_price_list_task,
)
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
        force = graphene.Boolean(
            description=(
                "Proceed even if draft/unconfirmed orders have allocations "
                "that will be removed."
            ),
            default_value=False,
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

        if not data.get("force"):
            product_ids = list(
                price_list.items.filter(is_valid=True, product_id__isnull=False)
                .values_list("product_id", flat=True)
                .distinct()
            )
            if product_ids:
                affected_count = _count_draft_unconfirmed_orders(
                    price_list.warehouse, product_ids
                )
                if affected_count:
                    raise ValidationError(
                        {
                            "force": ValidationError(
                                f"{affected_count} draft/unconfirmed order(s) have "
                                "allocations that will be removed. "
                                "Pass force=true to proceed.",
                                code=ProductErrorCode.ORDERS_REQUIRE_AMENDMENT.value,
                            )
                        }
                    )

        deactivate_price_list_task.delay(price_list.pk)
        return PriceListDeactivate(price_list=price_list)
