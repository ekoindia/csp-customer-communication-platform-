# Admin portal — deploy on the RAG server

The admin portal runs on the shared RAG server behind the existing nginx
(`/csp-admin/` → `127.0.0.1:7000`). These scripts make install/update one command
each. The code comes from the public GitHub repo, so **`git pull` is the update**.

**Data safety:** `admin.db` (issued API keys + CSP fleet data), `secret.key` and
`.env` are gitignored — they are **never** overwritten by an update.

## First install / migrate the existing directory
Lays down the current code into `/home/Prateek/csp_platform`, preserving the live
`admin.db`/`secret.key`/`.env`, backing the old dir up to `*.bak-<timestamp>`:

```bash
curl -sL https://raw.githubusercontent.com/ekoindia/csp-customer-communication-platform-/main/admin_dashboard/deploy/install_admin.sh | bash
```

## Fast auto-deploy (recommended — set up once, then never SSH to update)
Installs a cron job so the live portal **auto-tracks GitHub `main`**: every push
goes live within ~1–2 min, with no manual step. This is the fix for the portal
silently drifting behind GitHub (which is what left the old install/update pages
showing on the live server long after they were deleted in the code).

```bash
bash /home/Prateek/csp_platform/admin_dashboard/deploy/setup_autoupdate.sh
```

- Runs `auto_update_admin.sh` every 2 min (change with `ADMIN_AUTOUPDATE_MIN=1`).
- It only pulls + restarts when `main` actually moved — otherwise it's a silent
  no-op (`git fetch` only), so running it often is cheap.
- Log: `admin_dashboard/_autoupdate.log`. Turn off:
  `crontab -l | grep -v '# csp-admin-autoupdate' | crontab -`

## Update once, manually (if you prefer not to use cron)
```bash
bash /home/Prateek/csp_platform/admin_dashboard/deploy/update_admin.sh
```

## Restart only (no code change)
```bash
bash /home/Prateek/csp_platform/admin_dashboard/deploy/restart_admin.sh
```

Public URL: <http://122.176.147.78:8080/csp-admin/login>

> nginx is **not** touched by these scripts — the existing `/csp-admin/` proxy
> block keeps working. Only port 7000 (local) is (re)bound by the app.
