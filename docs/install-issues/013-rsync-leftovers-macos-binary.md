---
title: rsync from a macOS dev machine drops macOS Mach-O binaries in the Linux repo checkout (`main`, `rustc`, `main.rs`)
issue: 013
status: workaround-applied
severity: low
area: install-docs, git
---

# rsync from a macOS dev machine leaves macOS Mach-O binaries at the repo root

## Symptom

After rsyncing the busibox repo from a macOS machine to the Ubuntu host, three untracked files appear at the repo root:

```
$ file main rustc main.rs
main:    Mach-O 64-bit arm64 executable, flags:<NOUNDEFS|DYLDLINK|TWOLEVEL|PIE|HAS_TLV_DESCRIPTORS>
rustc:   C source, ASCII text
main.rs: C source, ASCII text
```

None of these files belong in the repo. `main` is a macOS arm64 executable from some on-the-fly `cargo build` / `rustc` run on the source mac; `main.rs` and `rustc` are stray scratch files. They don't break anything (they're untracked and not referenced), but they show up in `git status` and confuse anyone auditing the repo state.

## Root cause

`.gitignore` doesn't cover these exact filenames, and the author/rsync source had them sitting at the repo root. `rsync -a` preserves them. They're harmless but noisy.

## Workaround

`rm /home/gabe/maigent-code/busibox/{main,main.rs,rustc}` — done.

## Proposed fix

Not much here. Either:

- Add `/main`, `/main.rs`, `/rustc` to `.gitignore` if these are common scratch names in day-to-day work on this repo, or
- Just document in a CONTRIBUTING / ops note: "before rsyncing, run `git status` at source and delete anything at repo root that isn't tracked."

Low priority; listed mainly so the next person doesn't waste five minutes wondering "is this a missing toolchain?"
