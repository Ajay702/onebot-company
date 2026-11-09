"""Core logic: charters, the approval gate, routine triggers, and the multi-bot handoff."""
import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ---------- Charters ----------

@dataclass
class Charter:
    name: str
    role: str
    owns: list
    good_work: list
    ask_before: list
    approval_triggers: list = field(default_factory=list)
    facts: str = ""

    def system_prompt(self):
        bullets = lambda xs: "\n".join(f"- {x}" for x in xs)
        return (
            f"You are the {self.role}. You work for a one-person company and act like a trusted new hire.\n\n"
            f"You own:\n{bullets(self.owns)}\n\nGood work looks like:\n{bullets(self.good_work)}\n\n"
            f"You must stop and ask the owner before:\n{bullets(self.ask_before)}\n"
            "If your output touches any of those, start your reply with 'NEEDS APPROVAL: <reason>' "
            "on its own line, then still write the full draft.\n"
            + (f"\nFacts you may use:\n{self.facts}" if self.facts else "")
        )


def load_charters(folder):
    charters = {}
    for path in sorted(Path(folder).glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        charters[data["name"]] = Charter(
            name=data["name"], role=data["role"], owns=data.get("owns", []),
            good_work=data.get("good_work", []), ask_before=data.get("ask_before", []),
            approval_triggers=data.get("approval_triggers") or [], facts=data.get("facts", ""),
        )
    return charters


# ---------- Approval gate ----------

def approval_reasons(charter, text):
    """Why this draft must wait for the owner. Empty list = safe to send."""
    reasons = []
    first = text.strip().splitlines()[0] if text.strip() else ""
    if first.upper().startswith("NEEDS APPROVAL"):
        reasons.append("bot asked: " + first.split(":", 1)[-1].strip())
    for pattern in charter.approval_triggers:
        m = re.search(pattern, text)
        if m:
            reasons.append(f"charter rule matched {m.group(0)!r}")
    return reasons


# ---------- Routines & triggers ----------

def load_routines(path):
    return (yaml.safe_load(Path(path).read_text()) or {}).get("routines", [])


def message_matches(routine, message):
    trig = routine.get("trigger", {})
    if trig.get("type") != "message":
        return False
    text = f"{message.get('subject', '')} {message.get('body', '')}".lower()
    return any(re.search(r"\b" + re.escape(k.lower()) + r"\b", text) for k in trig.get("keywords", []))


def due_routines(routines, now, last_run):
    """Schedule routines that should run now: right day, past their time, not yet run today."""
    today = now.date().isoformat()
    due = []
    for r in routines:
        trig = r.get("trigger", {})
        if trig.get("type") != "schedule":
            continue
        days = [d.lower()[:3] for d in trig.get("days", DAYS)]
        hh, mm = map(int, trig["at"].split(":"))
        if DAYS[now.weekday()] in days and (now.hour, now.minute) >= (hh, mm) and last_run.get(r["name"]) != today:
            due.append(r)
    return due


def parse_trigger(spec):
    """'keywords:pricing,quote' -> message trigger; 'daily:08:00' or 'weekdays:08:00' -> schedule trigger."""
    kind, _, value = spec.partition(":")
    if kind == "keywords":
        return {"type": "message", "keywords": [k.strip() for k in value.split(",") if k.strip()]}
    if kind in ("daily", "weekdays"):
        if not re.fullmatch(r"\d{1,2}:\d{2}", value):
            raise ValueError(f"time must be HH:MM, got {value!r}")
        return {"type": "schedule", "at": value, "days": DAYS if kind == "daily" else DAYS[:5]}
    raise ValueError("trigger must look like 'keywords:a,b', 'daily:HH:MM' or 'weekdays:HH:MM'")


def save_routine(path, routine):
    path = Path(path)
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    routines = [r for r in data.get("routines", []) if r["name"] != routine["name"]]
    routines.append(routine)
    data["routines"] = routines
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


# ---------- Running routines ----------

class Output:
    """Writes drafts to out/<timestamp>/ and keeps a group-chat style transcript."""

    def __init__(self, root, label):
        self.dir = Path(root) / f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.chat = []

    def say(self, who, text):
        self.chat.append(f"**{who}:**\n{text}\n")
        print(f"\n[{who}]\n{text}")

    def draft(self, name, text, reasons):
        status = "NEEDS_APPROVAL" if reasons else "READY"
        header = f"<!-- status: {status}" + (f" | {'; '.join(reasons)}" if reasons else "") + " -->\n"
        (self.dir / f"{status.lower()}__{name}.md").write_text(header + text + "\n")
        return status

    def close(self):
        (self.dir / "group_chat.md").write_text("\n".join(self.chat))
        print(f"\nSaved to {self.dir}")


def run_message_routine(llm, charter, routine, message, out):
    user = (f"Routine: {routine['name']}\nSteps you were taught:\n{routine['steps']}\n\n"
            f"Incoming message\nFrom: {message['from']}\nSubject: {message['subject']}\n\n{message['body']}\n\n"
            "Write the reply email body only.")
    reply = llm.chat(charter.system_prompt(), user)
    reasons = approval_reasons(charter, reply)
    status = out.draft(f"{routine['name']}_{message.get('id', 'msg')}", reply, reasons)
    out.say(charter.role, f"({status}) reply to {message['from']}\n{reply}")
    return status


def process_inbox(llm, charters, routines, messages, out):
    results = []
    for msg in messages:
        hit = next((r for r in routines if message_matches(r, msg)), None)
        if hit:
            results.append((msg.get("id"), hit["name"], run_message_routine(llm, charters[hit["bot"]], hit, msg, out)))
        else:
            results.append((msg.get("id"), None, "SKIPPED"))
    return results


def run_scheduled_routine(llm, charter, routine, inputs, out):
    context = ""
    if routine.get("input") == "calendar":
        context = "Calendar for today:\n" + "\n".join(
            f"- {e['start']}-{e['end']} {e['title']} (attendees: {', '.join(e.get('attendees', [])) or 'none'})"
            for e in inputs.get("calendar", []))
    user = f"Routine: {routine['name']}\nSteps you were taught:\n{routine['steps']}\n\n{context}"
    text = llm.chat(charter.system_prompt(), user)
    status = out.draft(routine["name"], text, approval_reasons(charter, text))
    out.say(charter.role, text)
    return status


# ---------- Multi-bot objective (the "group chat") ----------

def load_leads(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def lead_line(lead):
    return " | ".join(f"{k}={v}" for k, v in lead.items())


def parse_picked_ids(text, valid_ids, limit):
    picked = []
    for i in re.findall(r"LEAD\s+(\w+)\s*:", text):
        if i in valid_ids and i not in picked:
            picked.append(i)
    return picked[:limit]


def review_verdict(text):
    """Returns (approved, feedback) from the reviewer's reply."""
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    if first.upper().startswith("APPROVED"):
        return True, ""
    return False, first.split(":", 1)[-1].strip() if ":" in first else text.strip()


def run_objective(llm, charters, objective, leads, out, max_leads=3, max_revisions=2):
    cos, res, wri, rev = (charters[n] for n in ("chief_of_staff", "researcher", "writer", "reviewer"))
    out.say("Owner", f"Objective: {objective}")

    brief = llm.chat(cos.system_prompt(), f"Objective from the owner: {objective}\n"
                     "Team: researcher, writer, reviewer. Write the team brief.")
    out.say(cos.role, brief)

    lead_text = "\n".join(lead_line(l) for l in leads)
    picks = llm.chat(res.system_prompt(), f"Brief:\n{brief}\n\nPick up to {max_leads} leads from this list:\n{lead_text}")
    out.say(res.role, picks)
    by_id = {l["id"]: l for l in leads}
    picked = parse_picked_ids(picks, set(by_id), max_leads)
    if not picked:
        out.say(cos.role, "Researcher returned no usable leads - stopping so the owner can check the lead list.")
        out.close()
        return []

    results = []
    for lead_id in picked:
        lead = by_id[lead_id]
        prompt = f"Brief:\n{brief}\n\nWrite a first-touch email to this lead:\n{lead_line(lead)}"
        draft = llm.chat(wri.system_prompt(), prompt)
        out.say(wri.role, draft)
        approved = False
        for attempt in range(max_revisions + 1):
            verdict = llm.chat(rev.system_prompt(), f"Brief:\n{brief}\n\nLead: {lead_line(lead)}\n\nDraft:\n{draft}")
            out.say(rev.role, verdict)
            approved, feedback = review_verdict(verdict)
            if approved or attempt == max_revisions:
                break
            draft = llm.chat(wri.system_prompt(), f"{prompt}\n\nYour previous draft:\n{draft}\n\n"
                             f"Reviewer feedback: {feedback}\nRewrite the email.")
            out.say(wri.role, draft)
        reasons = approval_reasons(wri, draft)
        if not approved:
            reasons.append("reviewer did not approve after max revisions")
        slug = re.sub(r"\W+", "_", lead["name"]).strip("_").lower()
        results.append((lead_id, out.draft(f"outreach_{lead_id}_{slug}", draft, reasons)))

    summary = "\n".join(f"- lead {i}: {s}" for i, s in results)
    report = llm.chat(cos.system_prompt(), f"Objective: {objective}\nStatus report needed. Results:\n{summary}")
    out.say(cos.role, report)
    out.close()
    return results


def load_json(path):
    return json.loads(Path(path).read_text())
