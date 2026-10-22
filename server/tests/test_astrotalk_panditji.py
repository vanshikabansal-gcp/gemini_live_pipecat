"""Unit tests for Astrotalk Panditji execution engine (Janam Kundli, Dasha Analysis, Vedic Upay)."""

import unittest
from unittest.mock import AsyncMock

from persona_registry import AstrotalkPanditjiArchitecture
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
        self.assertEqual(res1["next_action"], "speak_remedies_and_warm_blessing_naturally")
        self.assertTrue(res1["shuddh_sanskrit_mantra"].startswith("ॐ"))
        self.assertIn("Never say 'this is complete'", res1["voice_rule"])
        self.assertIn("single language", res1["voice_rule"])
        self.assertIn("pure Shuddh Sanskrit", res1["mandatory_spoken_points"])
        self.assertEqual(res1["consultation_id"], res2["consultation_id"])
        self.assertTrue(res1["consultation_id"].startswith("AT-KUNDLI-"))
        self.assertTrue(any(e.get("type") == "booking_confirmed" for e in events))

    async def test_architecture_auto_phase_transitions_and_no_meta_utterance(self):
        arch = AstrotalkPanditjiArchitecture()
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

        # 1. generate_janam_kundli auto-transitions to SOP_02_KUNDLI_DASHAS and embeds card without calling inject_directive
        p1 = DummyParams({
            "caller_name": "Aarav",
            "birth_date": "15 August 1995",
            "birth_time": "10:30 AM",
            "birth_place": "Delhi",
        })
        await handlers["generate_janam_kundli"](p1)
        self.assertEqual(arch.engine.active_phase, "SOP_02_KUNDLI_DASHAS")
        self.assertIn("active_phase_card", p1.result)
        self.assertIn("SOP_02", p1.result["active_phase_card"])
        llm.inject_directive.assert_not_awaited()

        # 2. analyze_kundli_question auto-transitions to SOP_03_LIFE_PREDICTION and embeds card without calling inject_directive
        p2 = DummyParams({
            "domain": "career",
            "specific_question": "When will I get promoted?",
        })
        await handlers["analyze_kundli_question"](p2)
        self.assertEqual(arch.engine.active_phase, "SOP_03_LIFE_PREDICTION")
        self.assertIn("active_phase_card", p2.result)
        self.assertIn("SOP_03", p2.result["active_phase_card"])
        llm.inject_directive.assert_not_awaited()

        # 3. recommend_vedic_remedies auto-transitions to SOP_04_VEDIC_REMEDIES and embeds card without calling inject_directive
        p3 = DummyParams({
            "remedy_focus": "career_growth",
            "include_gemstone": True,
        })
        await handlers["recommend_vedic_remedies"](p3)
        self.assertEqual(arch.engine.active_phase, "SOP_04_VEDIC_REMEDIES")
        self.assertIn("active_phase_card", p3.result)
        self.assertIn("SOP_04", p3.result["active_phase_card"])
        self.assertEqual(p3.result["next_action"], "speak_remedies_and_warm_blessing_naturally")
        self.assertIn("Never say 'this is complete'", p3.result["voice_rule"])
        llm.inject_directive.assert_not_awaited()

        # 4. Redundant switch_phase to SOP_04_VEDIC_REMEDIES returns already_sent + anti-meta voice_rule
        p4 = DummyParams({"phase_id": "SOP_04_VEDIC_REMEDIES"})
        await handlers["switch_phase"](p4)
        self.assertEqual(p4.result["delivery_status"], "already_sent")
        self.assertIn("NEVER say 'This is complete'", p4.result["voice_rule"])
        llm.inject_directive.assert_not_awaited()

    async def test_start_trigger_processor_defers_until_start_frame_and_gates_mic(self):
        from agent_live import StartTriggerProcessor, resolve_prebuilt_avatar_name
        from pipecat.frames.frames import (
            BotStartedSpeakingFrame,
            BotStoppedSpeakingFrame,
            InputAudioRawFrame,
            InputTransportMessageFrame,
            LLMMessagesAppendFrame,
            LLMRunFrame,
            StartFrame,
        )

        # Avatar resolution for Panditji even with Sulafat voice
        self.assertEqual(
            resolve_prebuilt_avatar_name("auto", "astrotalk-panditji", "Sulafat", "female"),
            "Ben",
        )

        proc = StartTriggerProcessor(
            language="hi-IN",
            gate_mic_on_greeting=True,
            persona_id="astrotalk-panditji",
        )
        proc._enable_direct_mode = True
        self.assertEqual(proc.get_greeting_text(), "प्रणाम पंडित जी! जय श्री कृष्ण!")

        pushed = []

        async def capture_push(frame, direction=None):
            pushed.append(frame)

        proc.push_frame = capture_push
        proc.broadcast_interruption = AsyncMock()

        # 1. If start_trigger arrives before StartFrame, it is deferred until StartFrame is processed
        await proc.process_frame(
            InputTransportMessageFrame(message={"type": "start_trigger", "id": "t1"})
        )
        self.assertFalse(proc.triggered)
        self.assertEqual(len(pushed), 0)

        # 2. When StartFrame arrives, it forwards StartFrame and immediately fires the deferred greeting
        await proc.process_frame(StartFrame())
        self.assertTrue(proc.triggered)
        self.assertTrue(any(isinstance(f, StartFrame) for f in pushed))
        self.assertTrue(any(isinstance(f, LLMMessagesAppendFrame) for f in pushed))
        self.assertTrue(any(isinstance(f, LLMRunFrame) for f in pushed))

        # 3. Ambient noise while mic is gated is dropped
        pushed.clear()
        quiet_pcm = (b"\x10\x00" * 320)
        await proc.process_frame(InputAudioRawFrame(audio=quiet_pcm, sample_rate=16000, num_channels=1))
        self.assertEqual(len(pushed), 0)

        # 4. Loud audio BEFORE BotStartedSpeakingFrame stays gated in pre_buffer so downstream VAD
        # cannot fire broadcast_interruption() while the initial greeting is still connecting
        loud_pcm = (b"\x00\x10" * 320)  # value 4096 > 650 RMS
        await proc.process_frame(InputAudioRawFrame(audio=loud_pcm, sample_rate=16000, num_channels=1))
        await proc.process_frame(InputAudioRawFrame(audio=loud_pcm, sample_rate=16000, num_channels=1))
        self.assertTrue(proc._mic_gated)
        proc.broadcast_interruption.assert_not_awaited()
        self.assertEqual(len(pushed), 0)

        # 5. Once BotStartedSpeakingFrame arrives, user voice barge-in opens gate and interrupts
        await proc.process_frame(BotStartedSpeakingFrame())
        self.assertTrue(proc._bot_greeting_started)
        await proc.process_frame(InputAudioRawFrame(audio=loud_pcm, sample_rate=16000, num_channels=1))
        await proc.process_frame(InputAudioRawFrame(audio=loud_pcm, sample_rate=16000, num_channels=1))
        self.assertFalse(proc._mic_gated)
        proc.broadcast_interruption.assert_awaited_once()
        await proc.process_frame(BotStoppedSpeakingFrame())
        self.assertFalse(proc._mic_gated)


if __name__ == "__main__":
    unittest.main()
