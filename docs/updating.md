# Updating

```sh
docker compose pull && docker compose up -d
```

`DEXSTASH_VERSION` in `.env` pins a version (see [Configuration](configuration.md)). Each release on GitHub also carries the image as a file for servers that can't reach the registry, and a build from source updates with `git pull` and `--build`: [DEPLOY.md](../DEPLOY.md#updating) has both.

- **Everything the app keeps is in `data/`** (on a hosting service, the data folder or volume, often `/srv/data` or `/app/data`). `.env` and `data/` are kept by an update.
- **Updates only go forward.** On start, the app backs up the database into `data/backups/` and then updates its tables. An older version refuses to start on an updated database and says so in its log.
- **To go back,** stop the app, put the backup made before the update (`data/backups/…-upgrade.db`) in place of `data/dexstash.db`, then install the older version. [DEPLOY.md](../DEPLOY.md) has the commands. Restoring it from Account › Backups doesn't work for this, because the newer version updates it again.
- **Before a big update,** press Account › Backups › **Back up now** and **Download photos**, and keep both somewhere else.

[CHANGELOG.md](../CHANGELOG.md) lists what each version changes.
