# imd-compose

A page to compose, validate, pay for and follow identity.md swarm requests
(`job.open`, `launch.open`, `workflow.open`, `oracle.request` on api.imd.fun). Community tool, not
affiliated with the identity.md team.

It runs two ways:

- **Hosted (static)** — compose and check only. Open the page, write the request with the form or let
  your own agent write it, and copy the JSON. api.imd.fun refuses quotes and payments from web pages on
  other domains, so a hosted copy cannot send anything.
- **Local (full)** — the same page served by a tiny helper that proxies api.imd.fun:

  ```sh
  git clone https://github.com/pointbreak01/imd-compose
  cd imd-compose && python3 server.py          # http://127.0.0.1:8790   (PORT=… to change it)
  ```

  Standard library only, Python 3.9+. It listens on 127.0.0.1 only and refuses other Host names
  (no DNS rebinding); write routes also need a custom header.

## What the page does

- **The room**: the same wall of instruments and the same Pepe as imd-panel (art by imd.fun, used with
  permission). Every module is a skill of the network; the skills your request will run come forward and
  light up, the knowledge it attaches glows amber, and the phosphor screen types your request out as a ticket
  with the check, the quote and the payment. Pepe reacts to what you do, and does a trick when poked.
- **Start from what you want**: eight plain goals (a report, a tested contract, a deploy, contracts plus a
  site, a website, a code review, an image, a question for a panel). Pick one, describe it in one box, and
  the page builds a valid request; skills, steps and files stay under **Fine-tune**.
- **Let your own AI write it**: a ready prompt for Claude, Codex or any assistant. It lists every request
  type, field and limit and the skill catalog, and asks for one `{action, input}` JSON object. Paste the
  answer back and press **Use this answer**; the checks tell you what to fix.
- **Compose**: a form for every request type, examples taken from the docs, hand-editable JSON, and
  local validation of every documented limit (control plane 23659b86, 23 Sep 2026). Without the helper the
  skill catalog comes from a snapshot in the page.
- **Quote (free, local)**: `POST /requests/quote` with a fresh token; the control plane rebuilds the
  request and answers 422 with its problems if it will not run. Nothing is charged.
- **Pay (local)**: the browser wallet (MetaMask/Rabby, mainnet) signs the x402 Permit2 payment
  (0.5 IMD) and the EIP-712 approval of the quote. It first checks the balance, the Permit2 allowance, and
  that the challenge matches `/requests/capabilities`.
- **Orders (local)**: kept in `~/.config/imd-compose/orders.json` (0600) with the token that reads them
  back.
- **Network (local)**: jobs, workflows, oracle requests, skills. From a completed job: "use as input" or
  "continue from this repo".

Note: the quote approval is not in the docs. It is an EIP-712 `QuoteApproval` under the domain
`{name: "IdentityMD Paid Action", version: "1", chainId}`; its `paymentHash` is IMD's canonical hash
(SHA-256 of sorted-key compact JSON, the same rule that reproduces the challenge's `inputHash`) of the x402
payment payload exactly as the `PAYMENT-SIGNATURE` header decodes.
