from typing import Final

import graphene

from ...graphql.core.enums import to_enum
from ...shipping import (
    IncoTerm,
    PostalCodeRuleInclusionType,
    ShipmentType,
    ShippingMethodType,
)
from ..core.doc_category import DOC_CATEGORY_SHIPPING

ShippingMethodTypeEnum: Final[graphene.Enum] = to_enum(
    ShippingMethodType, type_name="ShippingMethodTypeEnum"
)
ShippingMethodTypeEnum.doc_category = DOC_CATEGORY_SHIPPING

PostalCodeRuleInclusionTypeEnum: Final[graphene.Enum] = to_enum(
    PostalCodeRuleInclusionType, type_name="PostalCodeRuleInclusionTypeEnum"
)
PostalCodeRuleInclusionTypeEnum.doc_category = DOC_CATEGORY_SHIPPING

IncoTermEnum: Final[graphene.Enum] = to_enum(IncoTerm, type_name="IncoTermEnum")
IncoTermEnum.doc_category = DOC_CATEGORY_SHIPPING

ShipmentTypeEnum: Final[graphene.Enum] = to_enum(
    ShipmentType, type_name="ShipmentTypeEnum"
)
ShipmentTypeEnum.doc_category = DOC_CATEGORY_SHIPPING
