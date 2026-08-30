# PMDG 737 NG3 SDK Reference

The PMDG 737 NG3 uses the same SimConnect Client Data Area architecture as the
PMDG 777, just with NG3-specific identifiers. See `pmdg_777.md` for a deep
dive on the underlying mechanism; this page documents the NG3 differences.

## Data Channels

| Channel | Name | Direction | Purpose |
|---------|------|-----------|---------|
| **Data** | `PMDG_NG3_Data` | Read | Aircraft state (switches, annunciators, displays, sensors) |
| **Control** | `PMDG_NG3_Control` | Write | Send commands (events with optional parameter) |
| **CDU** | `PMDG_NG3_CDU_0/1` | Read | CDU screen content (24×14 grid). NG3 has **two** CDUs (Capt + F/O), not three. |

The data struct (`PMDG_NG3_Data` in `PMDG_NG3_SDK.h`) has ~365 raw fields. The
event ID space starts at `THIRD_PARTY_EVENT_ID_MIN = 0x00011000`, identical to
the 777.

## Enabling Data Broadcast

Add to `737NG3_Options.ini`:

```ini
[SDK]
EnableDataBroadcast=1
EnableCDUBroadcast.0=1   ; left CDU (Captain)
EnableCDUBroadcast.1=1   ; right CDU (F/O)
```

## Event Dispatch

Identical to the 777: standard events use `ROTOR_BRAKE` with `offset*100 + 1`;
direct-set events (offsets ≥ 14500) use the Control data area.

NG3-specific direct-set events:

| Event | Offset | Notes |
|-------|--------|-------|
| `EVT_MCP_CRS_L_SET` | 14500 | Captain's course |
| `EVT_MCP_CRS_R_SET` | 14501 | F/O's course |
| `EVT_MCP_IAS_SET` | 14502 | IAS (when IAS active) |
| `EVT_MCP_MACH_SET` | 14503 | MACH × 100 (send 78 → M0.78) |
| `EVT_MCP_HDG_SET` | 14504 | Heading; shortest turn |
| `EVT_MCP_ALT_SET` | 14505 | Altitude |
| `EVT_MCP_VS_SET` | 14506 | VS = parameter − 10000 (send 8200 → −1800 fpm) |
| `EVT_OH_PRESS_FLT_ALT_SET` | 14507 | NG3-only: pressurization FLT ALT |
| `EVT_OH_PRESS_LAND_ALT_SET` | 14508 | NG3-only: pressurization LAND ALT |

## CDU Symbol Set

The NG3 CDU character set adds up/down arrows the 777 does not have:

| Code | Char | Meaning |
|------|------|---------|
| `0xA1` | ← | Left arrow |
| `0xA2` | → | Right arrow |
| `0xA3` | ↑ | Up arrow (NG3-specific) |
| `0xA4` | ↓ | Down arrow (NG3-specific) |

`render_cdu_text` and `render_cdu_grid` in `pmdg_ng3.py` handle all four.

## Auto-Detection

`msfs_get_pmdg_var`, `msfs_get_pmdg_cdu`, and `msfs_send_pmdg_event` auto-detect the loaded
aircraft from the `TITLE` and `ATC_MODEL` SimVars (some liveries carry PMDG
branding in `ATC_MODEL` while `TITLE` is terse, e.g. a freighter's `TITLE` is
just "777F"). Pass `variant="pmdg_737"` to force the NG3 SDK, or
`variant="pmdg_777"` for the 777. When TITLE/ATC_MODEL detection fails, each
SDK's client data area is probed directly -- only the actually-loaded variant
ever responds, which is authoritative even when the title/model carry no PMDG
branding at all (live-verified: a PMDG 737-600 reports TITLE='737-600 PAX TC',
matching no catalog's pattern). If the probe also finds nothing responding,
the event/var name is looked up in both catalogs and the first match wins; if
that also fails, the 777 catalog is used as a last-resort default. Every
response carries a `variant_source` field (`"explicit"`, `"detected"`,
`"probed"`, `"name_match"`, or `"fallback"`) so a caller can tell a real
detection from a guess.

## Regenerating the Catalog

```
python scripts/parse_pmdg_sdk.py \
    "C:/Users/<you>/AppData/Local/Packages/Microsoft.Limitless_*/LocalCache/Packages/Community/pmdg-aircraft-738/Documentation/SDK/PMDG_NG3_SDK.h" \
    --aircraft-name "PMDG 737" --title-pattern "PMDG 737" \
    -o src/simconnect_mcp/data/pmdg_737.json
```

The parser auto-detects the struct name and CDU count from the header, so the
same script handles both 777 and NG3 SDKs.
