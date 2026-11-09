"""LLM clients: the real xAI Grok API (OpenAI-compatible) and an offline demo stand-in."""
import json
import os
import re
import urllib.request


class GrokClient:
    def __init__(self, api_key=None, model=None, base_url=None):
        self.api_key = api_key or os.environ.get("XAI_API_KEY", "")
        self.model = model or os.environ.get("GROK_MODEL", "grok-4")
        self.base_url = (base_url or os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")).rstrip("/")
        if not self.api_key:
            raise SystemExit("XAI_API_KEY is not set. Add it to .env, or run with --demo.")

    def chat(self, system, user):
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"].strip()


class DemoClient:
    """Deterministic fake replies so every command runs offline with no API key."""

    def chat(self, system, user):
        role = re.search(r"You are the (.+?)\.", system)
        role = role.group(1) if role else ""
        if role == "Lead Researcher":
            ids = re.findall(r"^id=(\d+) .*industry=(dental|physiotherapy|optometry)", user, re.M)
            return "\n".join(f"LEAD {i}: {ind} clinic with a fixable website problem" for i, ind in ids[:3])
        if role == "Outreach Writer":
            name = re.search(r"name=([^|]+?) \|", user)
            first = name.group(1).split()[0] if name else "there"
            if "Reviewer feedback" in user:
                return (f"Subject: Quick idea for your booking page\n\nHi {first},\n\n"
                        "I noticed a small issue on your clinic website that may be costing you bookings. "
                        "We fix exactly this for clinics every week.\n\nWould a 2-line summary of the fix be useful?")
            return (f"Subject: Your website!!!\n\nHi {first},\n\nI am reaching out because we are the BEST website "
                    "team for clinics and you really need to act now before your competitors do. "
                    "We can do everything for you.\n\nWhen can we talk?")
        if role == "Tone Reviewer":
            if "act now" in user:
                return "REVISE: Remove the fake urgency and mention one specific detail about their site."
            return "APPROVED\nClear, specific and polite."
        if role == "Inbox Manager":
            first = re.search(r"From: (?:Dr\. )?(\w+)", user)
            first = first.group(1) if first else "there"
            return (f"Hi {first},\n\nThanks for reaching out! Our Starter plan is $49/month (hosting + updates) and "
                    "Growth is $149/month (adds SEO + a monthly report). For a single clinic, Starter is usually "
                    "the right fit.\n\nHappy to walk you through it: https://cal.example.com/brightline/intro")
        if role == "Chief of Staff" and "Calendar" in user:
            return ("Today\n- 09:00 Intro call - Shah Dental. Prep: re-read Priya's email about the broken booking form.\n"
                    "- 09:30 Weekly planning (back-to-back with the intro call)\n"
                    "- 13:00 Proposal review - Lakeside Vet. Prep: have the Growth plan scope ready.\n"
                    "- 16:30 Invoice + bookkeeping\nGap: 10:00-13:00 is free for deep work.\n"
                    "Most important: send the Lakeside Vet proposal before 13:00.")
        if role == "Chief of Staff" and "Status report" in user:
            return ("Done: researcher picked leads, writer drafted outreach, reviewer approved after revisions.\n"
                    "Needs you: review the drafts in the output folder and send the ones you like.\n"
                    "Next: follow up in 3 business days with anyone who has not replied.")
        if role == "Chief of Staff":
            return ("- Goal: book intro calls with small clinics that have visible website problems\n"
                    "- Researcher: pick up to 3 best-fit leads\n- Writer: one short, specific email each\n"
                    "- Reviewer: block anything pushy\n- Success: drafts ready for the owner to send today")
        return "OK"
