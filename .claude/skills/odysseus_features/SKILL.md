---
name: odysseus_features
description: Catalog of everything you can do with this Odysseus instance, the features most relevant to the owner's profile (senior DBA, father & son, bodybuilder/fat-loss, stock investor, entrepreneur, tech enthusiast, English learner), and the pending setup tasks to do when bringing it up on the Mac. Invoke to get oriented, see what's worth using, and what still needs configuring.
---

# /odysseus_features — what you can do + what's pending

When invoked: (1) show the **pending setup tasks** and flag any not done yet,
(2) present the **feature catalog**, (3) highlight the **profile picks**, and
(4) if useful, check what's actually configured (model connected? ChromaDB up?
CalDAV/email set? Life-OS tasks enabled?) and suggest the next step.

---

## ⏳ Pending setup tasks (do these on the Mac)

- [ ] **(PENDING — option B) Remote access via Tailscale** — secure access from
  your phone anywhere, without exposing anything on the LAN or the public
  internet. Do this when you bring Odysseus up on the Mac:
  1. Install Tailscale on the **Mac** and your **phone**, signed into the same
     account: `brew install --cask tailscale` (or App Store), then `tailscale up`.
  2. Keep Odysseus on loopback (`APP_BIND=127.0.0.1`) — no LAN exposure needed.
  3. Expose it over HTTPS inside your tailnet (auto-cert, no mkcert):
     ```bash
     tailscale serve --bg 7860        # proxies https://<mac>.<tailnet>.ts.net → 127.0.0.1:7860
     tailscale serve status           # shows the exact URL
     ```
     (Use the port `start-macos.sh` printed — 7860 by default on macOS.)
  4. On the phone (Tailscale connected), open that `https://…ts.net` URL.
  5. Keep **auth + 2FA ON**. Never port-forward this on your router.
  - Why B over LAN (`APP_BIND=0.0.0.0`): encrypted, works outside the house,
    nothing exposed to other devices/guests on the local network.

- [ ] **Push notifications (ntfy)** — configure an ntfy reminder channel in
  Settings so the Life-OS briefs/digests reach your phone (`ntfy` service is in
  `docker-compose.yml`).

- [ ] **Enable the Life-OS tasks** — Settings → Tasks: Daily Goals, Ingest,
  Morning Brief, Fitness Digest, DBA Night Watch, English Lesson (all ship paused).

> Keep this list updated: check a box when done; add new pending items here.

---

## 📚 Full feature catalog

**AI core**
- **Chat + Agents** — local/API models with tools, MCP servers, shell, file
  attachments, skills, and long-term memory.
- **Presets / Personas** — specialized system prompts (incl. the 8 Life-OS coaches).
- **Cookbook** — hardware-aware model recommendations, download, and local serving
  (uses the Mac's Metal GPU when run natively).
- **Compare** — blind side-by-side model testing + synthesis.
- **Deep Research** — multi-step web research with source reading → cited report.
- **Memory + RAG** — semantic memory and document retrieval (ChromaDB + local embeddings).
- **Skills** — reusable agent procedures (SKILL.md), learned or taught.
- **Voice** — speech-to-text (mic) and text-to-speech (talk to the coaches).

**Productivity & data**
- **Documents** — writing-first editor with AI edits/suggestions; Markdown/HTML/CSV.
- **Email** — IMAP/SMTP inbox: triage, tags, summaries, reply drafts, signatures,
  urgency alerts, and email→calendar event extraction.
- **Notes / Tasks / Calendar** — reminders, todos, scheduled agent tasks, **CalDAV**
  sync (iCloud), contacts.
- **Gallery / Image** — image generation + editor, uploads, themes.
- **Web search** — multiple providers (SearXNG, DuckDuckGo, Brave, Tavily, …).
- **YouTube** — transcript extraction for research/summaries.

**Platform**
- **Auth / 2FA**, API tokens, webhooks, integrations (Claude Agent, Codex,
  Copilot), vault, backup/restore, diagnostics, multi-session history.

**Built this session (your branch)**
- **Database MCP server** — agents connect to PostgreSQL/MySQL/SQLite/Oracle/
  MongoDB (read-only by default, write opt-in per connection), diagnose issues,
  and document findings as Notes.
- **Life OS** — 8 personas, 5 reusable skills, per-area tracking notes.
- **Life OS automation** — hourly ingest (Apple Health via Postgres, CalDAV
  calendar, study docs) into a local history DB; daily goal reminders.
- **Proactive digests** — Morning Brief, weekly Fitness Digest (trend + calorie
  advice + plateau alert), DBA Night Watch (alerts only), English daily SRS lesson.
- **/odysseus_bugs** — post-PR security & bug verification skill.

---

## ⭐ Best picks for your profile

**🗄️ Senior DBA / work**
- Database MCP server (`db_diagnose`/`db_query`/`db_document`) + **DBA Sênior** persona.
- **DBA Night Watch** — unattended alerts (locks/slow queries/integrity) → runbook.
- Shell tool + Skills/runbook for repeatable fixes.

**🏋️ Bodybuilder / fat loss**
- Life-OS health history + **weekly Fitness Digest** (trend, calorie adjustment,
  plateau alert) + **Coach Fitness** persona + daily goal reminders.

**📈 Stock investor**
- **Investidor** persona + **Deep Research** on your holdings + the Portfolio note.
  (Next up if you want: an automated weekly portfolio brief.)

**🏢 Entrepreneur**
- Point the DB MCP at your business Postgres → KPI queries; **Empreendedor**
  persona; email triage to prioritize customer/investor mail.

**👨‍👩‍👦 Father & son**
- CalDAV calendar + **Família & Pessoal** persona + reminders + the **Morning
  Brief** (today's agenda, family commitments, top goal).

**🇬🇧 English (fast fluency)**
- **Tutor de Inglês** persona + **daily SRS lesson** (spaced repetition) +
  vocabulary tracking.

**🧠 Tech enthusiast**
- Cookbook (run local models on the M4), MCP/skills authoring, self-hosting,
  **Mentor Tech** persona, Compare for model evals.

**🧭 The glue**
- **Life OS** persona for the weekly integrated review; **Morning Brief** to
  start each day; **Memory** so it remembers your context across sessions.

---

## Pitfalls
- Many features need setup to shine: a connected model (Ollama/API), ChromaDB for
  good memory/RAG, CalDAV for calendar, IMAP for email, the health Postgres for
  fitness data, and the Life-OS tasks enabled. Check status before assuming a
  feature is "not working."
