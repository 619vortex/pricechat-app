# Building installers on GitHub Actions

**Set up 2026-11-07.** The build runs in the private repo `619vortex/KIWIVPN` (the iPhone project is at its root; Windows is in `windows-client/`). Workflow: `ci/kiwi-windows.yml`, copied to `.github/workflows/kiwi-windows.yml`. Push with `sync-ci-repo.ps1` (main PC, compiles nothing). It runs on every push to `main` that touches `windows-client/`, and by hand. It runs the React tests and type check, the service, client and app Rust tests, builds the installer, and keeps it as a private artifact for 14 days (`KiwiVPN-<version>-<commit>-unsigned-Setup.exe` with its SHA-256 and BUILD-INFO). It publishes nothing and uses no secrets. Every action is pinned to a commit; WireGuard's DLLs are checked by hash and signature. The installer is unsigned: Smart App Control blocks it until it's signed.

## Earlier plan (kept for the reasoning)

**It works in principle and would replace the VM for compiling. It wouldn't replace a real connection test or fix the Smart App Control block.** Nothing is set up: the repo has no GitHub remote, and creating one needs the owner's account. The draft workflow is `ci/windows-installer.yml`, and it has never run.

## What it changes
| | VM today | GitHub Actions |
|---|---|---|
| Compiles Rust and Tauri | Owner's VM | GitHub's Windows machine |
| Smart App Control on the main PC | Not involved | Not involved |
| Getting the installer | Copy from the VM | Download the build's artifact and run `receive-installer.ps1` |
| Installer signed | No | **No**, until a certificate is added |
| Blocked on a customer's PC with Smart App Control on | Yes | **Yes, the same** |
| Install, sign in, connect, leak tests | In the VM | **Still needs a real Windows machine**, never the main PC |

## Recommended setup
1. **A new private repo with only `windows-client/`**, not the whole monorepo, so a build machine never holds the trading, backend or admin code. `git subtree split --prefix=windows-client` keeps its history.
2. **Scan first:** a filename check of that history found nothing like a key or `.env`. Run a content scan such as gitleaks before the first push.
3. **Run by hand only,** with read-only permissions and no secrets. It only produces an artifact. Publishing stays a separate, deliberate step.
4. **`dist/` is git-ignored,** so the workflow builds `tunnel.dll` from the pinned WireGuard commit and downloads `wireguard.dll`, checking its signature and hash. Before the first run, fill in `WG_NT_SHA256` and pin each action to a commit SHA.

## Cost
GitHub's documentation counts Windows minutes at double. Third-party pricing pages list 2,000 included minutes a month for private repos on the Free plan, which I couldn't confirm on GitHub's own pricing page. The default spending limit is $0, so going over stops builds rather than billing. A build's length is unknown until one runs.

## Signing is the separate problem
Smart App Control blocks unsigned files with no good reputation, and it has no per-app exception. So customers with it on can't install the app, wherever it's built. Azure Artifact Signing ($9.99 a month) lists individual developers in the US and Canada, and organizations in the US, Canada, EU and UK; one Microsoft Q&A answer says individual sign-up has paused, so check before paying. Signing with a certificate from another authority is the alternative, not yet looked into. Microsoft's Smart App Control page says that when its service can't make a prediction, an app signed with a certificate from a Trusted Root Program authority is allowed, and every code module has to be signed, not just the installer. SmartScreen, on PCs without Smart App Control, may still warn until reputation builds.
