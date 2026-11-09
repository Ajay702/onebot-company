"""CLI:  python -m onebot [--demo] <inbox | brief | objective | scheduler | teach>"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from . import core
from .llm import DemoClient, GrokClient


def load_dotenv(path=".env"):
    if not Path(path).exists():
        return
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(key, default):
    return os.environ.get(key) or default


def main(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(prog="onebot", description="Run a one-person company with role-based Grok bots.")
    p.add_argument("--demo", action="store_true", help="use canned offline replies instead of the Grok API")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inbox", help="run message-triggered routines on every inbox message")
    sub.add_parser("brief", help="run the morning_brief routine now")
    o = sub.add_parser("objective", help="give the team an objective; bots hand work to each other")
    o.add_argument("text")
    o.add_argument("--max-leads", type=int, default=3)
    s = sub.add_parser("scheduler", help="keep running scheduled routines at their times")
    s.add_argument("--once", action="store_true", help="check once and exit (good for cron)")
    t = sub.add_parser("teach", help="save a task you just did as a reusable routine")
    t.add_argument("--name", required=True)
    t.add_argument("--bot", required=True)
    t.add_argument("--trigger", required=True, help="keywords:a,b | daily:HH:MM | weekdays:HH:MM")
    t.add_argument("--steps", required=True, help="the steps, or @file.txt with a worked example")
    t.add_argument("--input", choices=["calendar"], help="extra input to attach for scheduled routines")
    a = p.parse_args(argv)

    company = Path(env("COMPANY_DIR", "company"))
    charters = core.load_charters(company / "charters")
    routines_path = company / "routines.yaml"
    routines = core.load_routines(routines_path)
    out_root = env("OUTPUT_DIR", "out")

    if a.cmd == "teach":
        if a.bot not in charters:
            raise SystemExit(f"Unknown bot {a.bot!r}. Known: {', '.join(charters)}")
        steps = Path(a.steps[1:]).read_text() if a.steps.startswith("@") else a.steps
        routine = {"name": a.name, "bot": a.bot, "trigger": core.parse_trigger(a.trigger), "steps": steps}
        if a.input:
            routine["input"] = a.input
        core.save_routine(routines_path, routine)
        print(f"Saved routine {a.name!r} -> {routines_path}")
        return

    llm = DemoClient() if a.demo else GrokClient()
    inputs = lambda: {"calendar": core.load_json(env("CALENDAR_FILE", "samples/calendar.json"))}

    if a.cmd == "inbox":
        out = core.Output(out_root, "inbox")
        messages = core.load_json(env("INBOX_FILE", "samples/inbox.json"))
        for mid, routine, status in core.process_inbox(llm, charters, routines, messages, out):
            print(f"  {mid}: {routine or '-'} -> {status}")
        out.close()
    elif a.cmd == "brief":
        r = next(r for r in routines if r["name"] == "morning_brief")
        out = core.Output(out_root, "brief")
        core.run_scheduled_routine(llm, charters[r["bot"]], r, inputs(), out)
        out.close()
    elif a.cmd == "objective":
        out = core.Output(out_root, "objective")
        leads = core.load_leads(env("LEADS_FILE", "samples/leads.csv"))
        core.run_objective(llm, charters, a.text, leads, out, max_leads=a.max_leads)
    elif a.cmd == "scheduler":
        state_path = Path(out_root) / "scheduler_state.json"
        while True:
            last = json.loads(state_path.read_text()) if state_path.exists() else {}
            now = datetime.now()
            for r in core.due_routines(routines, now, last):
                out = core.Output(out_root, r["name"])
                core.run_scheduled_routine(llm, charters[r["bot"]], r, inputs(), out)
                out.close()
                last[r["name"]] = now.date().isoformat()
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(last, indent=2))
            if a.once:
                break
            time.sleep(60)


if __name__ == "__main__":
    main()
