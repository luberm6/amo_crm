# GitHub Push Checklist

Before pushing this repository:

1. Confirm these are not tracked:
   - `.env`
   - `admin-panel/.env.local`
   - `.venv/`
   - `node_modules/`
   - `admin-panel/dist/`
2. Confirm there are no real API keys in:
   - `.env.example`
   - `.env.local.example`
   - `.env.production.example`
   - `render.yaml`
3. Confirm local-only artifacts are ignored:
   - logs
   - caches
   - pytest artifacts
4. Run a quick smoke pass:

```bash
python3 -m pytest -q tests/test_env_config.py tests/test_provider_settings_api.py tests/test_admin_auth.py
cd admin-panel && npm test && npm run build
```

5. Re-read [render_deploy.md](/Users/iluxa/Amo_crm/docs/render_deploy.md) before connecting the repo to Render.
