# GitHub setup

How to connect DEM to GitHub: a bot identity, a fine-grained token, per-project webhooks, and branch
protection. Do this once per deployment (plus one webhook per repo). It assumes you have already run
the setup wizard's Plane step and enabled at least one project with its repos mapped (see the
console's Projects page).

For the conductor-side config field reference, see `docs/PLAN.md`; this doc is the operator
walkthrough on the GitHub side.

---

## 1. Pick the bot identity: machine account vs. your own account

Agents commit, push branches, and open PRs under whatever identity owns the token. Both approaches
use a scoped fine-grained token (§2) — the difference is **how the "a human approves, the bot
doesn't" gate is enforced.** Pick one:

### Option A — dedicated machine account (recommended: *enforced* approval gate)

Create a separate GitHub account (e.g. `your-org-dem-bot`) and invite it as a collaborator with
**write** access to each target repo (write is enough — it pushes branches and opens PRs; never give
it admin or a protection bypass). Now the bot **authors** PRs and **you** approve/merge them. Because
the author and the approver are different identities, **branch protection can *require* an approving
review the bot literally cannot give** (§5) — GitHub enforces the human gate for you. You also get
clean attribution (bot activity is distinct from yours) and can revoke the account without touching
your own access.

### Option B — your own account (the "eyeball" method: *manual* gate)

Skip the second account and issue the token from your personal account. Simpler, nothing extra to
manage. The catch is the approval gate becomes **your discipline, not GitHub's enforcement**:
**GitHub won't let you approve your own pull request**, and here every PR is authored by *you* — so
required-approving-reviews can't be satisfied by you alone. You therefore rely on personally reading
each PR before clicking **Merge** yourself (see §5 for the protection settings that still apply). Fine
for a solo/hobby deployment, as long as you accept the trade: you're swapping an *enforced* guarantee
for a *manual* one. If you ever add a second human or want the gate to be un-bypassable, move to
Option A.

> The rest of this guide works for either option. Where it says "the bot" / "the machine account,"
> read it as "whichever identity owns the token."

---

## 2. Create a fine-grained personal access token

DEM authenticates to the GitHub REST API with a single token, stored encrypted and entered in the
wizard's GitHub step as **`GITHUB_TOKEN`**.

Sign in **as the identity you chose in §1** (the machine account for Option A, or yourself for
Option B) and go to **Settings → Developer settings → Personal access tokens → Fine-grained tokens →
Generate new token**.

- **Resource owner:** the account/org that owns the target repos.
- **Repository access:** *Only select repositories* → pick every repo any enabled project maps to.
  (You can also use *All repositories*, but least-privilege is preferred.)
- **Repository permissions:**
  | Permission      | Access         | Why                                              |
  | --------------- | -------------- | ------------------------------------------------ |
  | Contents        | Read and write | Push the `ticket/<id>` branch                    |
  | Pull requests   | Read and write | Open PRs, read review state                      |
  | Metadata        | Read-only      | Mandatory; also powers the wizard's repo picker  |

  You do **not** need Administration — webhooks are added by hand (§3), not via the API.
- **Expiration:** set a calendar reminder to rotate before it lapses; a dead token silently stops
  all pipeline work.

Paste the token into the wizard's GitHub step and run **Test connection**.

> **Repo-visibility caveat.** A fine-grained PAT only sees repositories it was **explicitly granted
> at creation time**. If a repo is missing from the wizard's live repo picker, the fix is to **edit
> the token's repository access on GitHub** and add it — it is not a conductor bug and re-typing the
> `owner/name` by hand won't grant access either. After editing the grant, reopen the wizard so the
> picker refetches.

---

## 3. Choose delivery mode: webhook vs. poll

Set **`GITHUB_EVENT_MODE`** in the wizard's GitHub step.

| Mode                 | How it learns of PR activity                          | Use when                                                                 |
| -------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------ |
| **`webhook`** (default) | GitHub POSTs each event to `/webhooks/github` in real time | The conductor has a **public URL** (or a tunnel — see §6). Lowest latency, no wasted calls. |
| **`poll`**           | The conductor lists each mapped repo's PRs every `GITHUB_POLL_INTERVAL_SECONDS` (default 60) and reacts to state changes | No public URL and no tunnel. Simpler to stand up; costs one API sweep per interval and reacts within one interval, not instantly. |

Webhook is preferred for anything long-lived; poll is the escape hatch for a laptop or a private
network. You can switch modes any time — poll needs **no** GitHub-side webhook config, so if you
choose poll you can skip §4 entirely.

---

## 4. Add the webhook to each repo (webhook mode only)

