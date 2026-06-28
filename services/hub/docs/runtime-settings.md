# Runtime settings overview

## Goal
Runtime settings are editable without restarting containers. Changes are stored in Postgres, cached in Redis, and read by the API and worker on demand.

## Data flow
1. Client/admin calls the settings update endpoint.
2. API writes the new values to Postgres.
3. API also writes the same values to Redis hash cache.
4. Workers and API read settings from Redis on each request/job. If Redis has no settings for that user, they are loaded from Postgres once and cached.

## Defaults and overrides
- Defaults come from env and manifest.
- Per-user overrides are stored in Postgres and Redis.
- If a user has no overrides, env defaults are used.

## Explicit over Implicit (Fail Fast)
Sistemde "sessiz varsayılan" (silent fallback) mantığı kaldırılmıştır. 
- Eğer bir işlem için ayar override'ı gerekiyorsa, `user_id` **açıkça** belirtilmelidir.
- Client `user_id` göndermezse, sistem otomatik olarak `global` kullanıcısına geçmek yerine hata (HTTP 400/422) döndürür.
- `user_id` parametresi `None` gelirse, sistem **sadece** `.env` dosyasındaki statik varsayılanları kullanır, hiçbir DB/Redis override'ı uygulanmaz.

## Redis keys
- Prefix: `orion:settings:`
- Example: `orion:settings:alice`
- Global/Sistem geneli ayarlar için `global` user id'si kullanılır.

## Postgres table
Table: `orion_settings`

Columns:
- `user_id` (text)
- `key` (text)
- `value` (text)
- `updated_at` (timestamptz)

Primary key: `(user_id, key)`

## Update endpoint
`POST /api/v1/admin/settings`

Body:
```json
{
  "user_id": "alice",
  "values": {
    "LLAMA_MODEL": "gemma-2",
    "LLAMA_SYSTEM_PROMPT": "You are Orion."
  }
}
```

Headers:
- `X-Admin-Key`: required when `ADMIN_API_KEY` is set.

## Startup behavior
- On startup, no special migration is required.
- Settings are loaded lazily: first request for a user populates Redis from Postgres.

## Notes
- Env values like host/port/URL are not stored in DB.
- Change propagation is immediate after the update call.
- If Redis is flushed, settings are rehydrated from Postgres on next access.
