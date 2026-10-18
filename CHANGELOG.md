# Changelog

## 0.2.0 — Frontier (breaking)

Replace the event-sourced worklog with one bounded current snapshot. Add GLOBAL
historical slots 1/2/3, card-level CAS, in-place updates, explicit removal on finish,
fixed temp publication, bounded disposable Hook flags, and offline destructive
migration. Remove event replay from normal operations; remove session/attempt/
checkpoint/handoff/evidence archives, artifacts, verifier, job registry, leases,
heartbeat launcher, and idempotency history. Context reads no longer write events,
and context no longer loses active cards via tail truncation. Reinstall adapters.

## 0.1.0

Initial immutable-ledger implementation. Superseded for the frontier-only use case;
not included as a runtime compatibility mode.
