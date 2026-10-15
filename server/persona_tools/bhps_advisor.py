"""Tools and execution engine for Riya (Asian Paints Beautiful Homes Painting Service - BHPS).

Provides SF API pincode serviceability lookup, painting/civil/timeline qualification,
site visit / BH Store / Sleek Kitchen registration, callback scheduling, and JIT phase switching
with full RTVI event broadcasting and observability logging.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Dict, Optional
try:
    from google.genai import types
except ImportError:
    from types import SimpleNamespace
    types = SimpleNamespace(
        FunctionDeclaration=lambda **kw: SimpleNamespace(**kw),
        Schema=lambda **kw: SimpleNamespace(**kw),
        Type=SimpleNamespace(OBJECT="OBJECT", STRING="STRING", BOOLEAN="BOOLEAN", ARRAY="ARRAY"),
    )
try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

try:
    from diagnostic_buffer import append_diagnostic_log
except ImportError:
    def append_diagnostic_log(event_type: str, details: str, **kwargs: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Tool Schemas
# ---------------------------------------------------------------------------

check_pincode_serviceability_schema = types.FunctionDeclaration(
    name="check_pincode_serviceability",
    description=(
        "Check Asian Paints SF API serviceability for the caller's 6-digit pincode (Step 2 of BHPS Script). "
        "Returns whether Beautiful Homes Painting Service (BHPS), Beautiful Homes (BH) Store Interiors, "
        "and Sleek Modular Kitchens & Wardrobes are serviceable in that pincode, plus the exact Step 3/4/5 question to ask next."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "pincode": types.Schema(
                type=types.Type.STRING,
                description="6-digit Indian postal code (e.g., '400053', '560001', '110001').",
            ),
        },
        required=["pincode"],
    ),
)

record_painting_qualification_schema = types.FunctionDeclaration(
    name="record_painting_qualification",
    description=(
        "Record the customer's answers to the BHPS qualification steps (Steps 5-13: painting vs waterproofing, "
        "pending civil/construction work & completion timeline, work start timeline, existing painter/contractor "
        "and 1-Wall Highlightation Texture interest, or BH Store / Sleek details) and get the exact next script instruction."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "painting_scope": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Customer's selected scope: 'painting_only', 'painting_and_waterproofing', "
                    "'one_wall_texture_wallpaper', 'bh_store_interiors', or 'sleek_kitchen_wardrobe'."
                ),
            ),
            "civil_work_pending": types.Schema(
                type=types.Type.BOOLEAN,
                description="True if any civil or construction work is planned or pending completion (Step 6).",
            ),
            "civil_completion_timeline": types.Schema(
                type=types.Type.STRING,
                description="Expected timeline for civil work completion, e.g. 'within_2_months' or 'exceeds_2_months' (Step 7).",
            ),
            "work_start_timeline": types.Schema(
                type=types.Type.STRING,
                description="When the customer plans to start painting/waterproofing, e.g. 'within_1_month' or 'more_than_1_month' (Step 9).",
            ),
            "existing_painter_onboarded": types.Schema(
                type=types.Type.BOOLEAN,
                description="True if a painter/contractor/carpenter is already working on site (Step 12 / Sleek Step 6).",
            ),
            "one_wall_texture_interest": types.Schema(
                type=types.Type.BOOLEAN,
                description="If existing painter is onboarded, whether the customer is interested in Asian Paints 1-Wall Highlightation Texture & Wallpaper (Step 13).",
            ),
            "interior_or_sleek_details": types.Schema(
                type=types.Type.STRING,
                description="Optional summary of BH Store categories, Fresh/Renovation status, or Sleek kitchen dimensions/slab/budget.",
            ),
        },
        required=["painting_scope"],
    ),
)

book_bhps_action_schema = types.FunctionDeclaration(
    name="book_bhps_action",
    description=(
        "Execute the terminal action for the Asian Paints call: book a BHPS Associate Site Visit (Step 16), "
        "register for Beautiful Homes Store (BH Store Step 6), register for Sleek Modular Kitchen/Wardrobe design visit "
        "(Sleek Step 12/14), trigger Budget Calculator SMS + 1-month callback (Step 10), schedule 2-month civil/possession "
        "callback (Step 8 / BH Step 10), or log a declined appointment & send trusted contractor SMS (Step 19)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action_type": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Action to execute: 'book_site_visit', 'register_bh_store', 'register_sleek_kitchen', "
                    "'register_sleek_components', 'budget_calculator_callback_1m', 'civil_work_callback_2m', "
                    "'bh_store_possession_callback_2m', 'log_declined_appointment', or 'schedule_callback'."
                ),
            ),
            "pincode": types.Schema(
                type=types.Type.STRING,
                description="Customer's 6-digit pincode if known.",
            ),
            "preferred_date": types.Schema(
                type=types.Type.STRING,
                description="Preferred visit or callback date (e.g. 'Tomorrow', 'Saturday', 'After 1 month').",
            ),
            "preferred_time_slot": types.Schema(
                type=types.Type.STRING,
                description="Preferred visit or callback time slot (e.g. '11:00 AM', '4:00 PM', 'Within 48 hours').",
            ),
            "service_summary": types.Schema(
                type=types.Type.STRING,
                description="Summary of the service booked (e.g. 'Painting + Waterproofing', '1-Wall Texture & Wallpaper', 'Sleek Modular Kitchen').",
            ),
            "notes": types.Schema(
                type=types.Type.STRING,
                description="Additional notes, UPVC/Bathsense interest, or reason for declining appointment (Step 19).",
            ),
        },
        required=["action_type"],
    ),
)

switch_phase_schema = types.FunctionDeclaration(
    name="switch_phase",
    description=(
        "Transition the active Asian Paints BHPS SOP phase and inject its JIT phase card before replying. "
        "Allowed phases: 'SOP_01_PINCODE_SERVICEABILITY', 'SOP_02_SCOPE_QUALIFICATION', "
        "'SOP_03_INTERIORS_OR_SLEEK', 'SOP_04_VISIT_BOOKING'."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "phase_id": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Target phase identifier: 'SOP_01_PINCODE_SERVICEABILITY', 'SOP_02_SCOPE_QUALIFICATION', "
                    "'SOP_03_INTERIORS_OR_SLEEK', or 'SOP_04_VISIT_BOOKING'."
                ),
            ),
        },
        required=["phase_id"],
    ),
)


# ---------------------------------------------------------------------------
# Pincode Serviceability Directory (SF API Mock)
# ---------------------------------------------------------------------------

NON_SERVICEABLE_PREFIXES = {"000", "999", "744", "682", "194"}

PINCODE_PREFIX_DIRECTORY: Dict[str, Dict[str, Any]] = {
    "400": {
        "city": "Mumbai",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Worli / Linking Road, Mumbai",
        "nearest_sleek_studio": "Sleek by Asian Paints Kitchen Studio, Bandra West, Mumbai",
    },
    "411": {
        "city": "Pune",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Baner / Kalyani Nagar, Pune",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Wakad, Pune",
    },
    "110": {
        "city": "New Delhi",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Lajpat Nagar / Kirti Nagar, New Delhi",
        "nearest_sleek_studio": "Sleek by Asian Paints Flagship Studio, South Extension, New Delhi",
    },
    "122": {
        "city": "Gurugram",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Golf Course Road, Gurugram",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Sector 29, Gurugram",
    },
    "201": {
        "city": "Noida",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Sector 18, Noida",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Sector 104, Noida",
    },
    "560": {
        "city": "Bengaluru",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Indiranagar / HSR Layout, Bengaluru",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Koramangala, Bengaluru",
    },
    "500": {
        "city": "Hyderabad",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Jubilee Hills, Hyderabad",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Banjara Hills, Hyderabad",
    },
    "600": {
        "city": "Chennai",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Anna Nagar, Chennai",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Nungambakkam, Chennai",
    },
    "700": {
        "city": "Kolkata",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, Park Street / Salt Lake, Kolkata",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Ballygunge, Kolkata",
    },
    "380": {
        "city": "Ahmedabad",
        "bh_store_available": True,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": "Asian Paints Beautiful Homes Store, SG Highway, Ahmedabad",
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Navrangpura, Ahmedabad",
    },
    "452": {
        "city": "Indore",
        "bh_store_available": False,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": None,
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, Vijay Nagar, Indore",
    },
    "302": {
        "city": "Jaipur",
        "bh_store_available": False,
        "sleek_available": True,
        "bhps_available": True,
        "nearest_bh_store": None,
        "nearest_sleek_studio": "Sleek by Asian Paints Studio, C-Scheme, Jaipur",
    },
    "781": {
        "city": "Guwahati",
        "bh_store_available": False,
        "sleek_available": False,
        "bhps_available": True,
        "nearest_bh_store": None,
        "nearest_sleek_studio": None,
    },
}


class BHPSExecutionEngine:
    """Manages Asian Paints BHPS tool executions, slot state, and RTVI observability events."""

    def __init__(self, broadcast: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self._broadcast = broadcast
        self.active_phase = "SOP_01_PINCODE_SERVICEABILITY"
        self.slots: Dict[str, Any] = {}
        self._last_action_key: Optional[str] = None
        self._last_action_result: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()

    async def _emit(self, payload: Dict[str, Any]) -> None:
        if self._broadcast is not None:
            try:
                res = self._broadcast(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.warning(f"[AsianPaints/BHPS] UI event broadcast failed: {exc}")

    async def check_pincode_serviceability(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Check SF API serviceability for a 6-digit Indian pincode (Step 2)."""
        raw_pin = str(params.get("pincode", "")).strip()
        digits = "".join(re.findall(r"\d", raw_pin))
        pincode = digits[:6] if len(digits) >= 6 else (raw_pin or "400053")
        prefix = pincode[:3]

        if prefix in NON_SERVICEABLE_PREFIXES:
            self.slots["pincode"] = pincode
            self.slots["city"] = f"Non-Serviceable Zone ({pincode})"
            self.slots["serviceability_tier"] = "NON_SERVICEABLE"
            result = {
                "status": "non_serviceable",
                "serviceable": False,
                "retryable": False,
                "pincode": pincode,
                "city": self.slots["city"],
                "bhps_painting_serviceable": False,
                "bh_store_available": False,
                "sleek_kitchen_available": False,
                "nearest_bh_store": None,
                "nearest_sleek_studio": None,
                "toll_free_helpline": "1800-209-5678",
                "website": "www.beautifulhomes.asianpaints.com",
                "next_script_instruction": (
                    f"STEP 2 NON-SERVICEABLE RULE: Politely apologize that Asian Paints Beautiful Homes Painting Service "
                    f"is currently not serviceable in pincode {pincode}. Share our toll-free helpline 1800-209-5678 and "
                    "website www.beautifulhomes.asianpaints.com, ask if there is anything else you can help with (Step 20), "
                    "and thank them for their valuable time given to Asian Paints (Step 21)."
                ),
            }
            logger.info(f"🎨 [Asian Paints SF API] Pincode {pincode} -> NON_SERVICEABLE")
            append_diagnostic_log(
                "🎨 Asian Paints SF API",
                f"Pincode {pincode} -> NON_SERVICEABLE (Helpline 1800-209-5678)",
            )
            await self._emit({"type": "call_state", "slots": dict(self.slots)})
            return result

        info = PINCODE_PREFIX_DIRECTORY.get(prefix)
        if not info:
            # Tier-2 pincodes starting with 2/3/8: Sleek + BHPS available; others full BH Store
            sleek_only = prefix[:1] in {"2", "3", "8"} and prefix not in PINCODE_PREFIX_DIRECTORY
            info = {
                "city": f"Service Zone ({pincode})",
                "bh_store_available": not sleek_only,
                "sleek_available": True,
                "bhps_available": True,
                "nearest_bh_store": f"Asian Paints Beautiful Homes Store ({pincode})" if not sleek_only else None,
                "nearest_sleek_studio": f"Sleek by Asian Paints Studio ({pincode})",
            }

        tier = (
            "BH_STORE_AND_SLEEK_AND_BHPS"
            if info["bh_store_available"]
            else ("SLEEK_AND_BHPS" if info["sleek_available"] else "BHPS_ONLY")
        )

        self.slots["pincode"] = pincode
        self.slots["city"] = info["city"]
        self.slots["serviceability_tier"] = tier

        if info["bh_store_available"]:
            next_step = (
                "STEP 3 (BH Store Cross-Sell Check): Ask the customer: "
                "'Are you looking only for painting service or have any other interior requirements "
                "like furniture, modular kitchen, UPVC & wardrobe etc.?' "
                "If they want ONLY painting -> call switch_phase('SOP_02_SCOPE_QUALIFICATION'). "
                "If they want interiors/kitchen/wardrobe -> call switch_phase('SOP_03_INTERIORS_OR_SLEEK')."
            )
        elif info["sleek_available"]:
            next_step = (
                "STEP 4 (Sleek Cross-Sell Check): Ask the customer: "
                "'Are you looking only for painting service or have any interior requirements for modular kitchen & wardrobe?' "
                "If YES -> call switch_phase('SOP_03_INTERIORS_OR_SLEEK'). "
                "If NO (only painting) -> call switch_phase('SOP_02_SCOPE_QUALIFICATION')."
            )
        else:
            next_step = (
                "STEP 5 (BHPS Painting Only): Call switch_phase('SOP_02_SCOPE_QUALIFICATION') and ask: "
                "'Are you looking for only painting service or waterproofing solution as well?'"
            )

        result = {
            "status": "serviceable",
            "serviceable": True,
            "retryable": False,
            "pincode": pincode,
            "city": info["city"],
            "bhps_painting_serviceable": info["bhps_available"],
            "bh_store_available": info["bh_store_available"],
            "sleek_kitchen_available": info["sleek_available"],
            "nearest_bh_store": info["nearest_bh_store"],
            "nearest_sleek_studio": info["nearest_sleek_studio"],
            "next_script_instruction": next_step,
        }

        logger.info(
            f"🎨 [Asian Paints SF API] Pincode {pincode} ({info['city']}) -> "
            f"BHPS={info['bhps_available']}, BH_Store={info['bh_store_available']}, Sleek={info['sleek_available']}"
        )
        append_diagnostic_log(
            "🎨 Asian Paints SF API",
            f"Pincode {pincode} ({info['city']}) -> Tier={tier}",
        )
        await self._emit({"type": "call_state", "slots": dict(self.slots)})
        return result

    async def record_painting_qualification(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Record qualification details (Steps 5-13 or BH Store / Sleek) and return next script directive."""
        scope = str(params.get("painting_scope", "painting_only")).strip()
        civil_pending = params.get("civil_work_pending")
        civil_timeline = str(params.get("civil_completion_timeline", "")).strip()
        start_timeline = str(params.get("work_start_timeline", "")).strip()
        existing_painter = params.get("existing_painter_onboarded")
        texture_interest = params.get("one_wall_texture_interest")
        extra_details = str(params.get("interior_or_sleek_details", "")).strip()

        self.slots["painting_scope"] = scope
        if civil_pending is not None:
            self.slots["civil_work"] = (
                f"pending ({civil_timeline or 'timeline TBD'})" if civil_pending else "none"
            )
        if start_timeline:
            self.slots["start_timeline"] = start_timeline
        if existing_painter is not None:
            self.slots["existing_painter"] = "yes" if existing_painter else "no"
        if texture_interest is not None:
            self.slots["one_wall_texture_interest"] = "yes" if texture_interest else "no"
        if extra_details:
            self.slots["service_track"] = extra_details

        # Evaluate deterministic branching per BHPS_Script.xlsx
        lower_civil = civil_timeline.lower()
        lower_start = start_timeline.lower()

        if civil_pending and any(k in lower_civil for k in ["exceed", "more than 2", "> 2", ">2", "3 month", "4 month", "6 month", "after 2"]):
            next_directive = (
                "STEP 8 RULE (Civil Work > 2 Months): Tell the customer: "
                "'Sir/Ma'am, I will request you to avail this service once the civil/construction work gets completed, "
                "as only then our trained Associate will be able to share the quotation value with you. Shall I set a call-back for you?' "
                "Also check if they need UPVC doors/windows or Bathsense fittings, then call book_bhps_action(action_type='civil_work_callback_2m')."
            )
        elif any(k in lower_start for k in ["more than 1", "exceed", "> 1", ">1", "2 month", "3 month", "after 1", "later"]):
            next_directive = (
                "STEP 10 RULE (Work Start > 1 Month): Tell the customer: "
                "'We have taken a note of your enquiry and will contact you at the end of 1 month. "
                "Meanwhile, we will share the budget calculator link with you via SMS.' "
                "Call book_bhps_action(action_type='budget_calculator_callback_1m') and close warmly."
            )
        elif existing_painter is True and texture_interest is None:
            next_directive = (
                "STEP 13 RULE (Existing Painter Onboarded -> 1-Wall Highlightation Texture Pitch): Ask the customer: "
                "'You already seem to have ongoing work. Will you be interested to go ahead with 1-Wall Texture and Wallpaper "
                "from Asian Paints wherein any one wall of your choice will be given a Highlightation Texture which will make "
                "that wall look more unique and change the look of that room, wherein labour and material will be from Asian Paints for that wall?'"
            )
        elif existing_painter is True and texture_interest is False:
            next_directive = (
                "STEP 20 & 21 RULE (Customer declined 1-Wall Texture): Ask 'Is there anything else I can help you with?' "
                "and close warmly: 'Thank you for your valuable time given to Asian Paints. Have a good day ahead!'"
            )
        else:
            next_directive = (
                "STEP 14 & 15 RULE (Qualified for Associate Site Visit): Call switch_phase('SOP_04_VISIT_BOOKING'), "
                "explain the dealer associate site evaluation & trained applicator process (Step 14), and ask "
                "'So, should I go ahead and book a visit by our associate for this painting service?' (Step 15)."
            )

        result = {
            "status": "recorded",
            "retryable": False,
            "collected_slots": dict(self.slots),
            "next_script_instruction": next_directive,
        }
        logger.info(f"🎨 [Asian Paints BHPS] Qualification updated: {self.slots}")
        append_diagnostic_log(
            "🎨 BHPS Qualification",
            f"Scope={scope}, Civil={self.slots.get('civil_work', 'n/a')}, Start={start_timeline or 'n/a'}, Painter={self.slots.get('existing_painter', 'n/a')}",
        )
        await self._emit({"type": "call_state", "slots": dict(self.slots)})
        return result

    async def book_bhps_action(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute booking, BH Store / Sleek registration, callback, or declined-visit logging idempotently."""
        async with self._lock:
            action_type = str(params.get("action_type", "book_site_visit")).strip().lower()
            pincode = str(params.get("pincode") or self.slots.get("pincode") or "400053").strip()
            preferred_date = str(params.get("preferred_date") or "Within 48 hours").strip()
            preferred_time = str(params.get("preferred_time_slot") or "11:00 AM - 1:00 PM").strip()
            summary = str(
                params.get("service_summary")
                or self.slots.get("painting_scope")
                or "Asian Paints Beautiful Homes Painting Service"
            ).strip()
            notes = str(params.get("notes") or "").strip()

            dedup_key = f"{action_type}:{pincode}:{preferred_date.lower()}:{preferred_time.lower()}"
            if self._last_action_key == dedup_key and self._last_action_result is not None:
                return self._last_action_result

            ref_num = abs(hash(dedup_key)) % 90000 + 10000
            otp_code = str(abs(hash(f"otp:{dedup_key}")) % 9000 + 1000)
            city = self.slots.get("city", "Mumbai")

            if action_type == "book_site_visit":
                booking_id = f"AP-BHPS-{ref_num}"
                mandatory_script = (
                    f"1. Step 16: Confirm registration ID {booking_id} for Asian Paints Beautiful Homes Painting Service "
                    f"(preferred slot: {preferred_date}, {preferred_time}); our representative will call within 48 hours. "
                    "2. Step 17: Inform that if they choose not to proceed after speaking to the associate, the associate will share "
                    "contact details of our trusted & trained Asian Paints contractor via SMS. "
                    f"3. Step 18: Inform that they will receive an authentication verification code (OTP {otp_code}) via SMS "
                    "which they must share with the associate who connects with them."
                )
            elif action_type == "register_bh_store":
                booking_id = f"AP-BHSTORE-{ref_num}"
                mandatory_script = (
                    f"BH Store Step 6: Confirm registration {booking_id}. Store Details have been sent via SMS and Email. "
                    "Our in-store team from the Asian Paints Beautiful Homes Store will call within 48 hours, and they can also visit the store."
                )
            elif action_type in ("register_sleek_kitchen", "register_sleek_components"):
                booking_id = f"AP-SLEEK-{ref_num}"
                mandatory_script = (
                    f"Sleek Step 12: Confirm registration {booking_id}. Store details have been sent via SMS. "
                    "Our Sleek designer will connect within 48 hours for site measurement and 2D & 3D kitchen/wardrobe designs "
                    "(plus eligibility for the free kitchen appliance offer worth up to ₹1,00,000 on kitchen booking)."
                )
            elif action_type in ("budget_calculator_callback_1m", "civil_work_callback_2m", "bh_store_possession_callback_2m", "schedule_callback"):
                booking_id = f"AP-CB-{ref_num}"
                mandatory_script = (
                    f"Callback {booking_id} scheduled ({action_type}). Budget Calculator link / Asian Paints details dispatched via SMS. "
                    "Proceed to Step 20 & 21 closing."
                )
            else:
                booking_id = f"AP-CONTRACTOR-SMS-{ref_num}"
                mandatory_script = (
                    f"Step 19: Reason noted ({notes or 'customer preference'}). Inform customer: "
                    "'We understand you have not chosen to go ahead with Asian Paints Beautiful Homes Painting Service. "
                    "As a part of our service offering, we will shortly share contact details of our trusted & trained "
                    "Asian Paints contractor via SMS to provide the best assistance for your painting needs.'"
                )

            self.slots["visit_slot"] = f"{preferred_date} ({preferred_time})"
            self.slots["booking_status"] = action_type
            self.slots["booking_id"] = booking_id

            result = {
                "status": "confirmed",
                "retryable": False,
                "action_type": action_type,
                "booking_id": booking_id,
                "verification_code": otp_code,
                "pincode": pincode,
                "city": city,
                "preferred_date": preferred_date,
                "preferred_time_slot": preferred_time,
                "service_summary": summary,
                "sms_dispatched": True,
                "associate_call_sla": "Within 48 hours",
                "toll_free_helpline": "1800-209-5678",
                "website": "www.beautifulhomes.asianpaints.com",
                "mandatory_spoken_points": mandatory_script,
            }

            self._last_action_key = dedup_key
            self._last_action_result = result

            logger.info(
                f"🎨 [Asian Paints BHPS] Action '{action_type}' confirmed -> "
                f"ID={booking_id}, PIN={pincode}, Slot='{preferred_date} {preferred_time}'"
            )
            append_diagnostic_log(
                "🎨 BHPS Booking",
                f"Action={action_type}, ID={booking_id}, PIN={pincode} ({city}), Slot='{preferred_date} {preferred_time}'",
            )
            await self._emit({"type": "call_state", "slots": dict(self.slots)})
            await self._emit({
                "type": "booking_confirmed",
                "booking_id": booking_id,
                "center_name": f"Asian Paints BHPS ({city} - {pincode})",
                "city": city,
                "date": preferred_date,
                "time": preferred_time,
                "vehicle_variant": summary,
            })
            return result
