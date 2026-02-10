from collections import defaultdict

from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import Count

from ....order import OrderStatus
from ....order.actions import create_fulfillments
from ....order.models import Order
from ....plugins.manager import get_plugins_manager
from ....warehouse.management import can_confirm_order
from ....warehouse.models import Allocation


class Command(BaseCommand):
    help = (
        "Create WAITING_FOR_APPROVAL fulfillments for UNFULFILLED orders that lack them"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without actually creating fulfillments",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN MODE - No fulfillments will be created")
            )

        # Find UNFULFILLED orders without fulfillments
        orders_without_fulfillments = (
            Order.objects.filter(
                status=OrderStatus.UNFULFILLED,
            )
            .annotate(fulfillment_count=Count("fulfillments"))
            .filter(fulfillment_count=0)
        )

        orders_count = orders_without_fulfillments.count()
        self.stdout.write(
            f"Found {orders_count} UNFULFILLED orders without fulfillments"
        )

        if orders_count == 0:
            self.stdout.write(self.style.SUCCESS("✓ No missing fulfillments found"))
            return

        created_count = 0
        skipped_count = 0

        for order in orders_without_fulfillments:
            # Verify order still meets criteria (has allocations with sources)
            if not can_confirm_order(order):
                self.stdout.write(
                    self.style.WARNING(
                        f"  Skipping order {order.number} - not all allocations have sources"
                    )
                )
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"  Would create fulfillments for order {order.number}"
                )
                created_count += 1
            else:
                try:
                    # Get allocations and group by warehouse
                    allocations = Allocation.objects.filter(
                        order_line__order=order
                    ).select_related("stock__warehouse", "order_line")

                    if not allocations.exists():
                        self.stdout.write(
                            self.style.WARNING(
                                f"  Skipping order {order.number} - no allocations"
                            )
                        )
                        skipped_count += 1
                        continue

                    warehouse_groups = defaultdict(list)
                    for allocation in allocations:
                        warehouse_pk = allocation.stock.warehouse_id
                        warehouse_groups[warehouse_pk].append(allocation)

                    # Build fulfillment_lines_for_warehouses dict
                    fulfillment_lines_for_warehouses = {}
                    for warehouse_pk, allocations_list in warehouse_groups.items():
                        lines = []
                        for allocation in allocations_list:
                            lines.append(
                                {
                                    "order_line": allocation.order_line,
                                    "quantity": allocation.quantity_allocated,
                                }
                            )
                        fulfillment_lines_for_warehouses[warehouse_pk] = lines

                    # Create fulfillments
                    manager = get_plugins_manager(allow_replica=False)
                    site_settings = Site.objects.get_current().settings

                    fulfillments = create_fulfillments(
                        user=None,
                        app=None,
                        order=order,
                        fulfillment_lines_for_warehouses=fulfillment_lines_for_warehouses,
                        manager=manager,
                        site_settings=site_settings,
                        notify_customer=False,
                        auto_approved=False,
                        tracking_url="",
                    )

                    created_count += len(fulfillments)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Created {len(fulfillments)} fulfillments for order {order.number}"
                        )
                    )
                except ValueError as e:
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ Error for order {order.number}: {e}")
                    )
                    skipped_count += 1

        self.stdout.write("")
        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY RUN: Would create {created_count} fulfillments"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"✓ Created {created_count} fulfillments total")
            )

        if skipped_count > 0:
            self.stdout.write(self.style.WARNING(f"⚠ Skipped {skipped_count} orders"))
