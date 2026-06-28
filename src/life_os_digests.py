"""
life_os_digests.py

Builders for the Life-OS proactive digests/briefs, consumed by the scheduled
actions in src/builtin_actions.py:

  - morning_brief()    — daily integrated brief (calendar, fitness, goals, English)
  - fitness_digest()   — weekly fat-loss trend + calorie suggestion + plateau flag
  - dba_health_scan()  — unattended DB health, alerts only (DBA Night Watch)
  - english_lesson()   — daily spaced-repetition review + lesson prompt

Each returns plain data (title + Markdown). The actions turn these into Notes
with a due_date so the existing reminder pipeline pushes them (ntfy/browser/
email). All reads are owner-scoped and degrade gracefully when a source is
missing/unconfigured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src import life_os, life_os_history as hist

logger = logging.getLogger(__name__)

_WEIGHT_TYPES = ("body_mass", "weight", "bodyweight")
_FAT_TYPES = ("body_fat_percentage", "body_fat", "bodyfat")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _to_naive(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None)
    return None


def _recent_metrics(owner, types, days):
    """Return [(ts_naive, value)] for the given metric types in the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    session = hist.get_session()
    try:
        q = session.query(hist.HealthMetric).filter(
            hist.HealthMetric.metric_type.in_(types))
        if owner is not None:
            q = q.filter(hist.HealthMetric.owner == owner)
        out = []
        for r in q.all():
            ts = _to_naive(r.ts)
            if ts is not None and ts >= cutoff and r.value is not None:
                out.append((ts, float(r.value)))
        out.sort()
        return out
    finally:
        session.close()


def _avg(vals):
    return sum(vals) / len(vals) if vals else None


def _fitness_numbers(owner):
    """Compute this-week vs previous-week weight averages + recent fat/workouts."""
    weights = _recent_metrics(owner, _WEIGHT_TYPES, 14)
    now = datetime.utcnow()
    wk_this = [v for ts, v in weights if ts >= now - timedelta(days=7)]
    wk_prev = [v for ts, v in weights if now - timedelta(days=14) <= ts < now - timedelta(days=7)]
    fat = _recent_metrics(owner, _FAT_TYPES, 30)

    session = hist.get_session()
    try:
        wq = session.query(hist.Workout)
        if owner is not None:
            wq = wq.filter(hist.Workout.owner == owner)
        workouts = [w for w in wq.all()
                    if (_to_naive(w.start_ts) or datetime.min) >= now - timedelta(days=7)]
        n_workouts = len(workouts)
        kcal = sum(w.energy_kcal or 0 for w in workouts)
    finally:
        session.close()

    return {
        "this_avg": _avg(wk_this), "prev_avg": _avg(wk_prev),
        "latest_weight": weights[-1][1] if weights else None,
        "latest_fat": fat[-1][1] if fat else None,
        "workouts_7d": n_workouts, "workout_kcal_7d": round(kcal),
        "weigh_ins": len(weights),
    }


# --------------------------------------------------------------------------- #
# Fitness weekly digest
# --------------------------------------------------------------------------- #
def fitness_digest(owner: Optional[str] = None) -> dict:
    f = _fitness_numbers(owner)
    lines = [f"# Resumo Fitness da Semana — {_today()}", ""]
    alert = False

    if f["this_avg"] is None and f["prev_avg"] is None:
        lines.append("_Sem dados de peso ainda. Confirme a ingestão do Apple Health "
                     "(Life OS Ingest) e o Postgres de saúde._")
        return {"title": f"Resumo Fitness — {_today()}", "markdown": "\n".join(lines),
                "alert": False}

    lines.append("| Métrica | Valor |")
    lines.append("|---|---|")
    if f["latest_weight"] is not None:
        lines.append(f"| Peso atual | {f['latest_weight']:.1f} |")
    if f["this_avg"] is not None:
        lines.append(f"| Média desta semana | {f['this_avg']:.2f} |")
    if f["prev_avg"] is not None:
        lines.append(f"| Média semana passada | {f['prev_avg']:.2f} |")
    if f["latest_fat"] is not None:
        lines.append(f"| % gordura (recente) | {f['latest_fat']:.1f}% |")
    lines.append(f"| Treinos (7d) | {f['workouts_7d']} ({f['workout_kcal_7d']} kcal) |")
    lines.append("")

    # Trend + suggestion.
    if f["this_avg"] is not None and f["prev_avg"]:
        loss = f["prev_avg"] - f["this_avg"]
        pct = loss / f["prev_avg"] * 100 if f["prev_avg"] else 0
        lines.append(f"**Tendência:** {loss:+.2f} ({pct:+.2f}%/semana).")
        if pct < 0.2:
            alert = True
            lines.append("⚠️ **Platô / déficit estagnado.** Corte ~150–250 kcal/dia "
                         "ou adicione ~2.000 passos/dia. Mantenha proteína ~2 g/kg e "
                         "a carga dos treinos.")
        elif pct > 1.2:
            alert = True
            lines.append("⚠️ **Perda rápida demais** (risco de massa magra). Adicione "
                         "~100–150 kcal/dia e priorize força + sono.")
        else:
            lines.append("✅ **No alvo** (~0,5–1%/semana). Mantenha o protocolo atual.")
    else:
        alert = True
        lines.append("⚠️ Poucas pesagens nas últimas 2 semanas — pese-se mais vezes "
                     "para o ajuste semanal ser confiável.")

    if f["workouts_7d"] < 3:
        lines.append(f"\n_Só {f['workouts_7d']} treino(s) em 7 dias — suba o volume de força "
                     "para proteger a massa no cutting._")

    return {"title": f"Resumo Fitness — {_today()}", "markdown": "\n".join(lines),
            "alert": alert}


