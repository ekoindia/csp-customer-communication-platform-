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

## Update (every time after)
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
