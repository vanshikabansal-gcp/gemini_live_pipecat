"""Unit tests for Astrotalk Panditji execution engine (Janam Kundli, Dasha Analysis, Vedic Upay)."""

import unittest

from persona_tools.astrotalk_panditji import AstrotalkPanditjiEngine


class TestAstrotalkPanditji(unittest.IsolatedAsyncioTestCase):
    async def test_generate_janam_kundli_and_prashna_fallback(self):
        events = []
        engine = AstrotalkPanditjiEngine(broadcast=lambda ev: events.append(ev))

        res = await engine.generate_janam_kundli({
            "caller_name": "Aarav",
            "birth_date": "15 August 1995",
            "birth_time": "10:30 AM",
            "birth_place": "Delhi",
            "primary_concern": "career",
        })
        self.assertEqual(res["status"], "kundli_generated")
        self.assertIn("Lagna", res["lagna"])
        self.assertIn("Rashi", res["chandra_rashi"])
        self.assertIn("Nakshatra", res["nakshatra"])
        self.assertIn("Mahadasha", res["current_dasha"])
        self.assertTrue(any(e.get("type") == "call_state" for e in events))

        # Prashna Kundli when birth time is unknown
        res_prashna = await engine.generate_janam_kundli({
            "birth_date": "10 May 1998",
            "birth_time": "unknown",
            "birth_place": "Mumbai",
        })
        self.assertIn("Prashna Kundli", res_prashna["chart_type"])

    async def test_analyze_kundli_question_domains(self):
        engine = AstrotalkPanditjiEngine()
        for domain in ("career", "marriage", "love", "wealth", "health"):
            res = await engine.analyze_kundli_question({
                "domain": domain,
                "specific_question": f"How is my {domain} looking?",
            })
            self.assertEqual(res["status"], "analysis_complete")
            self.assertEqual(res["domain"], domain)
            self.assertTrue(res["auspicious_timeline"])
            if domain in ("marriage", "love"):
                self.assertIn("gun_milan_compatibility", res)

    async def test_recommend_vedic_remedies_idempotent(self):
        events = []
        engine = AstrotalkPanditjiEngine(broadcast=lambda ev: events.append(ev))

        res1 = await engine.recommend_vedic_remedies({
            "remedy_focus": "marriage_harmony",
            "include_gemstone": True,
            "include_epooja": True,
            "preferred_pooja_date": "Coming Thursday",
        })
        res2 = await engine.recommend_vedic_remedies({
            "remedy_focus": "marriage_harmony",
            "include_gemstone": True,
            "include_epooja": True,
            "preferred_pooja_date": "Coming Thursday",
        })
        self.assertEqual(res1["status"], "remedies_prescribed")
        self.assertEqual(res1["consultation_id"], res2["consultation_id"])
        self.assertTrue(res1["consultation_id"].startswith("AT-KUNDLI-"))
        self.assertTrue(any(e.get("type") == "booking_confirmed" for e in events))


if __name__ == "__main__":
    unittest.main()
