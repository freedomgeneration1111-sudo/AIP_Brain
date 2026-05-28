# AIP — AI Poiesis

**Product:** AI Poiesis (AIP) v0.1  
**Status:** Fresh start — Phase 1 build beginning

This repository contains the implementation of AI Poiesis (AIP) v0.1, built according to the authoritative build specifications in this repo (see `specs/`).

## Build Process

Work proceeds in strict, small, testable **CHUNK** units as defined in the Phase 1 BuildSpec. After each completed work unit (and all associated tests + quality gates passing), changes are committed and pushed so that downstream verification (including by GLM-5.1) can proceed immediately.

## Repository Structure (to be established)

```
aip/
├── specs/                 # Authoritative build specs (Phase 0, Phase 1, Architecture, etc.)
├── src/aip/               # Source (following Phase 0 layout assumptions)
├── config/
├── db/
├── tests/
└── ...
```

## Current Phase

See the latest BuildSpec in `specs/` for the active phase and chunk list.

---

*Initialized fresh. No prior code from other projects.*
