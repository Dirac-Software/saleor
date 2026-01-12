

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
