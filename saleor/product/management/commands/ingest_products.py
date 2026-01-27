"""Django management command for product ingestion from Excel files.

This is a thin wrapper around the ingestion logic in saleor.product.ingestion.
The command handles:
- CLI argument parsing
- Interactive user prompts (when config fields are None)
- Output formatting and display

All business logic is in saleor.product.ingestion.
"""

import logging

import attrs
from django.core.management.base import BaseCommand, CommandError

from saleor.product.ingestion import (
    ColumnMappingRequired,
    IngestConfig,
    IngestionResult,
    MinimumOrderQuantityRequired,
    PriceInterpretationConfirmationRequired,
    SpreadsheetColumnMapping,
    StockUpdateModeRequired,
    ingest_products_from_excel,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Ingest products from an Excel file into specified warehouse."

    def add_arguments(self, parser):
        parser.add_argument(
            "excel_file",
            type=str,
            help="Path to Excel file containing products",
        )
        parser.add_argument(
            "--warehouse-name",
            type=str,
            required=True,
            help="Name of warehouse to ingest products into",
        )
        parser.add_argument(
            "--warehouse-address",
            type=str,
            required=True,
            help="Full address of warehouse (e.g., '123 Sheikh Zayed Rd, Dubai')",
        )
        parser.add_argument(
            "--warehouse-country",
            type=str,
            required=True,
            help="ISO2 country code for warehouse (e.g., 'AE', 'GB', 'US')",
        )
        parser.add_argument(
            "--sheet",
            type=str,
            default="Sheet1",
            help="Name of the sheet to read from (default: Sheet1)",
        )
        parser.add_argument(
            "--header-row",
            type=int,
            default=0,
            help="Row number containing column headers (0-indexed, default: 0)",
        )
        parser.add_argument(
            "--not-for-web",
            action="store_true",
            help="Mark products as unavailable on all channels (prices still required)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate without making changes",
        )

    def handle(self, *args, **options):
        from django_countries import countries

        excel_file = options["excel_file"]
        dry_run = options["dry_run"]
        sheet_name = options["sheet"]
        header_row = options["header_row"]
        warehouse_name = options["warehouse_name"]
        warehouse_address = options["warehouse_address"]
        warehouse_country = options["warehouse_country"].upper()
        not_for_web = options["not_for_web"]

        # Validate country code
        if warehouse_country not in dict(countries):
            raise CommandError(
                f"Invalid country code '{warehouse_country}'. "
                f"Please use a valid ISO2 country code (e.g., 'AE', 'GB', 'US')."
            )

        self.stdout.write(f"Reading products from: {excel_file}")
        self.stdout.write(f"Sheet: {sheet_name}")

        if not_for_web:
            self.stdout.write(
                self.style.WARNING(
                    "\n⚠ NOT-FOR-WEB mode enabled:"
                    "\n  - Products will be marked as UNAVAILABLE on all channels"
                    "\n  - Prices are still required"
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING("Running in dry-run mode - no changes will be saved")
            )

        # Interactive header row detection if not explicitly set
        if header_row == 0:  # Default value, ask user
            header_row = self._prompt_header_row(excel_file, sheet_name)

        # Create initial config (with interactive fields set to None)
        config = IngestConfig(
            warehouse_name=warehouse_name,
            warehouse_address=warehouse_address,
            warehouse_country=warehouse_country,
            sheet_name=sheet_name,
            header_row=header_row,
            column_mapping=None,  # None triggers interactive prompt
            not_for_web=not_for_web,
            dry_run=dry_run,
            # These fields are None - will trigger interactive prompts:
            stock_update_mode=None,
            minimum_order_quantity=None,
            confirm_price_interpretation=False,
        )

        # Retry loop for interactive decisions
        while True:
            try:
                # Call main ingestion function
                result = ingest_products_from_excel(config, excel_file)

                # Success! Display results and exit
                self._display_results(result)
                break

            except MinimumOrderQuantityRequired as e:
                # Prompt for MOQ
                moq = self._prompt_minimum_order_quantity(e.product_count)
                config = attrs.evolve(config, minimum_order_quantity=moq)
                # Retry with updated config

            except StockUpdateModeRequired as e:
                # Prompt for stock update mode
                mode = self._prompt_stock_update_mode(
                    e.products_in_warehouse, e.warehouse_name
                )
                config = attrs.evolve(config, stock_update_mode=mode)
                # Retry with updated config

            except PriceInterpretationConfirmationRequired:
                # Prompt for price confirmation
                self._prompt_price_interpretation_confirmation()
                config = attrs.evolve(config, confirm_price_interpretation=True)
                # Retry with updated config

            except ColumnMappingRequired as e:
                # Prompt for column mapping
                mapping = self._prompt_column_mapping(e.available_columns)
                config = attrs.evolve(config, column_mapping=mapping)
                # Retry with updated config

    def _prompt_minimum_order_quantity(self, product_count: int) -> int:
        """Prompt user for Minimum Order Quantity.

        Args:
            product_count: Number of products being ingested

        Returns:
            MOQ value as integer

        Raises:
            CommandError: If user provides invalid input

        """
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(
            self.style.WARNING(
                f"Minimum Order Quantity (MOQ) required for {product_count} product(s)"
            )
        )
        self.stdout.write(
            "This is the minimum quantity a customer must order for each product."
        )

        while True:
            moq_input = input(
                "\nEnter Minimum Order Quantity (positive integer): "
            ).strip()

            if not moq_input:
                self.stdout.write(self.style.ERROR("MOQ cannot be empty"))
                continue

            try:
                moq = int(moq_input)
                if moq <= 0:
                    self.stdout.write(self.style.ERROR("MOQ must be greater than 0"))
                    continue

                self.stdout.write(self.style.SUCCESS(f"✓ MOQ set to: {moq}"))
                return moq

            except ValueError:
                self.stdout.write(self.style.ERROR("Please enter a valid integer"))

    def _prompt_stock_update_mode(
        self, products_in_warehouse: list, warehouse_name: str
    ) -> str:
        """Prompt user for stock update mode (REPLACE or ADD).

        Args:
            products_in_warehouse: List of (ProductData, Product) tuples
            warehouse_name: Name of warehouse

        Returns:
            "replace" or "add"

        Raises:
            CommandError: If user cancels

        """
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(
            self.style.WARNING(
                f"⚠ {len(products_in_warehouse)} product(s) already exist "
                f"in warehouse '{warehouse_name}'"
            )
        )
        self.stdout.write("\nProducts with existing stock:")
        for product_data, product in products_in_warehouse[:5]:  # Show first 5
            self.stdout.write(f"  - {product_data.product_code}: {product.name}")

        if len(products_in_warehouse) > 5:
            self.stdout.write(f"  ... and {len(products_in_warehouse) - 5} more")

        self.stdout.write("\nHow should stock quantities be updated?")
        self.stdout.write(
            "\n[1] REPLACE - Overwrite existing quantities (reconciliation)"
        )
        self.stdout.write("    Example: DB has 10 units, Excel has 3 → Result: 3 units")
        self.stdout.write("\n[2] ADD - Add to existing quantities (restocking)")
        self.stdout.write(
            "    Example: DB has 10 units, Excel has 3 → Result: 13 units"
        )

        while True:
            choice = input("\nSelect option [1/2]: ").strip()

            if choice == "1":
                self.stdout.write(
                    self.style.WARNING(
                        "✓ REPLACE mode: Existing quantities will be OVERWRITTEN"
                    )
                )
                return "replace"
            if choice == "2":
                self.stdout.write(
                    self.style.SUCCESS(
                        "✓ ADD mode: Quantities will be added to existing"
                    )
                )
                return "add"
            self.stdout.write(self.style.ERROR("Please enter 1 or 2"))

    def _prompt_price_interpretation_confirmation(self) -> None:
        """Prompt user to confirm price interpretation.

        Raises:
            CommandError: If user rejects confirmation

        """
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(self.style.WARNING("⚠ Price Interpretation Confirmation"))
        self.stdout.write("\nPlease confirm your understanding:")
        self.stdout.write("  1. The 'Price' column contains the SALE PRICE")
        self.stdout.write("     (the amount customers will pay)")
        self.stdout.write("  2. Prices do NOT include VAT")
        self.stdout.write("     (VAT will be added at checkout)")

        response = input("\nI understand and confirm [Y/n]: ").strip().lower()

        if response not in ["y", "yes", ""]:
            raise CommandError("Price interpretation not confirmed. Aborting.")

        self.stdout.write(self.style.SUCCESS("✓ Price interpretation confirmed"))

    def _prompt_header_row(self, excel_file: str, sheet_name: str) -> int:
        """Show first 5 rows and prompt user for header row number.

        Args:
            excel_file: Path to Excel file
            sheet_name: Sheet name to read

        Returns:
            Header row number (0-indexed)

        Raises:
            CommandError: If user provides invalid input

        """
        import pandas as pd

        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(self.style.WARNING("⚠ Header Row Detection"))
        self.stdout.write(f"\nShowing first 5 rows of '{sheet_name}':\n")

        # Read without header to show raw rows
        df_preview = pd.read_excel(
            excel_file, sheet_name=sheet_name, header=None, nrows=5
        )

        # Display rows with row numbers
        for idx, row in df_preview.iterrows():
            row_values = " | ".join(
                str(val)[:30] for val in row.values[:5]
            )  # First 5 columns, truncate long values
            self.stdout.write(f"  Row {idx}: {row_values}")

        self.stdout.write("\nWhich row contains the column headers?")
        self.stdout.write(
            "(Usually row 0, but if you have title rows it might be row 1 or 2)"
        )

        while True:
            header_input = input("\nEnter header row number [0]: ").strip()

            if not header_input:
                # Default to 0
                self.stdout.write(self.style.SUCCESS("✓ Using row 0 as header"))
                return 0

            try:
                header_row = int(header_input)
                if header_row < 0:
                    self.stdout.write(
                        self.style.ERROR("Row number must be 0 or greater")
                    )
                    continue

                self.stdout.write(
                    self.style.SUCCESS(f"✓ Using row {header_row} as header")
                )
                return header_row

            except ValueError:
                self.stdout.write(self.style.ERROR("Please enter a valid integer"))

    def _prompt_column_mapping(
        self, available_columns: list[str]
    ) -> SpreadsheetColumnMapping:
        """Prompt user to map Excel columns to expected fields.

        Args:
            available_columns: List of column names found in Excel

        Returns:
            SpreadsheetColumnMapping with user's mappings

        Raises:
            CommandError: If user cancels

        """
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(self.style.WARNING("⚠ Column Mapping Required"))
        self.stdout.write(f"\nFound {len(available_columns)} columns in Excel:")
        for idx, col in enumerate(available_columns, 1):
            self.stdout.write(f"  {idx}. {col}")

        self.stdout.write(
            "\nPlease map the following required fields to your Excel columns:"
        )
        self.stdout.write("(Enter column number OR exact column name)\n")

        def prompt_for_column(field_name: str, required: bool = True) -> str | None:
            """Prompt for a single column mapping."""
            required_str = (
                "(required)" if required else "(optional, press Enter to skip)"
            )
            while True:
                response = input(f"{field_name} {required_str}: ").strip()

                if not response and not required:
                    return None

                if not response and required:
                    self.stdout.write(self.style.ERROR(f"{field_name} is required"))
                    continue

                # Try as number first (1-indexed)
                try:
                    col_num = int(response)
                    if 1 <= col_num <= len(available_columns):
                        selected_col = available_columns[col_num - 1]
                        self.stdout.write(
                            self.style.SUCCESS(f"  → Using column: {selected_col}")
                        )
                        return selected_col
                    self.stdout.write(
                        self.style.ERROR(
                            f"Number must be between 1 and {len(available_columns)}"
                        )
                    )
                    continue
                except ValueError:
                    # Not a number, try exact column name match
                    if response in available_columns:
                        return response
                    self.stdout.write(
                        self.style.ERROR(
                            f"Column '{response}' not found. Enter a number (1-{len(available_columns)}) or exact column name."
                        )
                    )

        # Prompt for each field
        code = prompt_for_column("Product Code")
        brand = prompt_for_column("Brand")
        description = prompt_for_column("Description")
        category = prompt_for_column("Category")
        sizes = prompt_for_column("Sizes")
        rrp = prompt_for_column("RRP", required=False)
        price = prompt_for_column("Price")
        weight = prompt_for_column("Weight", required=False)
        image = prompt_for_column("Image", required=False)

        mapping = SpreadsheetColumnMapping(
            code=code,
            brand=brand,
            description=description,
            category=category,
            sizes=sizes,
            rrp=rrp,
            price=price,
            weight=weight,
            image=image,
        )

        self.stdout.write(self.style.SUCCESS("\n✓ Column mapping configured"))
        return mapping

    def _display_results(self, result: IngestionResult) -> None:
        """Display ingestion results to user.

        Args:
            result: IngestionResult with statistics

        """
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write(self.style.SUCCESS("✓ INGESTION COMPLETE"))
        self.stdout.write(f"{'=' * 60}")

        self.stdout.write(f"\nWarehouse: {result.warehouse.name}")
        self.stdout.write(
            f"Total Products Processed: {result.total_products_processed}"
        )

        if result.created_products:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Created {len(result.created_products)} new product(s) "
                    f"({result.total_variants_created} variants)"
                )
            )

        if result.updated_products:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n✓ Updated {len(result.updated_products)} existing product(s) "
                    f"({result.total_variants_updated} variants)"
                )
            )

        if result.skipped_products:
            self.stdout.write(
                self.style.WARNING(f"\n⚠ Skipped {result.skipped_products} product(s)")
            )

        self.stdout.write(f"\n{'=' * 60}")
