"""
life_os.py

"Life OS" starter pack — centralizes the owner's life goals inside Odysseus.

This bundles, for a specific persona (senior DBA, father & son, bodybuilder on
an accelerated fat-loss cut, stock investor, entrepreneur, tech enthusiast, and
English learner):

  - reusable agent **skills** (installed into the on-disk skills library), and
  - **tracking notes** (goals dashboard + per-area logs) created as Odysseus
    Notes so progress lives in one searchable place.

The matching chat **personas** ship as presets in ``src/preset_manager.py``.

Everything here is data + two seeders (``seed_skills`` / ``seed_notes``), kept
out of app startup so it only runs when the owner asks for it (see
``scripts/seed_life_os.py``). Both seeders are idempotent: skills dedupe by
content, notes skip when one with the same title already exists for the owner.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Source tags so seeded content is identifiable (and re-seeds stay idempotent).
NOTE_SOURCE = "life_os"
SKILL_SOURCE = "taught"


# --------------------------------------------------------------------------- #
# Skills — reusable procedures the agent can pull into context on demand.
# Shape matches services.memory.skills.SkillsManager.add_skill kwargs.
# --------------------------------------------------------------------------- #
SKILLS: list[dict[str, Any]] = [
    {
        "name": "db-incident-triage",
        "category": "dba",
        "description": "Triage and document a database incident (slow/locked/down) safely.",
        "tags": ["dba", "postgres", "mysql", "oracle", "mongodb", "incident"],
        "when_to_use": "A database is slow, locking, throwing errors, or down and you need a calm, read-only-first triage.",
        "procedure": [
            "Run db_diagnose on the affected connection to get sizes, long/blocked queries, integrity and connection counts.",
            "Confirm the bottleneck with db_query: active queries, locks/waits, and the worst plan (EXPLAIN ANALYZE on the suspect statement).",
            "Form a root-cause hypothesis (missing index, lock contention, runaway query, bloat, exhausted connections, bad plan).",
            "Decide the safest fix; if it writes/DDL, confirm allow_write is intended and prepare a rollback before running db_execute.",
            "Document the timeline, evidence, root cause and fix with db_document so the runbook grows.",
        ],
        "pitfalls": [
            "Never run writes on a read-only connection — diagnose first, change second.",
            "Killing a session can roll back a long transaction; weigh the cost before acting on prod.",
        ],
        "verification": [
            "Symptom metric returns to baseline (latency, lock count, connections).",
            "A note exists documenting cause + fix + prevention.",
        ],
    },
    {
        "name": "weekly-portfolio-review",
        "category": "investing",
        "description": "Run a disciplined weekly review of the stock portfolio.",
        "tags": ["investing", "stocks", "portfolio", "weekly"],
        "when_to_use": "Once a week, to review holdings, theses and risk without reacting to noise.",
        "procedure": [
            "Open the Portfólio note and list current positions, weights and the original thesis for each.",
            "For each holding, check whether the thesis still holds (fundamentals, valuation, catalysts, risks).",
            "Flag positions that broke their thesis or grew past the target weight.",
            "Decide actions (hold/add/trim/exit) with a one-line reason each; never trade on emotion.",
            "Record decisions and the reasoning in the note for future review.",
        ],
        "pitfalls": [
            "Confusing price drops with thesis breaks — separate the two.",
            "Over-trading; most weeks the right action is 'hold'.",
        ],
        "verification": ["Each position has an up-to-date thesis status and a logged decision."],
    },
    {
        "name": "fat-loss-weekly-adjust",
        "category": "fitness",
        "description": "Adjust the cut weekly from the weight/measure trend, preserving muscle.",
        "tags": ["fitness", "fat-loss", "nutrition", "weekly"],
        "when_to_use": "Weekly check-in on an accelerated fat-loss phase.",
        "procedure": [
            "Average the week's weigh-ins (use the trend, not a single day).",
            "Compare to last week: target ~0.5-1.0% bodyweight loss/week on an aggressive cut.",
            "If loss stalled >1 week: cut ~150-250 kcal or add steps/cardio; if dropping too fast or strength tanking: add calories.",
            "Keep protein ~2 g/kg and keep heavy lifting to protect muscle.",
            "Log the new targets and how you felt (energy, sleep, hunger) in the Treino/Nutrição notes.",
        ],
        "pitfalls": [
            "Cutting too hard too long → muscle loss and burnout; schedule diet breaks.",
            "Chasing daily scale noise instead of the weekly trend.",
        ],
        "verification": ["Weekly trend moves toward the goal while lifts stay roughly maintained."],
    },
    {
        "name": "daily-english-drill",
        "category": "learning",
        "description": "A short, repeatable daily English practice loop with logging.",
        "tags": ["english", "language", "daily", "habit"],
        "when_to_use": "Every day, to build an English fluency streak in 10-15 minutes.",
        "procedure": [
            "Pick today's topic (work, fitness, investing, family) so vocabulary is relevant.",
            "Have a short spoken/written exchange with the Tutor de Inglês persona; get corrections.",
            "Capture 3-5 new words/expressions with example sentences in the English study note.",
            "Do one mini-exercise the tutor proposes.",
            "Update the streak count in the note.",
        ],
        "pitfalls": ["Skipping logging breaks the habit loop; always capture the words."],
        "verification": ["The English note has today's date, new vocab, and an incremented streak."],
    },
    {
        "name": "weekly-life-review",
        "category": "life",
        "description": "Integrated weekly review across all life areas with next actions.",
        "tags": ["life-os", "weekly", "goals", "review"],
        "when_to_use": "Once a week (e.g. Sunday) to align all areas and set the week's priorities.",
        "procedure": [
            "Open the 'Life OS — Painel de Metas' note and read each area's goal.",
            "For each area (DBA/carreira, família, fitness, investimentos, empresa, tech, inglês): note what advanced and what stalled.",
            "Pick ONE next action per area for the coming week, with a day/time.",
            "Add time-bound actions to Tarefas/Calendário; protect family time first.",
            "Save the review summary back into the dashboard note.",
        ],
        "pitfalls": [
            "Planning everything and doing nothing — cap at one action per area.",
            "Letting work crowd out family and recovery.",
        ],
        "verification": ["Each area has one scheduled next action and the dashboard reflects this week."],
    },
]


# --------------------------------------------------------------------------- #
# Tracking notes — created as Odysseus Notes so they're searchable in the UI.
# --------------------------------------------------------------------------- #
NOTE_TEMPLATES: list[dict[str, str]] = [
    {
        "title": "Life OS — Painel de Metas",
        "label": "life-os",
        "content": """# Life OS — Painel de Metas

