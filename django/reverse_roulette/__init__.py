"""Reverse Roulette → Cohiva member-share auto-link integration.

Polls Supabase for confirmed on-chain donations to a configured Cohiva
charity, matches the donor's phone number against existing members
(creating one if needed), and records the donation as cooperative shares.
"""

default_app_config = "reverse_roulette.apps.ReverseRouletteConfig"
