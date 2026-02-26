from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

from ...models import Product

PRODUCT_CODE_SLUG = "product-code"
BRAND_SLUG = "brand"


class Command(BaseCommand):
    help = "Merge two products by moving all references from source to target, then deleting source."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="subcommand")

        # merge <source_id> <target_id>
        merge_parser = subparsers.add_parser(
            "merge", help="Merge a single source product into a target product"
        )
        merge_parser.add_argument(
            "source_id", type=int, help="ID of the product to delete after merging"
        )
        merge_parser.add_argument(
            "target_id", type=int, help="ID of the product to keep"
        )
        merge_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be changed without making any modifications",
        )

        # auto-merge
        auto_parser = subparsers.add_parser(
            "auto-merge",
            help=(
                f"Find all products sharing the same lowercase '{PRODUCT_CODE_SLUG}' "
                f"and '{BRAND_SLUG}' attribute values and merge duplicates into the "
                "lowest-pk product in each group"
            ),
        )
        auto_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be changed without making any modifications",
        )

    def handle(self, *args, **options):
        subcommand = options.get("subcommand")
        if subcommand == "auto-merge":
            self._handle_auto_merge(options)
        else:
            self._handle_merge(options)

    # ------------------------------------------------------------------
    # Single merge
    # ------------------------------------------------------------------

    def _handle_merge(self, options):
        source_id = options["source_id"]
        target_id = options["target_id"]
        dry_run = options["dry_run"]

        if source_id == target_id:
            raise CommandError("source_id and target_id must be different")

        try:
            source = Product.objects.get(pk=source_id)
        except Product.DoesNotExist:
            raise CommandError(f"Source product with id={source_id} not found")

        try:
            target = Product.objects.get(pk=target_id)
        except Product.DoesNotExist:
            raise CommandError(f"Target product with id={target_id} not found")

        self.stdout.write(f"Source: [{source.pk}] {source.name}")
        self.stdout.write(f"Target: [{target.pk}] {target.name}")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made\n"))

        with transaction.atomic():
            self._merge_pair(source, target, dry_run)

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(
                    self.style.WARNING("\nDry run complete — rolled back all changes.")
                )
            else:
                self.stdout.write(
                    f"\nDeleting source product [{source.pk}] {source.name} ..."
                )
                source.delete()
                self.stdout.write(self.style.SUCCESS("Merge complete."))

    # ------------------------------------------------------------------
    # Auto-merge
    # ------------------------------------------------------------------

    def _handle_auto_merge(self, options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be made\n"))

        groups = self._find_duplicate_groups()

        if not groups:
            self.stdout.write("No duplicate products found.")
            return

        self.stdout.write(f"Found {len(groups)} group(s) of duplicates.\n")

        for key, product_ids in groups.items():
            product_code, brand = key
            products = list(Product.objects.filter(pk__in=product_ids).order_by("pk"))
            target = self._pick_target(products)
            sources = [p for p in products if p.pk != target.pk]

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nGroup: product-code={product_code!r} brand={brand!r} "
                    f"— keeping [{target.pk}] {target.name}, "
                    f"merging {len(sources)} duplicate(s)"
                )
            )

            for source in sources:
                self.stdout.write(f"\n  Source: [{source.pk}] {source.name}")
                with transaction.atomic():
                    self._merge_pair(source, target, dry_run)
                    if dry_run:
                        transaction.set_rollback(True)
                    else:
                        self.stdout.write(f"  Deleting [{source.pk}] {source.name} ...")
                        source.delete()

        if dry_run:
            self.stdout.write(
                self.style.WARNING("\nDry run complete — no changes made.")
            )
        else:
            self.stdout.write(self.style.SUCCESS("\nAuto-merge complete."))

    def _pick_target(self, products):
        """Pick the product to keep from a duplicate group.
        Prefer the product whose variants have live allocations (it has real orders),
        falling back to the lowest pk.
        """
        from ....warehouse.models import Allocation

        for product in sorted(products, key=lambda p: p.pk):
            has_allocations = Allocation.objects.filter(
                stock__product_variant__product=product
            ).exists()
            if has_allocations:
                return product

        return min(products, key=lambda p: p.pk)

    def _find_duplicate_groups(self):
        """Return a dict mapping (product_code_lower, brand_lower) -> [product_id, ...]
        for all groups that have more than one product.
        """
        from ....attribute.models import AssignedProductAttributeValue

        # Fetch product_code values per product
        code_qs = AssignedProductAttributeValue.objects.filter(
            value__attribute__slug=PRODUCT_CODE_SLUG
        ).values("product_id", "value__name")
        code_by_product = {
            row["product_id"]: row["value__name"].lower()
            for row in code_qs
            if row["value__name"]
        }

        # Fetch brand values per product
        brand_qs = AssignedProductAttributeValue.objects.filter(
            value__attribute__slug=BRAND_SLUG
        ).values("product_id", "value__name")
        brand_by_product = {
            row["product_id"]: row["value__name"].lower()
            for row in brand_qs
            if row["value__name"]
        }

        # Group products by (product_code, brand)
        groups: dict[tuple, list] = {}
        all_product_ids = set(code_by_product) | set(brand_by_product)
        for product_id in all_product_ids:
            code = code_by_product.get(product_id)
            brand = brand_by_product.get(product_id)
            if code and brand:
                key = (code, brand)
                groups.setdefault(key, []).append(product_id)

        return {k: v for k, v in groups.items() if len(v) > 1}

    # ------------------------------------------------------------------
    # Core merge logic (shared)
    # ------------------------------------------------------------------

    def _merge_pair(self, source, target, dry_run):
        self._merge_translations(source, target, dry_run)
        self._merge_channel_listings(source, target, dry_run)
        self._merge_variants(source, target, dry_run)
        self._merge_media(source, target, dry_run)
        self._merge_collection_products(source, target, dry_run)
        self._merge_attribute_values(source, target, dry_run)
        self._merge_price_list_items(source, target, dry_run)
        self._merge_gift_cards(source, target, dry_run)
        self._merge_vouchers(source, target, dry_run)
        self._merge_shipping_exclusions(source, target, dry_run)

    def _merge_translations(self, source, target, dry_run):
        existing_langs = set(
            target.translations.values_list("language_code", flat=True)
        )
        to_move = source.translations.exclude(language_code__in=existing_langs)
        skipped = source.translations.filter(language_code__in=existing_langs)

        self.stdout.write(
            f"\nTranslations: moving {to_move.count()}, skipping {skipped.count()} (target already has them)"
        )
        if not dry_run:
            to_move.update(product=target)

    def _merge_channel_listings(self, source, target, dry_run):
        existing_channels = set(
            target.channel_listings.values_list("channel_id", flat=True)
        )
        to_move = source.channel_listings.exclude(channel_id__in=existing_channels)
        skipped = source.channel_listings.filter(channel_id__in=existing_channels)

        self.stdout.write(
            f"Channel listings: moving {to_move.count()}, skipping {skipped.count()} (target already listed in those channels)"
        )
        if not dry_run:
            to_move.update(product=target)

    def _build_variant_fingerprint(self, variant):
        """Return a frozenset of attribute value IDs assigned to this variant."""
        return frozenset(variant.attributevalues.values_list("value_id", flat=True))

    def _build_variant_slug_fingerprint(self, variant):
        """Return a frozenset of lowercased attribute value slugs for ilike matching."""
        from ....attribute.models import AttributeValue

        value_ids = variant.attributevalues.values_list("value_id", flat=True)
        slugs = AttributeValue.objects.filter(pk__in=value_ids).values_list(
            "slug", flat=True
        )
        return frozenset(s.lower() for s in slugs)

    def _match_variants(self, source, target):
        """Return a list of (source_variant, target_variant) pairs that match by
        attribute values, plus a list of unmatched source variants.

        Matching priority:
          1. Exact: same frozenset of value IDs
          2. ilike: same frozenset of lowercased value slugs
        """
        target_variants = list(target.variants.prefetch_related("attributevalues"))
        target_by_id = {self._build_variant_fingerprint(v): v for v in target_variants}
        target_by_slug = {
            self._build_variant_slug_fingerprint(v): v for v in target_variants
        }

        matched = []
        unmatched = []

        for sv in source.variants.prefetch_related("attributevalues"):
            id_fp = self._build_variant_fingerprint(sv)
            slug_fp = self._build_variant_slug_fingerprint(sv)

            tv = target_by_id.get(id_fp) or target_by_slug.get(slug_fp)
            if tv:
                matched.append((sv, tv))
            else:
                unmatched.append(sv)

        return matched, unmatched

    def _merge_single_variant(self, source_variant, target_variant, dry_run):
        """Move all references from source_variant onto target_variant, then delete it."""
        from ....checkout.models import CheckoutLine
        from ....order.models import OrderLine
        from ....warehouse.models import Stock

        sv, tv = source_variant, target_variant
        self.stdout.write(
            f"  Merging variant [{sv.pk}] {sv.name or sv.sku!r} "
            f"-> [{tv.pk}] {tv.name or tv.sku!r}"
        )

        # OrderLine (SET_NULL on delete, so safe to remap)
        order_lines = OrderLine.objects.filter(variant=sv)
        self.stdout.write(f"    Order lines: {order_lines.count()}")
        if not dry_run:
            order_lines.update(variant=tv)

        # CheckoutLine
        checkout_lines = CheckoutLine.objects.filter(variant=sv)
        self.stdout.write(f"    Checkout lines: {checkout_lines.count()}")
        if not dry_run:
            checkout_lines.update(variant=tv)

        # PurchaseOrderItem
        purchase_items = sv.items.all()
        self.stdout.write(f"    Purchase order items: {purchase_items.count()}")
        if not dry_run:
            purchase_items.update(product_variant=tv)

        # Stock — merge quantities when warehouse already exists on target
        existing_warehouses = set(tv.stocks.values_list("warehouse_id", flat=True))
        stocks_to_move = sv.stocks.exclude(warehouse_id__in=existing_warehouses)
        stocks_to_merge = sv.stocks.filter(warehouse_id__in=existing_warehouses)
        self.stdout.write(
            f"    Stock: moving {stocks_to_move.count()}, "
            f"merging quantities for {stocks_to_merge.count()} warehouse(s) already on target"
        )

        # Refuse to merge if source stocks that would be deleted have live allocations
        allocated = stocks_to_merge.filter(allocations__isnull=False).distinct()
        if allocated.exists():
            warehouse_ids = list(allocated.values_list("warehouse_id", flat=True))
            raise CommandError(
                f"Cannot merge variant [{sv.pk}] into [{tv.pk}]: source variant has "
                f"allocations on shared warehouse(s) {warehouse_ids}. "
                f"Swap source and target so the variant with allocations is kept."
            )

        if not dry_run:
            stocks_to_move.update(product_variant=tv)
            for src_stock in stocks_to_merge:
                Stock.objects.filter(
                    warehouse=src_stock.warehouse, product_variant=tv
                ).update(
                    quantity=models.F("quantity") + src_stock.quantity,
                    quantity_allocated=models.F("quantity_allocated")
                    + src_stock.quantity_allocated,
                )
                src_stock.delete()

        # ProductVariantChannelListing — skip channels target already has
        existing_channels = set(
            tv.channel_listings.values_list("channel_id", flat=True)
        )
        listings_to_move = sv.channel_listings.exclude(channel_id__in=existing_channels)
        listings_skipped = sv.channel_listings.filter(channel_id__in=existing_channels)
        self.stdout.write(
            f"    Variant channel listings: moving {listings_to_move.count()}, "
            f"skipping {listings_skipped.count()}"
        )
        if not dry_run:
            listings_to_move.update(variant=tv)

        # ProductVariantTranslation — skip languages target already has
        existing_langs = set(tv.translations.values_list("language_code", flat=True))
        translations_to_move = sv.translations.exclude(language_code__in=existing_langs)
        translations_skipped = sv.translations.filter(language_code__in=existing_langs)
        self.stdout.write(
            f"    Variant translations: moving {translations_to_move.count()}, "
            f"skipping {translations_skipped.count()}"
        )
        if not dry_run:
            translations_to_move.update(product_variant=tv)

        # VariantMedia — skip media target already has
        existing_media = set(tv.variant_media.values_list("media_id", flat=True))
        media_to_move = sv.variant_media.exclude(media_id__in=existing_media)
        self.stdout.write(f"    Variant media: moving {media_to_move.count()}")
        if not dry_run:
            media_to_move.update(variant=tv)

        if not dry_run:
            sv.delete()

    def _merge_variants(self, source, target, dry_run):
        matched, unmatched = self._match_variants(source, target)

        self.stdout.write(
            f"\nVariants: {len(matched)} matched (will merge), "
            f"{len(unmatched)} unmatched (will move to target product)"
        )

        for sv, tv in matched:
            self._merge_single_variant(sv, tv, dry_run)

        if unmatched:
            self.stdout.write(
                f"  Moving {len(unmatched)} unmatched variants to target product"
            )
            if not dry_run:
                from ...models import ProductVariant

                ProductVariant.objects.filter(pk__in=[v.pk for v in unmatched]).update(
                    product=target
                )

    def _merge_media(self, source, target, dry_run):
        count = source.media.count()
        self.stdout.write(f"Media: moving {count}")
        if not dry_run:
            source.media.update(product=target)

    def _merge_collection_products(self, source, target, dry_run):
        existing_collections = set(
            target.collectionproduct.values_list("collection_id", flat=True)
        )
        to_move = source.collectionproduct.exclude(
            collection_id__in=existing_collections
        )
        skipped = source.collectionproduct.filter(
            collection_id__in=existing_collections
        )

        self.stdout.write(
            f"Collections: moving {to_move.count()}, skipping {skipped.count()} (target already in those collections)"
        )
        if not dry_run:
            to_move.update(product=target)

    def _merge_attribute_values(self, source, target, dry_run):
        existing_values = set(target.attributevalues.values_list("value_id", flat=True))
        to_move = source.attributevalues.exclude(value_id__in=existing_values)
        skipped = source.attributevalues.filter(value_id__in=existing_values)

        self.stdout.write(
            f"Attribute values: moving {to_move.count()}, skipping {skipped.count()} (target already has them)"
        )
        if not dry_run:
            to_move.update(product=target)

    def _merge_price_list_items(self, source, target, dry_run):
        count = source.price_list_items.count()
        self.stdout.write(f"Price list items: moving {count}")
        if not dry_run:
            source.price_list_items.update(product=target)

    def _merge_gift_cards(self, source, target, dry_run):
        count = source.gift_cards.count()
        self.stdout.write(f"Gift cards: moving {count}")
        if not dry_run:
            source.gift_cards.update(product=target)

    def _merge_vouchers(self, source, target, dry_run):
        from ....discount.models import Voucher

        vouchers = Voucher.objects.filter(products=source)
        count = vouchers.count()
        self.stdout.write(f"Vouchers: updating {count}")
        if not dry_run:
            for voucher in vouchers:
                voucher.products.add(target)
                voucher.products.remove(source)

    def _merge_shipping_exclusions(self, source, target, dry_run):
        from ....shipping.models import ShippingMethod

        methods = ShippingMethod.objects.filter(excluded_products=source)
        count = methods.count()
        self.stdout.write(f"Shipping method exclusions: updating {count}")
        if not dry_run:
            for method in methods:
                method.excluded_products.add(target)
                method.excluded_products.remove(source)
