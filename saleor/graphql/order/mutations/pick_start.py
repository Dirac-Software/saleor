import graphene
from django.core.exceptions import ValidationError

from ....order.actions import start_pick
from ....order.error_codes import OrderErrorCode
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ...core.utils import from_global_id_or_error
from ..types import Pick


class PickStart(BaseMutation):
    """Start a pick session for an order fulfillment."""

    pick = graphene.Field(
        Pick,
        description="The started pick.",
    )

    class Arguments:
        pick_id = graphene.ID(
            required=True,
            description="ID of the pick to start.",
        )

    class Meta:
        description = "Start picking items from warehouse for fulfillment."
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"
        doc_category = DOC_CATEGORY_ORDERS

    @classmethod
    def perform_mutation(cls, root, info: ResolveInfo, /, **data):
        pick_id = data["pick_id"]

        _, pick_pk = from_global_id_or_error(pick_id, "Pick")

        from ....order.models import Pick as PickModel

        try:
            pick = PickModel.objects.get(pk=pick_pk)
        except PickModel.DoesNotExist:
            raise ValidationError(
                {
                    "pick_id": ValidationError(
                        "Pick not found.",
                        code=OrderErrorCode.NOT_FOUND.value,
                    )
                }
            ) from None

        try:
            pick = start_pick(pick, user=info.context.user)
        except ValueError as e:
            raise ValidationError(
                {
                    "pick_id": ValidationError(
                        str(e),
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            ) from e

        return PickStart(pick=pick)