Centralização de todos os objetivos de vida nesta Odysseus. Revise no check-in semanal (ver skill `weekly-life-review`).

| Área | Meta atual | Métrica | Status |
|------|------------|---------|--------|
| DBA / Carreira | _ex: zerar incidentes recorrentes_ | incidentes/mês | 🟡 |
| Família (pai & filho) | _ex: 1 ritual semanal com filhos + ligar p/ os pais_ | rituais/semana | 🟡 |
| Fitness (perda de gordura) | _ex: -1%/semana preservando força_ | % gordura / peso | 🟡 |
| Investimentos | _ex: aportar e revisar teses_ | aporte/mês, DY | 🟡 |
| Empresa | _ex: destravar gargalo X_ | KPI principal | 🟡 |
| Tech | _ex: dominar tópico Y_ | projeto/experimento | 🟡 |
| Inglês | _ex: fluência conversacional_ | streak de dias | 🟡 |

## Check-in da semana
- **Avançou:**
- **Travou:**
- **Próxima ação por área:**

> Personas: `Life OS`, `DBA Sênior`, `Coach Fitness`, `Investidor de Ações`, `Empreendedor`, `Tutor de Inglês`, `Família & Pessoal`, `Mentor Tech`.
""",
    },
    {
        "title": "Treino & Recomposição",
        "label": "fitness",
        "content": """# Treino & Recomposição

Objetivo: **perda de gordura acelerada** preservando massa magra. Coach: persona `Coach Fitness` + skill `fat-loss-weekly-adjust`.

## Metas
- Proteína: ~2 g/kg/dia
- Déficit: ajustado pela tendência semanal (alvo -0,5% a -1%/semana)
- Treino de força: progressão de carga

## Log de treino
| Data | Treino | Principais cargas | Notas |
|------|--------|-------------------|-------|
|  |  |  |  |

## Log de medidas
| Data | Peso | Cintura | % gordura | Energia/Sono |
|------|------|---------|-----------|--------------|
|  |  |  |  |  |
""",
    },
    {
        "title": "Nutrição",
        "label": "fitness",
        "content": """# Nutrição

Alvos de macros do cutting. Ajuste semanal junto com o treino.

## Alvos
- Calorias:
- Proteína (g):
- Carbo (g):
- Gordura (g):

## Diário alimentar
| Data | Calorias | Proteína | Aderência | Observações |
|------|----------|----------|-----------|-------------|
|  |  |  |  |  |
""",
    },
    {
        "title": "Portfólio de Ações",
        "label": "investing",
        "content": """# Portfólio de Ações

Persona `Investidor de Ações` + skill `weekly-portfolio-review`. **Não é recomendação de investimento.**

