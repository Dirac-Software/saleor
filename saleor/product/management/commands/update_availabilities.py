import logging

import attrs
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from saleor.product.ingestion import (
    get_products_by_code_and_brand,
    get_size_to_variant_map,
    parse_sizes_and_qty,
    read_excel_with_validation,
)
from saleor.warehouse.models import Stock, Warehouse

logger = logging.getLogger(__name__)


@attrs.frozen
class ProductData:
    """Simple data class to hold product information from Excel."""

    product_code: str
    brand: str
    sizes: tuple[str, ...]
    qty: tuple[int, ...]


class Command(BaseCommand):
    help = "Update stock quantities for existing products by matching on Product Code + Brand"

    def add_arguments(self, parser):
        parser.add_argument(
            "excel_file",
            type=str,
            help="Path to the Excel file containing product data",
        )
        parser.add_argument(
            "--sheet",
            type=str,
            default="Sheet1",
            help="Name of the sheet to read from (default: Sheet1)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without applying them to the database",
        )
        parser.add_argument(
            "--warehouse",
            type=str,
            help="Warehouse slug (optional, will prompt if multiple exist)",
        )

    def handle(self, *args, **options):
        excel_file = options["excel_file"]
        sheet_name = options["sheet"]
        dry_run = options["dry_run"]
        warehouse_slug = options.get("warehouse")

        self.stdout.write(f"Reading products from: {excel_file}")

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Running in dry-run mode - no changes will be saved")
            )

        try:
            # Read Excel file
            df = read_excel_with_validation(excel_file, sheet_name)
            self.stdout.write(f"Using sheet: '{sheet_name}'")

            self.stdout.write(self.style.SUCCESS("\n=== Summary ==="))
            self.stdout.write(f"Total rows in Excel: {len(df)}")
            self.stdout.write(f"Columns found: {', '.join(df.columns.tolist())}")

            # Phase 1: Read & Parse Excel
            self.stdout.write("\n=== Phase 1: Reading & Parsing Excel ===")
            products = []
            for _idx, row in df.iterrows():
                product_data = self._process_row(row)
                if product_data:
                    products.append(product_data)

            self.stdout.write(f"Parsed {len(products)} products from Excel")

            # Display first 3 products
            if products:
                self.stdout.write("\n=== First 3 Products (parsed) ===")
                for product in products[:3]:
                    self.stdout.write(
                        f"\nProduct Code: {product.product_code}, Brand: {product.brand}"
                    )
                    self.stdout.write(
                        f"  Sizes & Quantities: {list(zip(product.sizes, product.qty, strict=False))}"
                    )

            # Phase 2: Match Products by Code + Brand
            self.stdout.write("\n=== Phase 2: Matching Products ===")
            matched_products, unmatched_products = (
                self._match_products_by_code_and_brand(products)
            )

            # Phase 3: Validate & Show Unmatched
            self.stdout.write("\n=== Validation Results ===")
            self.stdout.write(
                f"Found: {len(matched_products)}/{len(products)} products in database"
            )

            if unmatched_products:
                self.stdout.write(
                    self.style.WARNING(f"NOT FOUND: {len(unmatched_products)} products")
                )
                self.stdout.write("\nProducts not in database:")
                for product_data in unmatched_products:
                    self.stdout.write(
                        f"  - {product_data.product_code} ({product_data.brand})"
                    )

                # Prompt user to continue
                if not self._prompt_user_to_continue(
                    len(matched_products), unmatched_products
                ):
                    raise CommandError("User cancelled operation")
            else:
                self.stdout.write(
                    self.style.SUCCESS("✓ All products found in database")
                )

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        "\n=== DRY RUN - Showing what would be updated ==="
                    )
                )
                self._preview_updates(matched_products, warehouse_slug)
                return

            # Confirm overwrite behavior
            if not self._confirm_overwrite_behavior(len(matched_products)):
                raise CommandError("User cancelled operation")

            # Phase 4: Update Stock
            warehouse = self._select_warehouse(warehouse_slug)

            self.stdout.write("\n=== Phase 4: Updating Stock ===")
            with transaction.atomic():
                update_summary = self._update_stock(matched_products, warehouse)

            # Phase 5: Summary
            self.stdout.write("\n=== Update Summary ===")
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Updated {update_summary['products_updated']} products"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Updated {update_summary['variants_updated']} variants"
                )
            )
            self.stdout.write("  - Added stock to existing variants")
            if update_summary["sizes_skipped"] > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f"  - Skipped {update_summary['sizes_skipped']} sizes (variants don't exist)"
                    )
                )
            if unmatched_products:
                self.stdout.write(
                    self.style.WARNING(
                        f"✗ Skipped {len(unmatched_products)} products (not found in DB)"
                    )
                )

            self.stdout.write(
                self.style.SUCCESS("\nStock update completed successfully!")
            )

        except FileNotFoundError as e:
            raise CommandError(f"File not found: {excel_file}") from e
        except Exception as e:
            logger.exception("Error processing Excel file")
            raise CommandError(f"Error processing Excel file: {str(e)}") from e

    def _process_row(self, row):
        """Process a single row from the Excel file and return ProductData instance.

        Expected columns: Code, Brand, Sizes
        Only uses Code, Brand, and Sizes columns.
        """
        code = str(row["Code"]).strip() if pd.notna(row["Code"]) else None
        brand = str(row["Brand"]).strip().title() if pd.notna(row["Brand"]) else ""
        sizes_str = str(row["Sizes"]).strip() if pd.notna(row["Sizes"]) else ""

        if not code:
            self.stdout.write(self.style.WARNING("Skipping row with missing Code"))
            return None

        if not brand:
            self.stdout.write(
                self.style.WARNING(f"Skipping row with missing Brand (Code: {code})")
            )
            return None

        # Parse sizes and quantities
        sizes, quantities = parse_sizes_and_qty(sizes_str)

        if not sizes:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipping row with no sizes (Code: {code}, Brand: {brand})"
                )
            )
            return None

        # Create ProductData instance
        product_data = ProductData(
            product_code=code,
            brand=brand,
            sizes=sizes,
            qty=quantities,
        )

        return product_data

    def _match_products_by_code_and_brand(self, excel_products):
        """Match products from Excel with database by Product Code + Brand.

        Returns: (matched_dict, unmatched_list)
        - matched_dict: {ProductData: Product instance}
        - unmatched_list: [ProductData instances not found]
        """
        # Build a list of all product codes from Excel
        all_codes = [p.product_code for p in excel_products]

        # Get products from database
        code_brand_to_product = get_products_by_code_and_brand(all_codes)

        # Match Excel products with database products
        matched_products = {}
        unmatched_products = []

        for excel_product in excel_products:
            key = (excel_product.product_code, excel_product.brand)

            if key in code_brand_to_product:
                matched_products[excel_product] = code_brand_to_product[key]
            else:
                unmatched_products.append(excel_product)

        return matched_products, unmatched_products

    def _prompt_user_to_continue(self, matched_count, unmatched_products):
        """Show unmatched products and ask for confirmation.

        Returns True if user confirms, False otherwise.
        """
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(
            f"Continue updating {matched_count} products? (Skipping {len(unmatched_products)} not found)"
        )
        self.stdout.write(
            self.style.WARNING(
                "Note: This will OVERWRITE quantities (not add to them)."
            )
        )
        response = input("[Y/n]: ").strip().lower()

        if response not in ["y", "yes", ""]:
            return False

        self.stdout.write(self.style.SUCCESS("✓ User confirmed"))
        return True

    def _confirm_overwrite_behavior(self, matched_count):
        """Confirm with user that quantities will be OVERWRITTEN.

        Returns True if user confirms, False otherwise.
        """
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.WARNING("\n⚠ IMPORTANT: Stock Quantity Behavior"))
        self.stdout.write(
            self.style.WARNING(
                f"\nThis will OVERWRITE existing quantities for {matched_count} product(s) on this sheet."
            )
        )
        self.stdout.write(
            "\nExisting stock quantities will be REPLACED with the values from the Excel file."
        )
        self.stdout.write(
            "Example: If database has Size 8 with qty=10, and Excel has Size 8[3],"
        )
        self.stdout.write("         the result will be qty=3 (not 13).")
        self.stdout.write("")

        response = input("Do you want to proceed? [Y/n]: ").strip().lower()

        if response not in ["y", "yes", ""]:
            return False

        self.stdout.write(self.style.SUCCESS("✓ User confirmed overwrite behavior"))
        return True

    def _select_warehouse(self, warehouse_slug=None):
        """Select warehouse for stock allocation.

        If warehouse_slug is provided, use it.
        If multiple warehouses exist, prompt user to select one.
        If only one exists, use it automatically.
        """
        if warehouse_slug:
            try:
                warehouse = Warehouse.objects.get(slug=warehouse_slug)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n=== Warehouse ===\nUsing warehouse: {warehouse.slug} ({warehouse.name})"
                    )
                )
                return warehouse
            except Warehouse.DoesNotExist as e:
                raise CommandError(
                    f"Warehouse with slug '{warehouse_slug}' not found"
                ) from e

        warehouses = Warehouse.objects.all()

        if not warehouses.exists():
            raise CommandError("No warehouses found in the database!")

        if len(warehouses) == 1:
            warehouse_or_none = warehouses.first()
            assert warehouse_or_none is not None  # We checked exists() above
            self.stdout.write(self.style.SUCCESS("\n=== Warehouse ==="))
            self.stdout.write(
                f"Using warehouse: {warehouse_or_none.slug} ({warehouse_or_none.name})"
            )
            return warehouse_or_none

        # Multiple warehouses - prompt user to select
        self.stdout.write(self.style.SUCCESS("\n=== Warehouse Selection ==="))
        self.stdout.write(f"Found {len(warehouses)} warehouses:")
        warehouse_list = list(warehouses)
        for idx, warehouse in enumerate(warehouse_list, 1):
            self.stdout.write(f"  {idx}. {warehouse.slug} ({warehouse.name})")

        while True:
            try:
                choice = input(
                    f"\nSelect warehouse (1-{len(warehouse_list)}): "
                ).strip()
                warehouse_idx = int(choice) - 1
                if 0 <= warehouse_idx < len(warehouse_list):
                    selected_warehouse = warehouse_list[warehouse_idx]
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"✓ Selected warehouse: {selected_warehouse.slug}"
                        )
                    )
                    return selected_warehouse
                self.stdout.write(
                    self.style.ERROR(
                        f"Invalid choice. Please enter 1-{len(warehouse_list)}"
                    )
                )
            except (ValueError, KeyboardInterrupt):
                raise CommandError("Warehouse selection cancelled") from None

    def _preview_updates(self, matched_products, warehouse_slug):
        """Preview what would be updated in dry-run mode."""
        warehouse = self._select_warehouse(warehouse_slug)

        self.stdout.write("\nProducts that would be updated:")

        for product_data, product in matched_products.items():
            self.stdout.write(
                f"\n  Product: {product.name} (Code: {product_data.product_code}, Brand: {product_data.brand})"
            )

            # Get existing variants with sizes
            size_to_variant = self._get_size_to_variant_map(product)

            # Show what would happen for each size
            for size, qty in zip(product_data.sizes, product_data.qty, strict=False):
                existing_variant = size_to_variant.get(size)

                if existing_variant:
                    # Get current stock
                    stock = Stock.objects.filter(
                        product_variant=existing_variant, warehouse=warehouse
                    ).first()

                    if stock:
                        old_qty = stock.quantity
                        new_qty = qty
                        self.stdout.write(
                            f"    ✓ Size {size}: Would set quantity to {new_qty} (was {old_qty})"
                        )
                    else:
                        self.stdout.write(
                            f"    ✓ Size {size}: Would create stock with quantity {qty}"
                        )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ⚠ Size {size}: Would skip (variant doesn't exist)"
                        )
                    )

    def _get_size_to_variant_map(self, product):
        """Get a map of size -> variant for a product."""
        return get_size_to_variant_map(product)

    def _update_stock(self, matched_products, warehouse):
        """Update stock for matched products.

        Returns a summary dictionary with counts.
        """
        products_updated = 0
        variants_updated = 0
        sizes_skipped = 0

        for idx, (product_data, product) in enumerate(matched_products.items(), 1):
            self.stdout.write(
                f"\n[{idx}/{len(matched_products)}] Updating: {product.name}"
            )

            # Get size to variant map
            size_to_variant = self._get_size_to_variant_map(product)

            # Track if this product was updated
            product_was_updated = False

            # Process each size
            for size, qty in zip(product_data.sizes, product_data.qty, strict=False):
                existing_variant = size_to_variant.get(size)

                if existing_variant:
                    # Variant exists - add to the quantity
                    stock = Stock.objects.filter(
                        product_variant=existing_variant, warehouse=warehouse
                    ).first()

                    if stock:
                        old_qty = stock.quantity
                        stock.quantity = qty
                        stock.save()
                        self.stdout.write(
                            f"  ✓ Size {size}: Set quantity to {qty} (was {old_qty})"
                        )
                    else:
                        # Stock doesn't exist for this variant, create it
                        Stock.objects.create(
                            warehouse=warehouse,
                            product_variant=existing_variant,
                            quantity=qty,
                        )
                        self.stdout.write(
                            f"  ✓ Size {size}: Created stock with quantity {qty}"
                        )

                    variants_updated += 1
                    product_was_updated = True
                else:
                    # Variant doesn't exist - skip it (don't create new variants)
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ⚠ Size {size}: Skipped (variant doesn't exist)"
                        )
                    )
                    sizes_skipped += 1

            if product_was_updated:
                products_updated += 1

        return {
            "products_updated": products_updated,
            "variants_updated": variants_updated,
            "sizes_skipped": sizes_skipped,
        }