# --------------------------------------------------------------------------- #
# Morning brief
# --------------------------------------------------------------------------- #
def _today_calendar(owner):
    try:
        from core.database import SessionLocal, CalendarEvent, CalendarCal
    except Exception:
        return []
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    db = SessionLocal()
    try:
        q = (db.query(CalendarEvent, CalendarCal)
             .join(CalendarCal, CalendarEvent.calendar_id == CalendarCal.id)
             .filter(CalendarEvent.dtstart < end, CalendarEvent.dtend > start,
                     CalendarEvent.status != "cancelled"))
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        return [(ev.dtstart, ev.summary) for ev, _ in q.order_by(CalendarEvent.dtstart).all()]
    except Exception:
        return []
    finally:
        db.close()


def morning_brief(owner: Optional[str] = None) -> dict:
    lines = [f"# Brief de Hoje — {_today()}", ""]

    events = _today_calendar(owner)
    lines.append("## 📅 Agenda")
    if events:
        for dt, summ in events[:8]:
            hhmm = dt.strftime("%H:%M") if isinstance(dt, datetime) else ""
            lines.append(f"- {hhmm} {summ or '(sem título)'}")
    else:
        lines.append("- _Sem eventos hoje (ou CalDAV não sincronizado)._")

    f = _fitness_numbers(owner)
    lines.append("\n## 🏋️ Fitness")
    if f["latest_weight"] is not None:
        trend = ""
        if f["this_avg"] is not None and f["prev_avg"]:
            trend = f" ({f['prev_avg'] - f['this_avg']:+.2f} vs semana passada)"
        lines.append(f"- Peso: {f['latest_weight']:.1f}{trend} · treinos 7d: {f['workouts_7d']}")
    else:
        lines.append("- _Sem dados de peso ainda._")

    v = hist.vocab_stats(owner)
    lines.append("\n## 🇬🇧 Inglês")
    lines.append(f"- {v['due']} card(s) para revisar hoje · {v['learned']} aprendidos "
                 f"(de {v['total']}).")

    lines.append("\n## 🎯 Meta nº1 por área")
    for g in life_os.DAILY_GOALS[:6]:
        lines.append(f"- **{g['area']}:** {g['goal']}")

    lines.append("\n> Personas: fale com `Life OS` para o panorama, ou a persona da área.")
    return {"title": f"Brief de Hoje — {_today()}", "markdown": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# DBA Night Watch
# --------------------------------------------------------------------------- #
def dba_health_scan(owner: Optional[str] = None) -> dict:
    """Scan all configured SQL connections; return only firing alerts."""
    from src import db_mcp
    conns = db_mcp.load_connections()
    all_alerts = []
    scanned = 0
    for cid, conn in conns.items():
        if conn.get("type") == "mongodb":
            continue
        scanned += 1
        try:
            all_alerts.extend(db_mcp.health_alerts(cid))
        except Exception as e:
            logger.warning("health scan failed for %s: %s", cid, e)
    if not all_alerts:
        return {"alerts": [], "scanned": scanned, "title": "", "markdown": ""}
    lines = [f"# ⚠️ DBA Night Watch — {_today()}", "",
             f"{len(all_alerts)} alerta(s) em {scanned} conexão(ões):", ""]
    for a in all_alerts:
        lines.append(f"- **{a['connection']}** [{a['check']}]: {a['detail']}")
    lines.append("\n_Use a persona `DBA Sênior` + `db_diagnose`/`db_query` para investigar "
                 "e `db_document` para registrar a causa/correção._")
    return {"alerts": all_alerts, "scanned": scanned,
            "title": f"DBA Night Watch — {_today()}", "markdown": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# English daily SRS lesson
# --------------------------------------------------------------------------- #
def english_lesson(owner: Optional[str] = None) -> dict:
    stats = hist.vocab_stats(owner)
    due = hist.due_vocab(owner, limit=12)
    lines = [f"# Inglês — Lição do Dia ({_today()})", "",
             f"📊 {stats['due']} para revisar · {stats['learned']} aprendidos · "
             f"{stats['total']} no total.", ""]

    if due:
        lines.append("## 🔁 Revisão (spaced repetition)")
        lines.append("Tente lembrar antes de olhar a resposta:")
        for d in due:
            tr = f" — _{d['translation']}_" if d.get("translation") else ""
            lines.append(f"- **{d['term']}**{tr}")
            if d.get("example"):
                lines.append(f"  - ex: {d['example']}")
        lines.append("")
    else:
        lines.append("_Nenhum card vencido hoje — bom momento para aprender palavras novas._\n")

    lines.append("## 🎯 Hoje com o Tutor de Inglês")
    lines.append("Abra a persona **Tutor de Inglês** e diga: "
                 "_\"Faça a lição de hoje sobre um tema do meu dia (trabalho/treino/"
                 "investimentos), corrija meus erros e registre 3–5 palavras novas.\"_")
    lines.append("\n> Para registrar vocabulário e revisões, use as funções de SRS do "
                 "Life OS (add_vocab/grade_vocab).")
    return {"title": f"Inglês — Lição do Dia ({_today()})", "markdown": "\n".join(lines),
            "due_count": stats["due"]}
