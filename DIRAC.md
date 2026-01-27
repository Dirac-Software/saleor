

As we are self hosting this, several changes have had to be made.

# Dropshipping
TODO: Mark warehouses as non-Dirac IN CODE, update the shipping app.

We will not own lots of the stock we sell. We use **Warehouses** to account for this. On reception of a new deal sheet from a new supplier we will want to process the stock and potentially put it on the website BEFORE it arrives in the Dirac Warehouse (perhaps one day this will be _one_ of the Dirac warehouses). We will do this by adding the products to a new warehouse. This reflects reality, we don't own the stock so it is in fact in a different warehouse, and as such it is easy to let our code reflect this:
- If some products for an online order are not in a Dirac warehouse the shipping calculator should handle this and extend the time to deliver.
- We should apply different ingest rules to a non-Dirac warehouse and a Dirac warehouse: Updating products for a non-Dirac warehouse typically means replacing, whereas at Dirac it normally means adding.

CLEARLY it is integral not to leak the warehouse that stock has come from to the frontend and this should be checked - the warehouses are our customer list.

The complexity is in these questions:
- What happens when we purchase stock from a supplier and as such the physical goods move from the non-Dirac warehouse to the Dirac warehouse?

## Non-Dirac -> Dirac
If a product line has already been sold (an order exists for it) then we should NOT move the stock to the Dirac warehouse. Although at present the stock will stop in at Dirac before being sent on it's way the products will not stay there, and if we keep the products as part of the warehouse they were part of we can be sure to prevent double-counting.
If we purchase a product line that has not already been sold on, then it SHOULD be moved to the Dirac warehouse and will live there. There is at present no built in support for this, but it is pretty simple.

## Accounting
We will NOT be including the buy price in Saleor product data at the moment. We don't actually know buy price of goods we haven't purchased as we may be able to get reduced prices at certain quantities and factoring in shipping can lead to changes known only _after_ we list the stock. We should do our acconting when we receive invoices for stock. We will know the cost of shipping and the cost of goods and tarriffs.

## Dirac Warehouse Functionality
It will be possible to add some functionality for all _dirac_ warehouses so that we can store where each product lives. Let's kick this down the line until we have some products!

## Example Flow
1. We receive a deal sheet from a new/existing supplier
2. We use Claude + Dirac functionality to process RRP, convert sheets to the right form, remove unnecessary products.
3. We have a sheet that can be linearly mapped to:
```
 list[
	 Product{
	 product_code
	 description (name)
	 brand
	 quantities
	 sizes
	 price
	 RRP
	 currency (has to be GBP)
	 weight
	 image_url(s)
	}
]
Config {
	show_on_website
	warehouse_to_use
    ...
}
```
4. Follow the ingest products flow on the dashboard.
IF new supplier:
    - Add a new non-Dirac warehouse for this supplier
    - Mark this warehouse as serving our channel + all shipping zones
    - Allocate all products to this warehouse
IF existing supplier:
    - Allocate these products to the corresponding warehouse
** there are some subtleties to do with adding / updating stock, changing product prices on merge, see the code **
5. Mark as 'show on web' or not (whether this deal can be displayed
6. wait until we have enough interst/orders to procure the stock. These orders remain in the pending state and we update shipping terms to reflect that Non-Dirac goods take longer (TODO)
7. TODO: Ingest the invoice and fulfill all orders that can be fulfilled. Cancel / amend orders as necessary.
8. On arrival of goods at Dirac warehouse: NO CHANGE for the products to be sent in orders, just process and send ASAP. For goods we will hold, assign a SKU and move them from non-Dirac -> Dirac warehouse.




# Database
We don't want to run as postgres (or superuser). We therefore have a `SUPERUSER_DATABASE_URL` in our .env and the `pyproject.toml` includes:
```toml
migrate.shell = "DATABASE_URL=${SUPERUSER_DATABASE_URL:-$DATABASE_URL} python manage.py migrate"
migrate.help = "Run database migrations (uses SUPERUSER_DATABASE_URL if set, otherwise DATABASE_URL)"
```
This means migrations run as postgres, other code does not. There is a subtle problem that we must grant our non-postgres user the right permissions for our database, and we can't do this without running a migration in this database. We therefore have our own `dirac_ops` application which solely exists to grant the saleor user the correct permissions.

The `dirac_ops` app contains a single migration (`0001_grant_saleor_permissions.py`) that grants:
- USAGE on schema public
- ALL PRIVILEGES on all existing tables and sequences
- EXECUTE on all functions
- DEFAULT PRIVILEGES for future objects created by postgres

This migration runs as superuser (via SUPERUSER_DATABASE_URL) and automatically grants the saleor role access to all database objects.

# Deployment
I added a `compose.yaml` which specifies how we deploy, and a `.github/workflows/cd.yml` for our CD. We will use the existing CI.


# Apps
```bash
uv run python manage.py install_app https://my-app-is-hosted-here/ --activate
```
Then you can use the dashboard to configure.

An annoying thing: In order to use the dashboard to configure you must use either localhost OR https, but to run docker containers without network-mode host you can't use localhost and I often use the my-device.tail473fa.ts.net workaround to get to localhost. This is hard.

# Packs
For some product, the user is NOT able to choose the makeup of variants (almost always sizes). Instead we use [Hamilton's method](https://en.wikipedia.org/wiki/Mathematics_of_apportionment) to ensure even distribution across variants.
We expose `getPackAllocation` and `checkoutAddPack`. We use the `minimum-order-quantity` attribute on a product to ensure the minimum pack has been ordered.
Note the traditional add and update checkout graphQL endpoints are still exposed. To fix this we would need to add a config changeable through the dashboard for whether a channel uses packs or not. Since this requires migrations and code changes that will make merging future solear releases harder, we leave it.

# Media Files

## Security Model

Media files are split into **public** and **private** files with different serving mechanisms:

**Public Files (Served by miniserve on port 8080):**
- Product images, thumbnails, user avatars
- Category/collection backgrounds
- App branding
- Served directly without authentication for performance

**Private Files (Served by Django with authentication):**
- Export files (`/media/export_files/{id}/`) - Owner or staff only
- Invoices (`/media/invoices/{id}/`) - Order owner or staff only
- Webhook payloads - Never exposed via HTTP (internal use only)

## Implementation

**Miniserve Container** (`Dockerfile.media`):
- Lightweight Debian container with miniserve binary
- Only mounts public directories from `/media/`
- Private directories (`payloads/`, `export_files/`, `invoices/`) are excluded

**Django Views** (`saleor/media_views.py`):
- `serve_export_file()` - Checks user owns export or is staff
- `serve_invoice()` - Checks user owns order or is staff
- Both return 401 for unauthenticated, 403 for unauthorized

**URL Generation:**
- Modified `saleor/csv/notifications.py` to generate secure URLs via `reverse("serve-export-file")`
- Modified `saleor/invoice/models.py` to generate secure URLs via `reverse("serve-invoice")`
- Public files use `MEDIA_URL` pointing to miniserve (e.g., `http://domain:8080/`)

## Configuration

Set in `.env`:
```bash
MEDIA_URL=http://your-domain:8080/  # Points to miniserve for public files
DEBUG=False  # Django views work in production
```

Start services:
```bash
docker compose --profile prod up -d saleor media-server
```

## Testing

Run security tests:
```bash
pytest saleor/tests/test_media_views.py -v --reuse-db
```

Tests verify:
- Unauthenticated access returns 401
- Owners can access their files
- Non-owners get 403
- Staff can access all files
