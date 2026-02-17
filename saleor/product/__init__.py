class ProductMediaTypes:
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"

    CHOICES = [
        (IMAGE, "An uploaded image or an URL to an image"),
        (VIDEO, "A URL to an external video"),
    ]


class ProductTypeKind:
    NORMAL = "normal"
    GIFT_CARD = "gift_card"

    CHOICES = [
        (NORMAL, "A standard product type."),
        (GIFT_CARD, "A gift card product type."),
    ]


class PriceListStatus:
    ACTIVE = "active"
    INACTIVE = "inactive"

    CHOICES = [
        (ACTIVE, "Active price list for its warehouse."),
        (INACTIVE, "Inactive — superseded or not yet activated."),
    ]