Webhook **secrets are per Plane project, not per repo and not global** (a project owns many repos and
they share one secret — see CLAUDE.md deviation #7). So: generate one secret per project in the
wizard, then configure **every repo in that project** with the **same** secret and the **same**
payload URL.

**In the wizard (once per enabled project):**
1. Open the GitHub step. Each enabled project has its own section.
2. Click **Generate secret** (or paste your own) and save. This is the project's shared webhook
   secret.
3. Copy the **payload URL** shown there — it is `https://<your-conductor-domain>/webhooks/github`
   (the wizard derives the host from how you reached it).

**In GitHub, for each repository mapped under that project** (repeat per repo):
1. Repo **Settings → Webhooks → Add webhook**.
2. **Payload URL:** the URL copied above.
3. **Content type:** `application/json` — **required**; the signature is computed over the raw JSON
   body and the handler only parses JSON.
4. **Secret:** the project's shared secret from step 2. Every repo in the same project uses the
   *same* value.
5. **SSL verification:** **Enabled**. Leave it on; disabling it defeats the point of HTTPS.
6. **Which events?** → *Let me select individual events*, and tick exactly these four:
   - **Pull requests** (`pull_request`)
   - **Pull request reviews** (`pull_request_review`)
   - **Pull request review comments** (`pull_request_review_comment`)
   - **Pull request review threads** (`pull_request_review_thread`)

   Untick everything else. DEM acknowledges and ignores any other event type, but sending them just
   wastes deliveries.
7. **Add webhook**, then check **Recent Deliveries** — GitHub's initial `ping` (and every real
   delivery) should show a `2xx`. A `401` means the secret doesn't match the project's stored secret;
   a `401` on a repo you *did* map can also mean the repo isn't actually saved under this project —
   recheck the Projects page.

### How verification works (and why the order matters)

The handler can't trust the repo name until *after* it verifies the signature, but it needs to know
which project's secret to verify *with* — a chicken-and-egg the multi-tenant-webhook pattern solves
by **lookup-before-verify**:

1. Read `repository.full_name` from the (still-unverified) body.
2. Find which project maps that repo, and load **that project's** secret.
3. HMAC-SHA256 the raw body with that secret and constant-time compare it to `X-Hub-Signature-256`.
4. Only a match is processed; a missing/unknown repo, a missing signature, or a mismatch → **401**.

This bounds the blast radius of a leaked secret to the repos a human already grouped under one
project, instead of every repo the conductor serves.

---

## 5. Branch protection — the approval gate

**DEM never auto-merges.** The pipeline takes a ticket all the way to `ready_for_approval` and opens
a PR, then stops. A human does the merge. How strongly that's *enforced* depends on the identity you
chose in §1 — agent prompts are never the guarantee (they can be ignored or jailbroken); branch
protection is.

On each target repo: **Settings → Branches → Add branch ruleset** (or *Branch protection rule*) for
the base branch (`main`, or whatever you mapped).

**Option A (machine account) — enforced gate:**

- ✅ **Require a pull request before merging** — and **Require approvals** (≥ 1). The bot authors the
  PR, so it can't supply this approval — you do.
- ✅ **Do not allow bypassing the above settings** — this is what stops the bot from merging its own
  PR. Confirm the machine account is **not** in any bypass/allow list.
- ✅ (Recommended) **Require status checks to pass** if the repo runs CI.
- Keep the machine account's access at **write**, never admin, so it cannot edit the ruleset.

Net effect: the bot can open and update a PR but *physically cannot* merge it.

**Option B (your own account) — manual gate:** because you authored the PR, GitHub won't let you
approve it, so **Require approvals** can't be satisfied by you and would just block you. Instead:

- ✅ **Require a pull request before merging** (keeps work off the base branch and forces the PR view
  where you review the diff) — but leave **Require approvals** off, or you'll be unable to merge.
- ✅ (Recommended) **Require status checks to pass** if the repo runs CI — this part *is* still
  enforced regardless of identity.
- The gate is now **you reading the diff before clicking Merge**. Nothing GitHub-side stops a
  bad merge, so this rests on your discipline (the trade you accepted in §1).

Either way, merging is your second (and final) human touchpoint.

---

## 6. No public URL? Use a tunnel (or poll)

Webhook mode needs GitHub to reach the conductor over the public internet. If you're running behind
NAT / on a laptop / on a private network and don't want to expose a port, put a tunnel in front of
`/webhooks/github`:

- **Cloudflare Tunnel** (`cloudflared`) — maps a public hostname to the local conductor with no
  inbound firewall change; pairs naturally with Cloudflare Access if you already front the console
  with it.
- **Tailscale Funnel** — exposes a single HTTPS path from your tailnet to the public internet;
  minimal setup if you already run Tailscale.

Point the webhook payload URL at the tunnel's public hostname (`https://<tunnel-host>/webhooks/github`).
If a tunnel isn't an option, use **`poll` mode** (§3) — it needs no inbound connectivity at all.

---

## 7. Acceptance check

Once configured, confirm the integration end to end (this is the Phase 3 acceptance test from
`docs/PLAN.md`):

- The wizard shows a checkbox per Plane project; enabling one reveals a GitHub section that accepts
  2+ repos from the live-fetched picker plus a generated per-project secret.
- The mapping round-trips through a **targets.yml** export → import (Config page).
- **Webhook mode:** deliveries of all four subscribed events are accepted (`2xx`), while an unsigned
  or wrong-secret delivery is rejected **401**; a redelivery of the same event is deduped (not
  double-processed).
- **Poll mode:** open a PR by hand on a tracked repo and confirm the conductor notices the state
  change within one poll interval.
- A merged PR triggers the cleanup job.
