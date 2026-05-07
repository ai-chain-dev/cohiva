# Reverse Roulette → Cohiva integration

Automated link from the **Reverse Roulette** charity-betting smart contracts
to the **Cohiva** cooperative software:

1. The Reverse Roulette **user frontend** captures the donor's WhatsApp
   phone number *before* the bet is signed and POSTs a *bet intent*
   `(wallet, phone, intended_amount, charity, contract)` to the admin
   backend.
2. The admin backend's existing **`/api/bets/sync` indexer** fetches
   `BetPlaced` / `SponsoredBetPlaced` events from the Ronin chain.  When
   the charity address matches a configured Cohiva charity wallet, the
   indexer reconciles the event with the matching `bet_intents` row
   (wallet ∈ {player, sponsor, fundraiser} and intended amount and time
   window) and writes a `cohiva_bet_links` row carrying the donor phone
   number.
3. **This Django app** polls Supabase periodically (Celery beat,
   every 5 minutes), matches the phone against existing
   `geno.Member.name` addresses (across all four phone fields *and* an
   optional historic-phone `MemberAttributeType`), creates a new member
   if none matches, and books a `geno.Share` of the configured
   `ShareType` for the donation amount.

## Settings

Configured in `cohiva/settings_defaults.py` (override per deployment):

| Setting | Purpose |
| --- | --- |
| `REVERSE_ROULETTE_SUPABASE_URL` | Supabase REST endpoint, e.g. `https://xxx.supabase.co` |
| `REVERSE_ROULETTE_SUPABASE_SERVICE_ROLE_KEY` | Service-role key (server-side only) |
| `REVERSE_ROULETTE_COHIVA_CHARITY_ADDRESS` | The cooperative's charity wallet (lowercase 0x…) |
| `REVERSE_ROULETTE_TOKEN_DECIMALS` | dict `{contract_address.lower(): decimals}`. Native RON = 18, USDC = 6. |
| `REVERSE_ROULETTE_DEFAULT_DECIMALS` | Fallback decimals (default 18) |
| `REVERSE_ROULETTE_MIN_CONFIRMATIONS` | Block confirmations before processing (default 12) |
| `REVERSE_ROULETTE_SHARETYPE_ID` | PK of the dedicated `geno.ShareType` |
| `REVERSE_ROULETTE_HISTORIC_PHONE_ATTR_ID` | PK of an optional `MemberAttributeType` carrying historic phone numbers |
| `REVERSE_ROULETTE_ENABLE_ACCOUNTING_BOOKING` | Off by default; enable once income/equity prefixes are configured |
| `REVERSE_ROULETTE_INCOME_ACCOUNT_PREFIX` | GnuCash/CashCtrl account prefix for donation income |
| `REVERSE_ROULETTE_EQUITY_ACCOUNT_PREFIX` | GnuCash/CashCtrl account prefix for member-share equity |

Also add `'reverse_roulette'` to `cbc.FEATURES` in your
`cohiva_base_config.py` to enable the app and its periodic Celery task.

## Operational commands

- One-shot manual sync (idempotent):
  ```bash
  python manage.py sync_reverse_roulette_donations
  ```
- Dry-run (upserts `RouletteDonation` rows but does not create members
  or shares):
  ```bash
  python manage.py sync_reverse_roulette_donations --dry-run
  ```
- Periodic Celery: `reverse_roulette.sync_donations` runs every 5 minutes
  (configured by the beat schedule when the feature is enabled).

## Status semantics

`RouletteDonation.status`:

| Status | Meaning |
| --- | --- |
| `pending` | Pulled from Supabase but not yet processed |
| `matched` | Phone matched a single existing member; share booked |
| `created` | New member created from the phone; share booked |
| `unattributed` | Donation has no phone; manual triage in admin |
| `ambiguous` | Phone matched > 1 member; admin must pick one and re-run the *“Re-process”* admin action |
| `error` | Unexpected failure; check `error_message` |
| `skipped` | Zero donation amount (e.g. all-loss configurations) |

## Tests

```bash
python manage.py test reverse_roulette
```

Tests are fully offline: Supabase calls are mocked; on-chain data is
stubbed via crafted payloads.
