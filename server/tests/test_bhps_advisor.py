import asyncio
import unittest
from typing import Any, Dict
from unittest.mock import AsyncMock

from persona_tools.bhps_advisor import BHPSExecutionEngine
from persona_registry import AsianPaintsBHPSArchitecture


class TestBHPSAdvisorEngine(unittest.TestCase):
    def setUp(self):
        self.events = []

        async def emit_event(event: Dict[str, Any]):
            self.events.append(event)

        self.engine = BHPSExecutionEngine(broadcast=emit_event)

    def test_pincode_serviceability_bh_store(self):
        result = asyncio.run(
            self.engine.check_pincode_serviceability(
                {
                    "pincode": "400050",
                    "customer_interest": "paint_plus_interiors",
                }
            )
        )
        self.assertEqual(result["status"], "serviceable")
        self.assertTrue(result["serviceable"])
        self.assertFalse(result["retryable"])
        self.assertEqual(result["pincode"], "400050")
        self.assertTrue(result["bh_store_available"])
        self.assertTrue(result["sleek_kitchen_available"])
        self.assertIn("STEP 3 (BH Store Cross-Sell Check)", result["next_script_instruction"])
        self.assertEqual(self.engine.slots["pincode"], "400050")
        self.assertEqual(self.engine.slots["serviceability_tier"], "BH_STORE_AND_SLEEK_AND_BHPS")

    def test_pincode_serviceability_sleek(self):
        result = asyncio.run(
            self.engine.check_pincode_serviceability(
                {
                    "pincode": "452001",
                    "customer_interest": "paint_plus_interiors",
                }
            )
        )
        self.assertEqual(result["status"], "serviceable")
        self.assertTrue(result["serviceable"])
        self.assertFalse(result["bh_store_available"])
        self.assertTrue(result["sleek_kitchen_available"])
        self.assertIn("STEP 4 (Sleek Cross-Sell Check)", result["next_script_instruction"])
        self.assertEqual(self.engine.slots["serviceability_tier"], "SLEEK_AND_BHPS")

    def test_pincode_serviceability_bhps_only(self):
        result = asyncio.run(
            self.engine.check_pincode_serviceability(
                {
                    "pincode": "781001",
                    "customer_interest": "painting_only",
                }
            )
        )
        self.assertEqual(result["status"], "serviceable")
        self.assertTrue(result["serviceable"])
        self.assertFalse(result["bh_store_available"])
        self.assertFalse(result["sleek_kitchen_available"])
        self.assertTrue(result["bhps_painting_serviceable"])
        self.assertIn("STEP 5 (BHPS Painting Only)", result["next_script_instruction"])
        self.assertEqual(self.engine.slots["serviceability_tier"], "BHPS_ONLY")

    def test_pincode_non_serviceable(self):
        result = asyncio.run(
            self.engine.check_pincode_serviceability(
                {
                    "pincode": "744101",
                }
            )
        )
        self.assertEqual(result["status"], "non_serviceable")
        self.assertFalse(result["serviceable"])
        self.assertFalse(result["retryable"])
        self.assertFalse(result["bhps_painting_serviceable"])
        self.assertEqual(result["toll_free_helpline"], "1800-209-5678")
        self.assertIn("STEP 2 NON-SERVICEABLE RULE", result["next_script_instruction"])
        self.assertEqual(self.engine.slots["serviceability_tier"], "NON_SERVICEABLE")

    def test_record_painting_qualification_civil_work_over_2_months(self):
        result = asyncio.run(
            self.engine.record_painting_qualification(
                {
                    "painting_scope": "painting_only",
                    "civil_work_pending": True,
                    "civil_completion_timeline": "more than 2 months",
                }
            )
        )
        self.assertEqual(result["status"], "recorded")
        self.assertFalse(result["retryable"])
        self.assertIn("STEP 8 RULE (Civil Work > 2 Months)", result["next_script_instruction"])

    def test_record_painting_qualification_start_timeline_over_1_month(self):
        result = asyncio.run(
            self.engine.record_painting_qualification(
                {
                    "painting_scope": "painting_and_waterproofing",
                    "civil_work_pending": False,
                    "work_start_timeline": "more than 1 month",
                }
            )
        )
        self.assertEqual(result["status"], "recorded")
        self.assertIn("STEP 10 RULE (Work Start > 1 Month)", result["next_script_instruction"])

    def test_record_painting_qualification_existing_painter_1_wall_pitch(self):
        result = asyncio.run(
            self.engine.record_painting_qualification(
                {
                    "painting_scope": "both",
                    "civil_work_pending": False,
                    "work_start_timeline": "within 1 month",
                    "existing_painter_onboarded": True,
                }
            )
        )
        self.assertEqual(result["status"], "recorded")
        self.assertIn("1-Wall Highlightation Texture Pitch", result["next_script_instruction"])

    def test_book_bhps_action_site_visit(self):
        asyncio.run(self.engine.check_pincode_serviceability({"pincode": "400050"}))
        result = asyncio.run(
            self.engine.book_bhps_action(
                {
                    "action_type": "book_site_visit",
                    "preferred_date": "Saturday",
                    "preferred_time_slot": "11:00 AM - 1:00 PM",
                }
            )
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(result["retryable"])
        self.assertTrue(result["booking_id"].startswith("AP-BHPS-"))
        self.assertTrue(result["sms_dispatched"])
        self.assertEqual(result["toll_free_helpline"], "1800-209-5678")
        self.assertEqual(result["website"], "www.beautifulhomes.asianpaints.com")
        self.assertIn("verification code", result["mandatory_spoken_points"])
        self.assertTrue(any(e.get("type") == "booking_confirmed" for e in self.events))

    def test_book_bhps_action_bh_store_and_sleek_and_declined(self):
        # 1. BH Store registration (Sheet 2 Step 6)
        res_bh = asyncio.run(
            self.engine.book_bhps_action(
                {
                    "action_type": "register_bh_store",
                    "pincode": "400053",
                    "service_summary": "BH Store: Furniture, Modular Kitchen, UPVC Windows",
                }
            )
        )
        self.assertTrue(res_bh["booking_id"].startswith("AP-BHSTORE-"))
        self.assertIn("Store Details have been sent via SMS and Email", res_bh["mandatory_spoken_points"])

        # 2. Sleek components registration (Sheet 3 Step 14)
        res_sleek = asyncio.run(
            self.engine.book_bhps_action(
                {
                    "action_type": "register_sleek_components",
                    "pincode": "452001",
                    "service_summary": "Sleek Hardware, Wire Baskets & Chimney",
                }
            )
        )
        self.assertTrue(res_sleek["booking_id"].startswith("AP-SLEEK-"))
        self.assertIn("2D & 3D kitchen/wardrobe designs", res_sleek["mandatory_spoken_points"])

        # 3. Declined appointment -> Trusted contractor SMS (Sheet 1 Step 19)
        res_declined = asyncio.run(
            self.engine.book_bhps_action(
                {
                    "action_type": "log_declined_appointment",
                    "pincode": "560001",
                    "notes": "Customer wants to consult family first",
                }
            )
        )
        self.assertTrue(res_declined["booking_id"].startswith("AP-CONTRACTOR-SMS-"))
        self.assertIn("trusted & trained Asian Paints contractor via SMS", res_declined["mandatory_spoken_points"])

    def test_architecture_auto_phase_transitions(self):
        arch = AsianPaintsBHPSArchitecture()
        llm = AsyncMock()
        llm.inject_directive = AsyncMock(return_value=True)
        events = []

        async def broadcast(ev):
            events.append(ev)

        handlers = {}
        llm.register_function = lambda name, fn: handlers.setdefault(name, fn)
        arch.register_handlers(llm, broadcast=broadcast, engine="live")

        class DummyParams:
            def __init__(self, arguments):
                self.arguments = arguments
                self.result = None

            async def result_callback(self, res):
                self.result = res

        # 1. BHPS-only pincode (781001) auto-transitions to SOP_02_SCOPE_QUALIFICATION
        p1 = DummyParams({"pincode": "781001"})
        asyncio.run(handlers["check_pincode_serviceability"](p1))
        self.assertEqual(arch.engine.active_phase, "SOP_02_SCOPE_QUALIFICATION")

        # 2. Qualified record_painting_qualification auto-transitions to SOP_04_VISIT_BOOKING
        p2 = DummyParams(
            {
                "painting_scope": "painting_and_waterproofing",
                "civil_work_pending": False,
                "work_start_timeline": "within 1 month",
                "existing_painter_onboarded": False,
            }
        )
        asyncio.run(handlers["record_painting_qualification"](p2))
        self.assertEqual(arch.engine.active_phase, "SOP_04_VISIT_BOOKING")
        self.assertTrue(any(e.get("type") == "phase_transition" and e.get("phase_id") == "SOP_04_VISIT_BOOKING" for e in events))


if __name__ == "__main__":
    unittest.main()
