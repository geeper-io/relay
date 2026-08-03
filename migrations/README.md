# Relay database migrations

Run migrations through Relay's compatibility wrapper:

```bash
python -m app.db.migrate upgrade
```

The wrapper serializes concurrent upgrades and recognizes databases created by older Relay releases with
`Base.metadata.create_all()`. Direct `alembic upgrade head` is safe for fresh or already-versioned databases, but it
does not perform that legacy bootstrap.
