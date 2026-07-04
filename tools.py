"""LLM function tools available to the OutboundAI voice agent."""

import asyncio
import csv
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional

import httpx

from livekit import agents, api
from livekit.agents import llm

from db import (
    check_slot, get_next_available, insert_appointment, log_call,
    log_error, get_calls_by_phone, get_appointments_by_phone,
    add_contact_memory, get_contact_memory, compress_contact_memory,
)

logger = logging.getLogger("appointment-tools")

# ── Lead CSV capture ─────────────────────────────────────────────────────────
LEAD_CSV_PATH = os.getenv(
    "LEAD_CSV_PATH", os.path.join(os.path.dirname(__file__), "leads.csv")
)
LEAD_CSV_FIELDS = [
    "call_id",
    "timestamp",
    "phone_number",
    "name",
    "qualification_choice",
    "scenario_reply",
    "investment_budget",
    "product_interest",
    "expected_investment",
    "project_timeline",
    "consultation_reply",
    "consultation_time",
    "mobile_number",
    "email",
    "notes",
]
_lead_csv_lock = threading.Lock()


def _upsert_lead_row(row: dict) -> None:
    """Insert or update (by call_id) a single lead row in the CSV file."""
    with _lead_csv_lock:
        parent_dir = os.path.dirname(LEAD_CSV_PATH)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        rows = []
        if os.path.exists(LEAD_CSV_PATH):
            with open(LEAD_CSV_PATH, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        updated = False
        for existing in rows:
            if existing.get("call_id") == row.get("call_id"):
                existing.update({k: v for k, v in row.items() if v})
                updated = True
                break
        if not updated:
            rows.append(row)
        with open(LEAD_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LEAD_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


async def _log(msg: str, detail: str = "", level: str = "info") -> None:
    try:
        await log_error("agent", msg, detail, level)
    except Exception:
        pass


class AppointmentTools(llm.ToolContext):
    """All function tools available to the appointment-booking agent."""

    def __init__(
        self,
        ctx: agents.JobContext,
        phone_number: Optional[str] = None,
        lead_name: Optional[str] = None,
    ):
        self.ctx = ctx
        self.phone_number = phone_number
        self.lead_name = lead_name
        self._call_start_time = time.time()
        self._sip_domain = os.getenv("VOBIZ_SIP_DOMAIN", "")
        self.recording_url: Optional[str] = None
        self._lead_data: dict = {
            "call_id": getattr(getattr(ctx, "room", None), "name", "") or f"call_{int(time.time())}",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "phone_number": phone_number or "",
            "name": lead_name if lead_name and lead_name != "there" else "",
        }
        super().__init__(tools=[])

    def build_tool_list(self, enabled: list) -> list:
        """Return tool methods filtered by the enabled list. Empty list = all enabled."""
        all_methods = [
            self.check_availability, self.book_appointment, self.end_call,
            self.transfer_to_human, self.send_sms_confirmation, self.lookup_contact,
            self.remember_details, self.book_calcom, self.cancel_calcom,
            self.save_lead_info,
        ]
        if not enabled:
            return all_methods
        name_map = {m.__name__: m for m in all_methods}
        return [name_map[n] for n in enabled if n in name_map]

    @llm.function_tool
    async def save_lead_info(self, field: str, value: str) -> str:
        """
        Save one piece of caller information to the lead CSV file. Call this
        IMMEDIATELY every time the caller answers a question.
        field must be one of: name | qualification_choice | scenario_reply |
        investment_budget | product_interest | expected_investment |
        project_timeline | consultation_reply | consultation_time |
        mobile_number | email | notes.
        value: the caller's answer, verbatim or lightly cleaned.
        """
        field = (field or "").strip().lower()
        if field not in LEAD_CSV_FIELDS or field in ("call_id", "timestamp", "phone_number"):
            return (
                "Invalid field. Use one of: name, qualification_choice, "
                "scenario_reply, investment_budget, product_interest, "
                "expected_investment, project_timeline, consultation_reply, "
                "consultation_time, mobile_number, email, notes."
            )
        self._lead_data[field] = (value or "").strip()
        try:
            row = {k: self._lead_data.get(k, "") for k in LEAD_CSV_FIELDS}
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _upsert_lead_row, row)
            return f"Saved {field}."
        except Exception as exc:
            logger.error("save_lead_info failed: %s", exc)
            return "Could not save right now, but continue the conversation."

    @llm.function_tool
    async def check_availability(self, date: str, time: str) -> str:
        """
        Check whether a date/time slot is available for booking.
        Call BEFORE attempting to book whenever the lead proposes a date/time.
        date: YYYY-MM-DD, time: HH:MM (24-hour). Returns 'available' or
        'unavailable: next available slot is <slot>'.
        """
        try:
            if await check_slot(date, time):
                return "available"
            next_slot = await get_next_available(date, time)
            return f"unavailable: next available slot is {next_slot}"
        except Exception:
            return "Unable to check availability right now — please suggest a date and I will confirm."

    @llm.function_tool
    async def book_appointment(self, name: str, phone: str, date: str, time: str, service: str) -> str:
        """
        Book an appointment after the lead has verbally confirmed all details.
        name: lead's full name | phone: with country code | date: YYYY-MM-DD
        time: HH:MM | service: type of service.
        """
        try:
            booking_id = await insert_appointment(name, phone, date, time, service)
            return f"Confirmed! Booking ID: {booking_id}. See you on {date} at {time} for {service}."
        except Exception:
            return "Technical issue saving the booking. Our team will confirm shortly."

    @llm.function_tool
    async def end_call(self, outcome: str, reason: str = "") -> str:
        """
        End the call and log the outcome. ALWAYS call this before the call ends.
        outcome: 'booked' | 'not_interested' | 'wrong_number' | 'voicemail' |
        'no_answer' | 'callback_requested' | 'login_link_sent' |
        'subscription_link_sent' | 'transferred'.
        """
        duration = int(time.time() - self._call_start_time)
        try:
            await log_call(
                phone_number=self.phone_number or "unknown",
                lead_name=self.lead_name, outcome=outcome, reason=reason,
                duration_seconds=duration, recording_url=self.recording_url,
            )
        except Exception as exc:
            logger.error("Failed to log call: %s", exc)
        try:
            await self.ctx.room.disconnect()
        except Exception:
            pass
        return "Call ended."

    @llm.function_tool
    async def transfer_to_human(self, reason: str) -> str:
        """
        Transfer the call to a human agent via SIP REFER.
        Use when lead requests a human or has a complex issue.
        reason: brief explanation of why you're transferring.
        """
        destination = os.getenv("DEFAULT_TRANSFER_NUMBER", "")
        if not destination:
            return "Transfer unavailable: no fallback number configured."
        if "@" not in destination:
            clean = destination.replace("tel:", "").replace("sip:", "")
            destination = (
                f"sip:{clean}@{self._sip_domain}" if self._sip_domain else f"tel:{clean}"
            )
        elif not destination.startswith("sip:"):
            destination = f"sip:{destination}"

        participant_identity = f"sip_{self.phone_number}" if self.phone_number else None
        if not participant_identity:
            for p in self.ctx.room.remote_participants.values():
                participant_identity = p.identity
                break
        if not participant_identity:
            return "Transfer failed: could not identify caller."

        try:
            await self.ctx.api.sip.transfer_sip_participant(
                api.TransferSIPParticipantRequest(
                    room_name=self.ctx.room.name,
                    participant_identity=participant_identity,
                    transfer_to=destination, play_dialtone=False,
                )
            )
            return "Transferring you to a human agent now. Please hold."
        except Exception as exc:
            logger.error("Transfer failed: %s", exc)
            return "Transfer failed. Please call us back directly."

    @llm.function_tool
    async def send_sms_confirmation(self, phone: str, message: str) -> str:
        """
        Send an SMS confirmation. Skips silently if Twilio not configured.
        phone: recipient phone in E.164 | message: text body.
        """
        sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        token = os.getenv("TWILIO_AUTH_TOKEN", "")
        from_num = os.getenv("TWILIO_FROM_NUMBER", "")
        if not (sid and token and from_num):
            return "SMS skipped: Twilio not configured."
        try:
            from twilio.rest import Client
            loop = asyncio.get_event_loop()
            client = Client(sid, token)
            await loop.run_in_executor(
                None,
                lambda: client.messages.create(body=message, from_=from_num, to=phone),
            )
            return f"SMS sent to {phone}."
        except Exception as exc:
            logger.error("SMS send failed: %s", exc)
            return "SMS delivery failed, but the next step is confirmed."

    @llm.function_tool
    async def lookup_contact(self, phone: str) -> str:
        """
        Look up a contact's full history. Call at the START of every call.
        phone: lead's phone number with country code. Returns call history,
        appointments, and remembered details.
        """
        try:
            calls = await get_calls_by_phone(phone)
            appointments = await get_appointments_by_phone(phone)
            memories = await get_contact_memory(phone)
            if not calls and not appointments and not memories:
                return f"No history for {phone}. First-time contact."
            lines = [f"Contact history for {phone}:"]
            if memories:
                lines.append(f"\nREMEMBERED ({len(memories)} notes):")
                for m in memories[:10]:
                    lines.append(f"  • {m['insight']}")
            if calls:
                lines.append(f"\nCALL HISTORY ({len(calls)} calls):")
                for c in calls[:5]:
                    ts = (c.get("timestamp") or "")[:16]
                    lines.append(f"  • {ts} — {c.get('outcome','?')}: {c.get('reason','')}")
            if appointments:
                lines.append(f"\nAPPOINTMENTS ({len(appointments)}):")
                for a in appointments[:3]:
                    lines.append(
                        f"  • {a.get('date')} {a.get('time')} — {a.get('service')} [{a.get('status')}]"
                    )
            return "\n".join(lines)
        except Exception as exc:
            logger.error("lookup_contact failed: %s", exc)
            return "Unable to retrieve contact history."

    @llm.function_tool
    async def remember_details(self, insight: str) -> str:
        """
        Store a key insight about this lead for future calls.
        Use whenever you learn something useful (preferences, objections, timing,
        career interests, location, family info, callback time).
        """
        if not self.phone_number:
            return "Cannot remember — no phone number for this call."
        try:
            await add_contact_memory(self.phone_number, insight)
            memories = await get_contact_memory(self.phone_number)
            if len(memories) >= 5:
                asyncio.create_task(self._compress_memories())
            return f"Remembered: {insight}"
        except Exception:
            return "Could not save detail."

    async def _compress_memories(self) -> None:
        try:
            memories = await get_contact_memory(self.phone_number)
            if len(memories) < 5:
                return
            import google.generativeai as genai
            api_key = os.getenv("GOOGLE_API_KEY", "")
            if not api_key:
                return
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.0-flash")
            bullets = "\n".join(f"- {m['insight']}" for m in memories)
            prompt = (
                "Compress these notes about a sales contact into 3-5 concise "
                f"bullets. Keep all key facts.\n\n{bullets}"
            )
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: model.generate_content(prompt)
            )
            text = (getattr(response, "text", "") or "").strip()
            if text:
                await compress_contact_memory(self.phone_number, text)
        except Exception as exc:
            logger.warning("Memory compression failed (non-fatal): %s", exc)

    @llm.function_tool
    async def book_calcom(
        self, name: str, email: str, date: str, time: str, notes: str = ""
    ) -> str:
        """
        Book a Cal.com event. Use only if Cal.com is configured.
        date: YYYY-MM-DD, time: HH:MM (24-hour, in CALCOM_TIMEZONE).
        Returns booking UID or error.
        """
        api_key = os.getenv("CALCOM_API_KEY", "")
        event_id = os.getenv("CALCOM_EVENT_TYPE_ID", "")
        tz = os.getenv("CALCOM_TIMEZONE", "Asia/Kolkata")
        if not (api_key and event_id):
            return "Cal.com not configured — appointment saved internally only."
        try:
            start_iso = f"{date}T{time}:00.000Z"
            payload = {
                "eventTypeId": int(event_id),
                "start": start_iso,
                "timeZone": tz,
                "responses": {"name": name, "email": email, "notes": notes},
                "metadata": {"source": "OutboundAI"},
            }
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.cal.com/v1/bookings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
            if resp.status_code >= 400:
                return f"Cal.com booking failed: {resp.text[:200]}"
            uid = (resp.json() or {}).get("uid") or "unknown"
            return f"Cal.com booking created. UID: {uid}"
        except Exception as exc:
            logger.error("Cal.com book failed: %s", exc)
            return "Cal.com booking failed — please try again."

    @llm.function_tool
    async def cancel_calcom(self, uid: str) -> str:
        """Cancel a Cal.com booking by its UID."""
        api_key = os.getenv("CALCOM_API_KEY", "")
        if not api_key:
            return "Cal.com not configured."
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.delete(
                    f"https://api.cal.com/v1/bookings/{uid}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code >= 400:
                return f"Cancel failed: {resp.text[:200]}"
            return f"Cal.com booking {uid} cancelled."
        except Exception as exc:
            return f"Cancel failed: {exc}"
