from typing import Final

import graphene

from ...invoice import InvoiceType
from ..core.doc_category import DOC_CATEGORY_ORDERS
from ..core.enums import to_enum


InvoiceTypeEnum: Final[graphene.Enum] = to_enum(InvoiceType, type_name="InvoiceType")
InvoiceTypeEnum.doc_category = DOC_CATEGORY_ORDERS
