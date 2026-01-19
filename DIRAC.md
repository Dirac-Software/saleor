

As we are self hosting this, several changes have had to be made.

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
