"""PriceList replace mutation."""

import graphene
from django.core.exceptions import ValidationError

from .....permission.enums import ProductPermissions
from .....product.error_codes import ProductErrorCode
from .....product.tasks import (
    _count_draft_unconfirmed_orders,
    replace_price_list_task,
)
from ....core import ResolveInfo
from ....core.doc_category import DOC_CATEGORY_PRODUCTS
from ....core.mutations import BaseMutation
from ....core.types import ProductError


class PriceListReplace(BaseMutation):
    old_price_list = graphene.Field(
        "saleor.graphql.product.types.price_list.PriceList",
        description="The price list being replaced (will become INACTIVE).",
    )
    new_price_list = graphene.Field(
        "saleor.graphql.product.types.price_list.PriceList",
        description="The new price list taking over (will become ACTIVE).",
    )

    class Arguments:
        old_price_list_id = graphene.ID(
            required=True,
            description="ID of the currently active price list to replace.",
        )
        new_price_list_id = graphene.ID(
            required=True,
            description="ID of the processed price list that will become active.",
        )
        force = graphene.Boolean(
            description=(
                "Proceed even if draft/unconfirmed orders may have allocations "
                "affected by this replacement."
            ),
            default_value=False,
        )

    class Meta:
        description = (
            "Replace an active price list with a new one for the same warehouse. "
            "Performs a diff-based stock handover. Runs asynchronously."
        )
        doc_category = DOC_CATEGORY_PRODUCTS
        permissions = (ProductPermissions.MANAGE_PRODUCTS,)
        error_type_class = ProductError
        error_type_field = "product_errors"

    @classmethod
    def perform_mutation(cls, _root, info: ResolveInfo, /, **data):
        old_pl = cls.get_node_or_error(
            info,
            data["old_price_list_id"],
            field="old_price_list_id",
            only_type="PriceList",
        )
        new_pl = cls.get_node_or_error(
            info,
            data["new_price_list_id"],
            field="new_price_list_id",
            only_type="PriceList",
        )

        if not data.get("force"):
            affected_count = _count_draft_unconfirmed_orders(old_pl.warehouse)
            if affected_count:
                raise ValidationError(
                    {
                        "force": ValidationError(
                            f"{affected_count} draft/unconfirmed order(s) may have "
                            "allocations affected by this replacement. "
                            "Pass force=true to proceed.",
                            code=ProductErrorCode.ORDERS_REQUIRE_AMENDMENT.value,
                        )
                    }
                )

        replace_price_list_task.delay(old_pl.pk, new_pl.pk)
        return PriceListReplace(old_price_list=old_pl, new_price_list=new_pl)
