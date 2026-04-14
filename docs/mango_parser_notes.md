# Mango Parser Notes

Date: 2026-04-14

## Live Payload Deviation Observed

The live Mango tenant did not return flat extension records for `POST /config/users/request`.

Instead, the payload shape was nested:

```json
{
  "users": [
    {
      "general": {
        "name": "..."
      },
      "telephony": {
        "extension": "10",
        "outgoingline": "79585382099",
        "numbers": [...]
      }
    }
  ]
}
```

## Parser Adjustments Applied

### `app/integrations/telephony/mango_client.py`

`list_extensions()` now reads:

- `general.name` as `display_name`
- `telephony.extension` as `extension`
- `telephony.outgoingline` as `line_phone_number`

Fallbacks to older flat fields were preserved so existing tests and simpler tenants do not regress.

### `app/services/mango_telephony_service.py`

`sync_lines()` now always refreshes:

- `line.phone_number = remote.phone_number`

This ensures existing line rows are upgraded to normalized `+7...` numbers during a later sync instead of retaining stale non-normalized values.

## Operational Note

The Mango tenant/API rate-limited repeated `config/users/request` calls during the same probe window.

Observed impact:

- direct first call: `200`
- repeated calls: `429`

This is why line binding must remain functional even when extension enrichment is temporarily unavailable.

