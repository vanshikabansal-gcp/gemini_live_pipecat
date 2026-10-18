"""Tools and execution engine for Pandit Raghavendra Ji (Astrotalk Vedic Astrology & Kundli Advisor).

Provides deterministic Janam Kundli / Prashna Kundli calculation, Vimshottari Mahadasha &
Gochar transit analysis across Career, Marriage/Love (including Gun Milan), Wealth/Business,
Health & Foreign Settlement, plus Saatvik Vedic Upay, AstroMall Gemstone & Online E-Pooja
booking with full RTVI event broadcasting and Observability logging.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

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

generate_janam_kundli_schema = types.FunctionDeclaration(
    name="generate_janam_kundli",
    description=(
        "Calculate the caller's Vedic Janam Kundli (or Prashna Kundli if birth time is unknown) on Astrotalk. "
        "Returns Lagna (Ascendant), Chandra Rashi (Moon Sign), Janam Nakshatra & Pada, current Vimshottari "
        "Mahadasha & Antardasha, key planetary placements across houses, active Yogas (Gajakesari, Budhaditya, "
        "Raj Yoga), and Dosha status (Manglik / Shani Sade Sati)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "caller_name": types.Schema(
                type=types.Type.STRING,
                description="Caller's name if shared (optional, e.g., 'Rahul', 'Priya').",
            ),
            "birth_date": types.Schema(
                type=types.Type.STRING,
                description="Date of birth (e.g., '15 August 1995', '12-03-1992', or 'unknown' for Prashna Kundli).",
            ),
            "birth_time": types.Schema(
                type=types.Type.STRING,
                description="Time of birth (e.g., '10:30 AM', '7:45 PM', or 'approximate/unknown').",
            ),
            "birth_place": types.Schema(
                type=types.Type.STRING,
                description="City/Place of birth (e.g., 'Delhi', 'Mumbai', 'Jaipur', 'Pune').",
            ),
            "primary_concern": types.Schema(
                type=types.Type.STRING,
                description="Primary topic the caller wants to consult about: 'career', 'marriage', 'love', 'wealth', 'health', or 'general'.",
            ),
        },
        required=["birth_date", "birth_place"],
    ),
)

analyze_kundli_question_schema = types.FunctionDeclaration(
    name="analyze_kundli_question",
    description=(
        "Perform a deep Vedic astrological analysis for a specific life domain or question on Astrotalk: "
        "'career' (10th house Karma Bhava, promotion, job switch, abroad), 'marriage' or 'love' (7th house "
        "Kalatra Bhava, marriage timing, love vs arranged, Gun Milan / partner compatibility), 'wealth' "
        "(2nd/11th house Dhana/Labha Yoga, business, property), or 'health' (6th/8th house & mental peace). "
        "Returns house lord analysis, Gochar (transit) impact of Jupiter/Saturn/Rahu, and exact auspicious timelines."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "domain": types.Schema(
                type=types.Type.STRING,
                description="Consultation domain: 'career', 'marriage', 'love', 'wealth', 'property', 'foreign', or 'health'.",
            ),
            "specific_question": types.Schema(
                type=types.Type.STRING,
                description="The caller's specific question or situation.",
            ),
            "partner_details": types.Schema(
                type=types.Type.STRING,
                description="Optional partner name or birth details if asking about Love / Marriage / Kundli Milan.",
            ),
        },
        required=["domain", "specific_question"],
    ),
)

recommend_vedic_remedies_schema = types.FunctionDeclaration(
    name="recommend_vedic_remedies",
    description=(
        "Prescribe personalized, Saatvik Vedic remedies (Upay) for the caller's Kundli and optionally book an "
        "Astrotalk Online VIP E-Pooja, AstroMall energized Gemstone/Rudraksha recommendation, or dispatch the "
        "Kundli PDF & Remedy Summary to the caller's Astrotalk app chat."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "remedy_focus": types.Schema(
                type=types.Type.STRING,
                description="Focus area for remedies: 'career_growth', 'marriage_harmony', 'wealth_stability', 'peace_and_graha_shanti', or 'general_blessing'.",
            ),
            "include_gemstone": types.Schema(
                type=types.Type.BOOLEAN,
                description="True if caller asked about or benefits from a natural Ratna (Gemstone) or Rudraksha.",
            ),
            "include_epooja": types.Schema(
                type=types.Type.BOOLEAN,
                description="True if booking or recommending an Astrotalk Online E-Pooja / Graha Shanti Sankalp.",
            ),
            "preferred_pooja_date": types.Schema(
                type=types.Type.STRING,
                description="Preferred day/date for the E-Pooja Sankalp or follow-up consultation (e.g., 'Coming Thursday', 'Shukla Paksha Ekadashi').",
            ),
        },
        required=["remedy_focus"],
    ),
)

switch_phase_schema = types.FunctionDeclaration(
    name="switch_phase",
    description=(
        "Transition the active Astrotalk Panditji SOP phase and inject its JIT phase card before replying. "
        "Allowed phases: 'SOP_01_BIRTH_DETAILS', 'SOP_02_KUNDLI_DASHAS', "
        "'SOP_03_LIFE_PREDICTION', 'SOP_04_VEDIC_REMEDIES'."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "phase_id": types.Schema(
                type=types.Type.STRING,
                description=(
                    "Target phase identifier: 'SOP_01_BIRTH_DETAILS', 'SOP_02_KUNDLI_DASHAS', "
                    "'SOP_03_LIFE_PREDICTION', or 'SOP_04_VEDIC_REMEDIES'."
                ),
            ),
        },
        required=["phase_id"],
    ),
)


# ---------------------------------------------------------------------------
# Deterministic Vedic Astrology Catalogs
# ---------------------------------------------------------------------------

_LAGNA_PROFILES: List[Dict[str, Any]] = [
    {
        "lagna": "Vrishabha (Taurus) Lagna",
        "lagna_lord": "Shukra (Venus)",
        "rashi": "Karka (Cancer) Rashi",
        "nakshatra": "Pushya Nakshatra (Charan 2)",
        "mahadasha": "Guru (Jupiter) Mahadasha — Shukra (Venus) Antardasha",
        "dasha_window": "Active until November 2027 (Major Raj Yoga activation from next month)",
        "planetary_highlights": (
            "Lagnesh Shukra sits in the 10th house (Karma Bhava) with Budha forming Lakshmi-Narayan Yoga; "
            "Chandra in 3rd house with Guru aspecting 11th house (Gajakesari Yoga); Surya in 11th house Labha Bhava."
        ),
        "dosha_check": "Non-Manglik (Mangal in 6th house Upachaya gives victory over obstacles); Shani Sade Sati is NOT active.",
    },
    {
        "lagna": "Simha (Leo) Lagna",
        "lagna_lord": "Surya (Sun)",
        "rashi": "Vrishchika (Scorpio) Rashi",
        "nakshatra": "Anuradha Nakshatra (Charan 3)",
        "mahadasha": "Shani (Saturn) Mahadasha — Budha (Mercury) Antardasha",
        "dasha_window": "Active until August 2027 (Strong career elevation & authority yoga)",
        "planetary_highlights": (
            "Surya and Budha conjoined in 10th house forming a powerful Budhaditya Raj Yoga; "
            "Yogakaraka Mangal placed in 9th house (Bhagya Bhava); Guru aspects the 5th and 9th houses."
        ),
        "dosha_check": "Anshik (Mild) Manglik — fully cancelled by Guru's 5th aspect (Drishti); Shani Sade Sati completed.",
    },
    {
        "lagna": "Kanya (Virgo) Lagna",
        "lagna_lord": "Budha (Mercury)",
        "rashi": "Meena (Pisces) Rashi",
        "nakshatra": "Revati Nakshatra (Charan 1)",
        "mahadasha": "Budha (Mercury) Mahadasha — Guru (Jupiter) Antardasha",
        "dasha_window": "Active until January 2028 (Bhadra Mahapurusha & Hamsa Yoga window)",
        "planetary_highlights": (
            "Lagnesh Budha exalted in Lagna (Bhadra Yoga); Guru in Kendra forming Hamsa Yoga; "
            "Shukra in 9th house (Trikona) blessing fortune, partnerships, and foreign connections."
        ),
        "dosha_check": "No Manglik Dosha; Final phase of Sade Sati bringing tangible rewards for past hard work.",
    },
    {
        "lagna": "Dhanu (Sagittarius) Lagna",
        "lagna_lord": "Guru (Jupiter)",
        "rashi": "Mesha (Aries) Rashi",
        "nakshatra": "Ashwini Nakshatra (Charan 2)",
        "mahadasha": "Shukra (Venus) Mahadasha — Surya (Sun) Antardasha",
        "dasha_window": "Active until September 2027 (9th Lord Surya activating Bhagya & leadership)",
        "planetary_highlights": (
            "Lagnesh Guru in 5th house (Trikona) with Chandra forming Gajakesari Yoga; "
            "Surya and Mangal strong in Kendra giving administrative authority and rapid career rise."
        ),
        "dosha_check": "Clear chart with strong Trikona protection (Guru's 9th aspect removes all obstacles).",
    },
]

_DOMAIN_PREDICTIONS: Dict[str, Dict[str, Any]] = {
    "career": {
        "house_focus": "10th House (Karma Bhava) & 6th/11th House (Artha Trikona)",
        "transit_insight": (
            "Guru (Jupiter) is transiting favorably over your natal Karma/Labha axis while Shani strengthens "
            "long-term authority. Recent stagnation or workplace friction over the past 4-5 months was due to "
            "Rahu's sub-period, which is now separating."
        ),
        "auspicious_timeline": (
            "Next 6 to 14 weeks (especially after the upcoming Purnima) show a high-probability window for "
            "promotion, role expansion, or a higher-package switch."
        ),
        "counsel": (
            "Avoid impulsive resignations on Tuesdays or Saturdays; initiate key interviews or compensation "
            "discussions on Wednesdays or Thursdays during Abhijit Muhurat."
        ),
    },
    "marriage": {
        "house_focus": "7th House (Kalatra Bhava), Karak Shukra/Guru & Navamsha (D9) Chart",
        "transit_insight": (
            "Your 7th house lord is well-supported in the Navamsha (D9) chart, indicating a loyal, educated, "
            "and family-oriented life partner. Delays or last-minute hiccups in talks were caused by Shani's "
            "aspect on the 7th house, which ensures maturity before commitment."
        ),
        "auspicious_timeline": (
            "A strong Vivah Yoga (marriage/commitment window) opens over the next 3 to 9 months as Guru's "
            "blessing activates your 7th house."
        ),
        "gun_milan_note": (
            "Ashtakoot Gun Milan shows 27.5 out of 36 Gunas (Uttam Milan) with strong Nadi and Bhakoot harmony."
        ),
        "counsel": (
            "Both love-cum-arranged and arranged prospects are favorable; look for a partner with strong "
            "communication and mutual respect."
        ),
    },
    "love": {
        "house_focus": "5th House (Prema Bhava) & 7th House (Kalatra Bhava)",
        "transit_insight": (
            "5th lord and Shukra show deep emotional sincerity, though recent communication misunderstandings "
            "stemmed from Ketu's transit influence causing overthinking."
        ),
        "auspicious_timeline": (
            "Emotional clarity and reconciliation strengthen significantly within the next 3 to 6 weeks."
        ),
        "gun_milan_note": (
            "Synastry between Moon signs is harmonious (Mitra Rashi), supporting long-term stability if ego clashes are avoided."
        ),
        "counsel": (
            "Communicate openly on Fridays; avoid heated arguments late at night during Amavasya or Rahu Kaal."
        ),
    },
    "wealth": {
        "house_focus": "2nd House (Dhana Bhava), 11th House (Labha Bhava) & 9th House (Bhagya)",
        "transit_insight": (
            "Dhana Yoga between your 2nd and 11th lords is strong. Money inflows are good, but sudden unplanned "
            "expenses (12th house activation) have prevented savings from compounding at full potential."
        ),
        "auspicious_timeline": (
            "Financial liquidity and business/investment gains accelerate noticeably over the next 2 to 5 months."
        ),
        "counsel": (
            "Focus on disciplined long-term assets, property, or gold accumulation rather than speculative short-term trades."
        ),
    },
    "health": {
        "house_focus": "1st House (Tanu Bhava), 6th House (Roga Bhava) & Chandra (Mind/Sleep)",
        "transit_insight": (
            "Lagnesh is resilient, protecting overall vitality. Current fatigue, disturbed sleep, or digestive/neck "
            "tension is primarily stress-driven from Chandra-Rahu sensitivity."
        ),
        "auspicious_timeline": (
            "Energy levels and mental peace improve steadily within 3 to 4 weeks once daily routine and sleep stabilize."
        ),
        "counsel": (
            "Prioritize morning sunlight (Surya Arghya), hydration in a silver/copper vessel, and 10 minutes of Pranayama."
        ),
    },
}


class AstrotalkPanditjiEngine:
    """Manages Astrotalk Panditji Kundli calculations, Dasha analysis, Vedic remedies, and RTVI events."""

    def __init__(self, broadcast: Optional[Callable[[Dict[str, Any]], Any]] = None):
        self._broadcast = broadcast
        self.active_phase = "SOP_01_BIRTH_DETAILS"
        self.slots: Dict[str, Any] = {}
        self._last_remedy_key: Optional[str] = None
        self._last_remedy_result: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()

    async def _emit(self, payload: Dict[str, Any]) -> None:
        if self._broadcast is not None:
            try:
                res = self._broadcast(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.warning(f"[Astrotalk/Panditji] UI event broadcast failed: {exc}")

    async def generate_janam_kundli(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Generate the caller's Janam Kundli (or Prashna Kundli) from birth details."""
        caller_name = str(params.get("caller_name") or "Yajman").strip()
        birth_date = str(params.get("birth_date") or "15 August 1995").strip()
        birth_time = str(params.get("birth_time") or "10:30 AM (Prashna/Approx)").strip()
        birth_place = str(params.get("birth_place") or "New Delhi").strip()
        primary_concern = str(params.get("primary_concern") or "general").strip().lower()

        seed = abs(hash(f"{birth_date.lower()}:{birth_place.lower()}"))
        profile = _LAGNA_PROFILES[seed % len(_LAGNA_PROFILES)]
        is_prashna = "unknown" in birth_date.lower() or "unknown" in birth_time.lower()
        chart_type = "Prashna Kundli & Chandra Kundli" if is_prashna else "Vedic Janam Kundli (D1 + D9 Navamsha)"

        self.slots["caller_name"] = caller_name
        self.slots["birth_details"] = f"{birth_date}, {birth_time}, {birth_place}"
        self.slots["lagna"] = profile["lagna"]
        self.slots["rashi"] = profile["rashi"]
        self.slots["nakshatra"] = profile["nakshatra"]
        self.slots["mahadasha"] = profile["mahadasha"]
        if primary_concern:
            self.slots["primary_concern"] = primary_concern

        result = {
            "status": "kundli_generated",
            "retryable": False,
            "chart_type": chart_type,
            "caller_name": caller_name,
            "birth_date": birth_date,
            "birth_time": birth_time,
            "birth_place": birth_place,
            "lagna": profile["lagna"],
            "lagna_lord": profile["lagna_lord"],
            "chandra_rashi": profile["rashi"],
            "nakshatra": profile["nakshatra"],
            "current_dasha": profile["mahadasha"],
            "dasha_timeline": profile["dasha_window"],
            "planetary_placements": profile["planetary_highlights"],
            "dosha_and_sade_sati": profile["dosha_check"],
            "next_script_instruction": (
                "Share the caller's Lagna, Rashi, Nakshatra, and current Mahadasha-Antardasha warmly in 1-2 reassuring "
                "sentences, highlight their positive Raj Yoga / Gajakesari Yoga, and ask what specific question in "
                f"{primary_concern or 'career, marriage, or finance'} they would like to explore first. "
                "Do NOT call switch_phase (SOP_02_KUNDLI_DASHAS is already active)."
            ),
        }

        logger.info(
            f"🕉️ [Astrotalk Kundli] {caller_name} ({birth_date}, {birth_time}, {birth_place}) -> "
            f"{profile['lagna']}, {profile['rashi']}, {profile['mahadasha']}"
        )
        append_diagnostic_log(
            "🕉️ Astrotalk Kundli Engine",
            f"{chart_type}: {profile['lagna']} | {profile['rashi']} | {profile['mahadasha']}",
        )
        await self._emit({"type": "call_state", "slots": dict(self.slots)})
        return result

    async def analyze_kundli_question(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a specific life domain question using Dasha and Gochar transits."""
        raw_domain = str(params.get("domain") or "career").strip().lower()
        specific_question = str(params.get("specific_question") or "future prospects").strip()
        partner_details = str(params.get("partner_details") or "").strip()

        if any(k in raw_domain for k in ("marri", "vivah", "shaadi", "match", "milan", "spouse")):
            domain_key = "marriage"
        elif any(k in raw_domain for k in ("love", "relat", "partner")):
            domain_key = "love"
        elif any(k in raw_domain for k in ("wealth", "money", "finan", "business", "propert", "invest")):
            domain_key = "wealth"
        elif any(k in raw_domain for k in ("health", "peace", "stress", "medical")):
            domain_key = "health"
        else:
            domain_key = "career"

        insight = _DOMAIN_PREDICTIONS[domain_key]
        self.slots["primary_concern"] = domain_key
        self.slots["predicted_window"] = insight["auspicious_timeline"]
        if partner_details:
            self.slots["partner_details"] = partner_details

        result = {
            "status": "analysis_complete",
            "retryable": False,
            "domain": domain_key,
            "specific_question": specific_question,
            "house_focus": insight["house_focus"],
            "active_dasha": self.slots.get("mahadasha", "Guru Mahadasha — Shukra Antardasha"),
            "gochar_transit_insight": insight["transit_insight"],
            "auspicious_timeline": insight["auspicious_timeline"],
            "practical_counsel": insight["counsel"],
            **({"gun_milan_compatibility": insight["gun_milan_note"]} if "gun_milan_note" in insight else {}),
            "next_script_instruction": (
                "Explain the astrological reason (citing the house and current Dasha/Gochar transit) and the exact "
                "favorable timeline in 1-2 warm, uplifting sentences. Then ask if they would like simple Saatvik "
                "Vedic Upay (remedies) to strengthen this yoga, and WAIT for the caller to reply before calling "
                "`recommend_vedic_remedies`. Do NOT call switch_phase (SOP_03_LIFE_PREDICTION is already active)."
            ),
        }

        logger.info(f"🕉️ [Astrotalk Dasha Analysis] Domain={domain_key} -> Window='{insight['auspicious_timeline']}'")
        append_diagnostic_log(
            "🕉️ Astrotalk Transit Analysis",
            f"Domain={domain_key} | Focus={insight['house_focus']}",
        )
        await self._emit({"type": "call_state", "slots": dict(self.slots)})
        return result

    async def recommend_vedic_remedies(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Prescribe Saatvik Vedic Upay, Gemstone/Rudraksha guidance, and log Astrotalk consultation summary."""
        async with self._lock:
            focus = str(params.get("remedy_focus") or self.slots.get("primary_concern") or "general_blessing").strip().lower()
            include_gemstone = bool(params.get("include_gemstone", True))
            include_epooja = bool(params.get("include_epooja", False))
            pooja_date = str(params.get("preferred_pooja_date") or "Coming Thursday (Shubh Muhurat)").strip()

            dedup_key = f"{focus}:{include_gemstone}:{include_epooja}:{pooja_date.lower()}"
            if self._last_remedy_key == dedup_key and self._last_remedy_result is not None:
                return self._last_remedy_result

            ref_num = abs(hash(dedup_key)) % 90000 + 10000
            consultation_id = f"AT-KUNDLI-{ref_num}"

            if "marri" in focus or "love" in focus:
                mantra = "Chant 'Om Brim Brihaspataye Namah' or 'Om Shum Shukraya Namah' 108 times on Thursdays/Fridays."
                daan = "Offer yellow chana dal or white sweets at a temple on Thursdays and light a cow-ghee lamp."
                gemstone = "Natural Pukhraj (Yellow Sapphire, 5.25 Ratti in Gold on index finger on Thursday morning) or 6-Mukhi Rudraksha via Astrotalk AstroMall."
                pooja_name = "Astrotalk Shukra-Guru Vivah & Sambandh Siddhi Online E-Pooja"
            elif "wealth" in focus or "business" in focus:
                mantra = "Recite Kanakadhara Stotram or 'Om Shreem Hreem Shreem Kamale Kamalalaye Praseed Praseed' on Fridays."
                daan = "Feed green fodder to cows (Gau Seva) on Wednesdays and keep a Shree Yantra in the North-East (Ishaan Kon)."
                gemstone = "Natural Panna (Emerald, 5.25 Ratti in Gold/Silver on little finger on Wednesday) or 7-Mukhi Rudraksha via Astrotalk AstroMall."
                pooja_name = "Astrotalk Mahalakshmi Kubera Dhana Samriddhi Online E-Pooja"
            elif "health" in focus or "peace" in focus:
                mantra = "Chant Mahamrityunjaya Mantra 21 times daily and offer जल (water) to Surya Dev in a copper vessel every morning."
                daan = "Donate black sesame (kale til) or mustard oil on Saturdays and perform Rudrabhishek."
                gemstone = "5-Mukhi energized Nepali Rudraksha on a red thread on Monday morning via Astrotalk AstroMall."
                pooja_name = "Astrotalk Navagraha Shanti & Mahamrityunjaya Online E-Pooja"
            else:
                mantra = "Offer Arghya (water with kumkum) to rising Surya Dev daily and chant Aditya Hridaya Stotra or 'Om Gram Greem Groum Sah Gurave Namah' on Thursdays."
                daan = "Feed birds (pakshiyon ko satnaja) in the morning and offer turmeric/chana dal on Thursdays."
                gemstone = "Natural Pukhraj (Yellow Sapphire) or Manikya (Ruby, 5.25 Ratti) after trial, or 5-Mukhi Rudraksha via Astrotalk AstroMall."
                pooja_name = "Astrotalk Baglamukhi & Navagraha Karya Siddhi Online E-Pooja"

            self.slots["remedy_prescribed"] = mantra
            self.slots["gemstone_guidance"] = gemstone if include_gemstone else "Saatvik Mantra & Daan only"
            self.slots["pooja_or_booking"] = pooja_name if include_epooja else "Kundli & Upay Summary Dispatched"
            self.slots["consultation_id"] = consultation_id

            result = {
                "status": "remedies_prescribed",
                "retryable": False,
                "consultation_id": consultation_id,
                "remedy_focus": focus,
                "saatvik_mantra_upay": mantra,
                "daan_and_lifestyle_upay": daan,
                "astromall_gemstone_rudraksha": gemstone if include_gemstone else None,
                "online_epooja": pooja_name if include_epooja else None,
                "scheduled_muhurat": pooja_date,
                "kundli_pdf_dispatched_to_app": True,
                "next_action": "speak_remedies_and_warm_blessing_naturally",
                "voice_rule": (
                    "Deliver the remedies and blessing warmly to the caller in their Indian language, then ask if they "
                    "have any other question. Do NOT call switch_phase or recommend_vedic_remedies again. "
                    "Never say 'this is complete', 'this is already complete', or 'consultation complete'."
                ),
                "mandatory_spoken_points": (
                    f"1. Share 1-2 simple Saatvik remedies ({mantra} and {daan}). "
                    + (f"2. Mention optional gemstone/Rudraksha guidance ({gemstone}). " if include_gemstone else "")
                    + (f"3. Confirm {pooja_name} Sankalp for {pooja_date} (Ref {consultation_id}). " if include_epooja else "")
                    + f"4. Inform the caller that their full Janam Kundli PDF and written Upay summary ({consultation_id}) "
                    "have been sent to their Astrotalk chat, bless them warmly ('ईश्वर आपकी सभी मनोकामनाएं पूर्ण करें, शुभम् भवतु!'), "
                    "and ask if they have any other question ('क्या आपका कोई और प्रश्न है?'). "
                    "Do NOT call switch_phase or any tool after speaking, and NEVER say 'This is complete' or 'This is already complete'."
                ),
            }

            self._last_remedy_key = dedup_key
            self._last_remedy_result = result

            logger.info(
                f"🕉️ [Astrotalk Remedies] ID={consultation_id}, Focus={focus}, "
                f"Gemstone={include_gemstone}, EPooja={include_epooja}"
            )
            append_diagnostic_log(
                "🕉️ Astrotalk Vedic Remedies",
                f"ID={consultation_id} | Focus={focus} | Muhurat={pooja_date}",
            )
            await self._emit({"type": "call_state", "slots": dict(self.slots)})
            await self._emit({
                "type": "booking_confirmed",
                "booking_id": consultation_id,
                "center_name": "Astrotalk Vedic Consultation & AstroMall",
                "city": self.slots.get("birth_details", "India"),
                "date": pooja_date,
                "time": "Abhijit Shubh Muhurat",
                "vehicle_variant": pooja_name if include_epooja else f"Janam Kundli & Vedic Upay ({focus})",
            })
            return result