## Posições
| Ativo | Peso % | Preço médio | Tese (resumo) | Status da tese |
|-------|--------|-------------|---------------|----------------|
|  |  |  |  |  |

## Diário de decisões
| Data | Ação (compra/venda/hold) | Ativo | Razão |
|------|--------------------------|-------|-------|
|  |  |  |  |
""",
    },
    {
        "title": "Empresa — Metas & KPIs",
        "label": "business",
        "content": """# Empresa — Metas & KPIs

Persona `Empreendedor`. Foco no próximo gargalo, não no plano grandioso.

## KPIs
| KPI | Atual | Meta | Tendência |
|-----|-------|------|-----------|
| Receita |  |  |  |
| Margem |  |  |  |
| CAC / LTV |  |  |  |

## Prioridades da semana
- [ ] (ação) — responsável — prazo
""",
    },
    {
        "title": "Inglês — Plano de Estudo",
        "label": "learning",
        "content": """# Inglês — Plano de Estudo

Persona `Tutor de Inglês` + skill `daily-english-drill`. Meta: fluência rápida com prática diária.

**Streak atual:** 0 dias

## Vocabulário novo
| Data | Palavra/Expressão | Exemplo | Revisado? |
|------|-------------------|---------|-----------|
|  |  |  |  |

## Notas de gramática / erros recorrentes
-
""",
    },
    {
        "title": "Família & Pessoal",
        "label": "family",
        "content": """# Família & Pessoal

Persona `Família & Pessoal`. Ser pai presente e bom filho, sem deixar o trabalho engolir tudo.

## Rituais & compromissos
- [ ] Tempo de qualidade com os filhos (quando):
- [ ] Ligar/visitar os pais (quando):
- [ ] Limite trabalho ↔ casa:

## Datas importantes
| Data | Quem | O quê |
|------|------|-------|
|  |  |  |
""",
    },
]


# --------------------------------------------------------------------------- #
# Seeders
# --------------------------------------------------------------------------- #
def seed_skills(skills_manager=None, owner: Optional[str] = None) -> dict[str, Any]:
    """Install the Life-OS skills into the on-disk skills library.

    Idempotent: ``add_skill`` dedupes near-identical content, so re-running
    won't grow the library. Returns a summary dict.
    """
    if skills_manager is None:
        from src.constants import DATA_DIR
        from services.memory.skills import SkillsManager
        skills_manager = SkillsManager(DATA_DIR)

    added, skipped = [], []
    for sk in SKILLS:
        try:
            result = skills_manager.add_skill(
                name=sk["name"],
                description=sk["description"],
                category=sk.get("category", "life"),
                tags=sk.get("tags"),
                when_to_use=sk.get("when_to_use"),
                procedure=sk.get("procedure"),
                pitfalls=sk.get("pitfalls"),
                verification=sk.get("verification"),
                source=SKILL_SOURCE,
                status="published",
                owner=owner,
            )
            if isinstance(result, dict) and result.get("_deduped"):
                skipped.append(sk["name"])
            else:
                added.append(sk["name"])
        except Exception as e:
            logger.warning("Failed to seed skill %s: %s", sk["name"], e)
    return {"added": added, "skipped": skipped, "total": len(SKILLS)}


def seed_notes(owner: Optional[str] = None) -> dict[str, Any]:
    """Create the Life-OS tracking notes as Odysseus Notes.

    Idempotent: a note is skipped when one with the same title already exists
    for the owner (matched on our ``source`` tag). Returns a summary dict.
    """
    import uuid as _uuid

    from core.database import Note, SessionLocal

    created, skipped = [], []
    db = SessionLocal()
    try:
        existing = {
            n.title
            for n in db.query(Note).filter(
                Note.owner == owner, Note.source == NOTE_SOURCE
            ).all()
        }
        for tmpl in NOTE_TEMPLATES:
            if tmpl["title"] in existing:
                skipped.append(tmpl["title"])
                continue
            note = Note(
                id=str(_uuid.uuid4()),
                owner=owner,
                title=tmpl["title"],
                content=tmpl["content"],
                note_type="note",
                label=tmpl.get("label", "life-os"),
                source=NOTE_SOURCE,
            )
            db.add(note)
            created.append(tmpl["title"])
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("Failed to seed Life-OS notes: %s", e)
        raise
    finally:
        db.close()
    return {"created": created, "skipped": skipped, "total": len(NOTE_TEMPLATES)}


def seed_all(owner: Optional[str] = None, skills_manager=None) -> dict[str, Any]:
    """Seed both skills and notes. Returns a combined summary."""
    return {
        "skills": seed_skills(skills_manager=skills_manager, owner=owner),
        "notes": seed_notes(owner=owner),
    }
