import graphene
from django.core.exceptions import ValidationError

from ....order.actions import complete_pick
from ....order.error_codes import OrderErrorCode
from ....order.models import Pick as PickModel
from ....permission.enums import OrderPermissions
from ...core import ResolveInfo
from ...core.doc_category import DOC_CATEGORY_ORDERS
from ...core.mutations import BaseMutation
from ...core.types import OrderError
from ...core.utils import from_global_id_or_error
from ..types import Pick


class PickComplete(BaseMutation):
    """Complete a pick document."""

    pick = graphene.Field(
        Pick,
        description="The completed pick.",
    )

    class Arguments:
        pick_id = graphene.ID(
            required=True,
            description="ID of the pick to complete.",
        )

    class Meta:
        description = "Complete a pick document after all items have been picked."
        permissions = (OrderPermissions.MANAGE_ORDERS,)
        error_type_class = OrderError
        error_type_field = "order_errors"
        doc_category = DOC_CATEGORY_ORDERS

    @classmethod
    def perform_mutation(cls, root, info: ResolveInfo, /, **data):
        pick_id = data["pick_id"]

        _, pick_pk = from_global_id_or_error(pick_id, "Pick")
        try:
            pick = PickModel.objects.prefetch_related("items").get(pk=pick_pk)
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
            pick = complete_pick(pick, user=info.context.user, auto_approve=True)
        except ValueError as e:
            raise ValidationError(
                {
                    "pick_id": ValidationError(
                        str(e),
                        code=OrderErrorCode.INVALID.value,
                    )
                }
            ) from e

        return PickComplete(pick=pick)
