from enum import Enum


class WarehouseErrorCode(str, Enum):
    ALREADY_EXISTS = "already_exists"
    GRAPHQL_ERROR = "graphql_error"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    REQUIRED = "required"
    UNIQUE = "unique"
    VAT_INVALID_FORMAT = "vat_invalid_format"
    VAT_INVALID = "vat_invalid"
    VAT_SERVICE_UNAVAILABLE = "vat_service_unavailable"


class StockErrorCode(str, Enum):
    ALREADY_EXISTS = "already_exists"
    GRAPHQL_ERROR = "graphql_error"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    OWNED_WAREHOUSE = "owned_warehouse"
    REQUIRED = "required"
    UNIQUE = "unique"


class StockBulkUpdateErrorCode(str, Enum):
    GRAPHQL_ERROR = "graphql_error"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    OWNED_WAREHOUSE = "owned_warehouse"
    REQUIRED = "required"
