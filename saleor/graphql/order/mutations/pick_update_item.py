import graphene
from django.core.exceptions import ValidationError

from ....order.actions import update_pick_item
from ....order.error_codes import OrderErrorCode
from ....order.models import PickItem as PickItemModel
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ...core.utils import from_global_id_or_error
from ..types import PickItem


class PickUpdateItem(BaseMutation):
    """Update quantity picked for a pick item."""

    pick_item = graphene.Field(
        PickItem,
        description="The updated pick item.",
    )

    class Arguments:
        pick_item_id = graphene.ID(
            required=True,
            description="ID of the pick item to update.",
        )
        quantity_picked = graphene.Int(
            required=True,
            description="Quantity picked.",
        )
        notes = graphene.String(
            description="Optional notes about picking this item.",
        )

    class Meta:
        description = "Update quantity picked for a pick item."
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"
        doc_category = DOC_CATEGORY_ORDERS

    @classmethod
    def perform_mutation(cls, root, info: ResolveInfo, /, **data):
        pick_item_id = data["pick_item_id"]
        quantity_picked = data["quantity_picked"]
        notes = data.get("notes", "")

        _, pick_item_pk = from_global_id_or_error(pick_item_id, "PickItem")
        try:
            pick_item = PickItemModel.objects.select_related("pick").get(
                pk=pick_item_pk
            )
        except PickItemModel.DoesNotExist:
            raise ValidationError(
                {
                    "pick_item_id": ValidationError(
                        "Pick item not found.",
                        code=OrderErrorCode.NOT_FOUND.value,
                    )
                }
            ) from None

        if quantity_picked < 0:
            raise ValidationError(
                {
                    "quantity_picked": ValidationError(
                        "Quantity must be non-negative.",
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            )

        try:
            pick_item = update_pick_item(
                pick_item=pick_item,
                quantity_picked=quantity_picked,
                user=info.context.user,
                notes=notes,
            )
        except ValueError as e:
            raise ValidationError(
                {
                    "quantity_picked": ValidationError(
                        str(e),
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            ) from e

        return PickUpdateItem(pick_item=pick_item)
