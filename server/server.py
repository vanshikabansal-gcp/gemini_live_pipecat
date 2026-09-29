import asyncio
import warnings
warnings.filterwarnings("ignore", message=".*grpcio < 1.83.0.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*vertexai.preview.rag.*")
import importlib
import os
import time
import argparse
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from urllib.parse import quote
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, Query, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables
load_dotenv(override=True)
import diagnostic_buffer
import voice_profiles
import session_access
import abhay_challenge

# Abhay negotiation challenge (APP_MODE=abhay-challenge). None in Voice Studio
# mode, where nothing below that checks it changes behaviour.
CHALLENGE: Optional[abhay_challenge.ChallengeSettings] = abhay_challenge.settings_from_env()


def load_pipeline(bot_type):
    """Load only the requested media pipeline after a connection is accepted."""
    from runtime_compat import install_runtime_patches
    install_runtime_patches()
    if bot_type == "gemini-live":
        from agent_live import run_agent_live
        return run_agent_live
    from agent import run_agent
    return run_agent


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except OSError:
        pass


def warm_media_pipelines():
    """Pay the one-time media SDK import cost (~25s on Cloud Run) before the first call.

    Without this, the first WebSocket on a fresh instance waits for pipecat, google-genai and
    transformers (pulled in by Smart Turn v3) to import, which outlasts client timeouts.
    """
    from runtime_compat import _ensure_valid_adc
    _ensure_valid_adc()
    for bot_type in ("tts-llm-stt", "gemini-live"):
        load_pipeline(bot_type)
    importlib.import_module("pipecat.audio.turn.smart_turn.local_smart_turn_v3")


def warm_challenge_pipeline():
    """The challenge only ever runs Gemini Live: skip the cascade and its turn model."""
    from runtime_compat import _ensure_valid_adc
    _ensure_valid_adc()
    load_pipeline("gemini-live")


async def _warm_in_background(warm=None):
    started = time.monotonic()
    try:
        await asyncio.to_thread(warm or warm_media_pipelines)
        _safe_print(f"Media pipelines warmed in {time.monotonic() - started:.1f}s")
    except Exception as exc:  # Warmup is an optimization; a call will retry the import.
        _safe_print(f"Media pipeline warmup failed (will load on first call): {exc!r}")


from system_prompt import SYSTEM_PROMPT, tts_prompt

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles FastAPI startup and shutdown."""
    if CHALLENGE is not None:
        # Warm before serving. uvicorn opens the port only after startup, so
        # Cloud Run keeps players off a fresh instance until its first round can
        # start at once; a round that raced the warmup waited ~20s on imports.
        await _warm_in_background(warm_challenge_pipeline)
        yield
        return
    warmup = asyncio.create_task(_warm_in_background())
    yield  # Run app
    if not warmup.done():
        warmup.cancel()

# Initialize FastAPI app with lifespan manager
app = FastAPI(lifespan=lifespan)

if CHALLENGE is None:
    # Configure CORS to allow requests from any origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
# The challenge UI is served from this origin, so it registers no CORS policy:
# browsers then refuse cross-origin reads of its API.


@app.middleware("http")
async def challenge_security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    if CHALLENGE is not None:
        abhay_challenge.apply_security_headers(response.headers, request.url.path)
    return response

@app.middleware("http")
async def check_sni_mismatch_middleware(request: Request, call_next):
    gfe_info = request.headers.get("x-google-gfe-frontline-info")
    if gfe_info:
        host = (request.headers.get("host") or "").split(":")[0].lower()
        sni = None
        for pair in gfe_info.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip().lower() == "sni":
                    sni = v.strip().lower()
                    break
        if sni and host and sni != host:
            return Response(status_code=421, content="Misdirected Request")
    return await call_next(request)

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    bot_type: str = "tts-llm-stt",
    model: str = "gemini-3.8-live",
    voice: Optional[str] = "Puck",
    language: str = "en-US",
    system_instruction: Optional[str] = None,
    tts: bool = False,
    tts_voice: str = "en-US-Chirp3-HD-Aoede",
    tts_model: str = "gemini-3.8-flash-lite-tts",
    tts_pace: float = 0.80,
    tts_style: Optional[str] = None,
    tts_accent: Optional[str] = None,
    tts_pitch: Optional[str] = None,
    tts_pace_label: Optional[str] = None,
    tts_voice_prompt: Optional[str] = None,
    llm_model: str = "gemini-3.5-flash-lite",
    stt_model: str = "gemini-3.5-transcribe-live-aistudio",
    stt_language: str = "en-US",
    tools: Optional[str] = None,
    skip_stt: bool = False,
    vad: bool = True,
    context_compression: bool = True,
    context_compression_trigger_tokens: Optional[int] = 5000,
    thinking: bool = False,
    thinking_level: Optional[str] = None,
    vad_mode: Optional[str] = None,
    # Opaque, single-use handle minted by /connect. Raw cloning keys are
    # deliberately not accepted here: this URL is logged in several places.
    voice_profile_id: Optional[str] = None,
    # Stamps every log line and latency sample this session produces, so
    # concurrent demoers do not blend their metrics together.
    session_id: Optional[str] = None,
    connection_id: Optional[str] = None,
    # Selects the persona's execution architecture server-side. This is the only
    # signal that decides which persona tooling loads; the system instruction is
    # never inspected for routing. See server/persona_registry.py.
    persona_id: Optional[str] = None,
    avatar_enabled: bool = False,
    avatar_name: str = "auto",
):
    await websocket.accept()
    _safe_print("WebSocket connection accepted")
    if connection_id:
        if not session_access.consume_join(session_id, connection_id):
            await websocket.close(code=1008, reason="Invalid or expired connection")
            return
    else:
        if CHALLENGE is not None:
            await websocket.close(code=1008, reason="Start the challenge from the page")
            return
        # Raw websocket clients get an isolated anonymous scope; a supplied ID
        # cannot impersonate a session created through /connect.
        session_id = str(uuid4())
    if CHALLENGE is not None:
        # Every query parameter is ignored: the round runs from server state.
        await _run_challenge_socket(websocket, session_id)
        return
    if bot_type not in diagnostic_buffer.BOT_TYPES:
        await websocket.close(code=1008, reason="Unknown bot_type")
        return
    diagnostic_buffer.bind_session(session_id, bot_type)
    stored_instruction = session_access.take_instructions(session_id)
    system_instruction = stored_instruction or system_instruction
    avatar_custom_image = session_access.take_avatar_custom_image(session_id)
    custom_voice_audio = session_access.take_custom_voice_audio(session_id)
    custom_voice_key = voice_profiles.consume(voice_profile_id)
    try:
        if bot_type == "gemini-live":
            run_agent_live = await asyncio.to_thread(load_pipeline, bot_type)
            await run_agent_live(
                websocket,
                model=model,
                voice=voice,
                language=language,
                system_instruction=system_instruction,
                tts=tts,
                tts_pace=tts_pace,
                tools=tools,
                vad=vad,
                vad_mode=vad_mode,
                context_compression=context_compression,
                context_compression_trigger_tokens=max(5000, context_compression_trigger_tokens) if context_compression_trigger_tokens is not None else 5000,
                thinking=thinking,
                thinking_level=thinking_level,
                custom_voice_key=custom_voice_key,
                persona_id=persona_id,
                avatar_enabled=avatar_enabled,
                avatar_name=avatar_name,
                avatar_custom_image=avatar_custom_image,
                custom_voice_audio=custom_voice_audio,
            )
        elif bot_type == "tts-llm-stt":
            run_agent = await asyncio.to_thread(load_pipeline, bot_type)
            await run_agent(
                websocket,
                tts_voice=tts_voice,
                tts_pace=tts_pace,
                llm_model=llm_model,
                stt_model=stt_model,
                stt_language=stt_language,
                tts_model=tts_model,
                tts_voice_prompt=tts_voice_prompt,
                tts_style=tts_style,
                tts_accent=tts_accent,
                tts_pitch=tts_pitch,
                tts_pace_label=tts_pace_label,
                system_instruction=system_instruction,
                skip_stt=skip_stt,
                vad=vad,
                vad_mode=vad_mode,
                custom_voice_key=custom_voice_key,
                persona_id=persona_id,
            )
    except Exception as e:
        diagnostic_buffer.append_raw_log_entry(f"Session failed: {type(e).__name__}: {e}", "ERROR")
        _safe_print(f"Exception in run_bot: {e}")
        try:
            from pipecat.frames.frames import OutputTransportMessageFrame
            from pipecat.serializers.protobuf import ProtobufFrameSerializer
            payload = await ProtobufFrameSerializer().serialize(
                OutputTransportMessageFrame(
                    message={
                        "label": "rtvi-ai",
                        "type": "error",
                        "data": {"error": f"Session failed: {e}", "fatal": True},
                    }
                )
            )
            if payload:
                await websocket.send_bytes(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
        except Exception:
            pass


async def _send_fatal_error(websocket: WebSocket, message: str, code: Optional[str] = None) -> None:
    """Tell the client the session failed, over the same RTVI protobuf channel.
    ``code`` lets the challenge page show a specific message."""
    data: Dict[str, Any] = {"error": message, "fatal": True}
    if code:
        data["code"] = code
    try:
        from pipecat.frames.frames import OutputTransportMessageFrame
        from pipecat.serializers.protobuf import ProtobufFrameSerializer
        payload = await ProtobufFrameSerializer().serialize(
            OutputTransportMessageFrame(message={"label": "rtvi-ai", "type": "error", "data": data})
        )
        if payload:
            await websocket.send_bytes(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
    except Exception:
        pass


async def _close_quietly(websocket: WebSocket, code: int, reason: str = "") -> None:
    """Close with a real close code instead of just dropping the connection.
    The pipeline or the client may already have closed it."""
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass


async def _claim_challenge_id(websocket: WebSocket, player_id: str, session_id: str) -> bool:
    """Reserve the ID for this round, or tell the page why it cannot play."""
    try:
        outcome = await asyncio.to_thread(
            CHALLENGE.store.claim,
            player_id,
            session_id,
            int(time.time() * 1000),
            abhay_challenge.claim_ttl_ms(CHALLENGE.duration_s),
        )
    except Exception as e:
        _safe_print(f"Challenge ID claim failed: {type(e).__name__}: {e}")
        await _send_fatal_error(websocket, abhay_challenge.UNAVAILABLE_MESSAGE, code="unavailable")
        await websocket.close(code=1011, reason="Try again in a minute")
        return False
    if outcome == abhay_challenge.CLAIM_OK:
        return True
    if outcome == abhay_challenge.ID_PLAYED:
        await _send_fatal_error(websocket, abhay_challenge.ID_PLAYED_MESSAGE, code="already_played")
    else:
        await _send_fatal_error(websocket, abhay_challenge.ID_BUSY_MESSAGE, code="in_progress")
    await websocket.close(code=1008, reason="This ID cannot start a round")
    return False


async def _run_challenge_socket(websocket: WebSocket, session_id: str) -> None:
    """One timed Abhay round. Everything comes from what /connect staged."""
    challenge = session_access.take_challenge(session_id)
    instructions = session_access.take_instructions(session_id)
    # Nothing else may ride along on a challenge session.
    session_access.take_avatar_custom_image(session_id)
    session_access.take_custom_voice_audio(session_id)
    player_id = abhay_challenge.normalize_player_id((challenge or {}).get("player_id"))
    language = (challenge or {}).get("language")
    if not player_id or language not in abhay_challenge.LANGUAGES or not instructions:
        await websocket.close(code=1008, reason="Start the challenge from the page")
        return
    if not CHALLENGE.try_acquire_slot():
        await websocket.close(code=1013, reason="All showroom slots are busy")
        return
    try:
        if not await _claim_challenge_id(websocket, player_id, session_id):
            return
        diagnostic_buffer.bind_session(session_id, "gemini-live")
        config = abhay_challenge.ChallengeConfig(
            session_id=session_id,
            player_id=player_id,
            language=language,
            duration_s=CHALLENGE.duration_s,
            store=CHALLENGE.store,
            on_recorded=CHALLENGE.board.invalidate,
        )
        run_agent_live = await asyncio.to_thread(load_pipeline, "gemini-live")
        # Backstop only: the round ends itself at duration_s. This bounds a
        # stuck pipeline (and its Live API bill) if that ever fails.
        await asyncio.wait_for(
            run_agent_live(
                websocket,
                model=abhay_challenge.MODEL,
                voice=abhay_challenge.VOICE,
                language=language,
                system_instruction=instructions,
                tts=False,
                tools=None,
                vad=True,
                vad_mode=abhay_challenge.VAD_MODE,
                context_compression=True,
                context_compression_trigger_tokens=5000,
                thinking=False,
                thinking_level=None,
                custom_voice_key=None,
                persona_id=abhay_challenge.PERSONA_ID,
                avatar_enabled=False,
                avatar_name="auto",
                avatar_custom_image=None,
                custom_voice_audio=None,
                challenge=config,
            ),
            timeout=CHALLENGE.duration_s + 90,
        )
    except asyncio.TimeoutError:
        _safe_print(f"Challenge round exceeded its wall-clock limit ({abhay_challenge.mask_player_id(player_id)})")
        await _close_quietly(websocket, 1011, "The round ran too long")
    except Exception as e:
        diagnostic_buffer.append_raw_log_entry(f"Challenge session failed: {type(e).__name__}: {e}", "ERROR")
        _safe_print(f"Exception in challenge round: {type(e).__name__}: {e}")
        # Details stay in the server log; the player gets a generic message.
        await _send_fatal_error(websocket, "The round could not continue. Please try again.")
        await _close_quietly(websocket, 1011, "The round could not continue")
    finally:
        # A round that never produced a result (the claim was refused, or the
        # call failed before the clock started) must not use up the ID.
        # Finished rounds have already recorded their score or released the
        # claim themselves. Releasing is always safe: it only ever removes
        # this session's own claim, including one whose claim call was cut off.
        if abhay_challenge.recent_result(session_id) is None:
            try:
                await asyncio.to_thread(CHALLENGE.store.release, player_id, session_id)
            except Exception as e:
                _safe_print(f"Challenge ID release failed: {type(e).__name__}: {e}")
        CHALLENGE.release_slot()


@app.get("/persona-prompt/{persona_id}")
async def persona_prompt(
    persona_id: str,
    phase: Optional[str] = None,
    engine: Optional[str] = "live",
    tone: str = "professional",
    language: str = "en-US",
) -> Dict[str, Any]:
    """Return the system prompt the backend will actually run for a persona.

    Personas whose architecture owns their prompt discard whatever the client
    sends. Without this endpoint the studio could only preview its own local
    copy, which would silently disagree with the running session.

    If `phase` is specified (e.g. SOP_02_DISCOVERY), returns the JIT
    card formatted prompt for live phase inspection (for live engine).
    In cascade engine, the monolithic prompt is always returned.
    """
    if CHALLENGE is not None:
        # Abhay's prompt states his hidden floor; the challenge never serves it.
        raise HTTPException(status_code=404, detail="Not found")
    from persona_registry import (
        ArchitecturePattern,
        get_persona_architecture,
        is_persona_ui_editable,
        resolve_persona_architecture,
    )

    editable = is_persona_ui_editable(persona_id)
    architecture = resolve_persona_architecture(persona_id)
    from persona_prompt_cards import get_session_preset, get_persona_card, format_persona_prompt_card
    try:
        composed = get_session_preset(persona_id, engine=engine or "live", tone=tone, language=language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not editable and phase and engine != "cascade":
        card = get_persona_card(persona_id, phase)
        if card:
            composed = format_persona_prompt_card(persona_id, card)

    persona_arch_obj = get_persona_architecture(persona_id)
    effective_engine = engine or "live"
    try:
        if getattr(persona_arch_obj, "has_exclusive_tools", lambda eng="live": False)(effective_engine):
            raw_tools = list(persona_arch_obj.get_tool_schemas(effective_engine))
        else:
            from pipecat.adapters.schemas.function_schema import FunctionSchema
            from rag_function import search_knowledge_base_schema
            raw_tools = [
                FunctionSchema(
                    name="get_current_time",
                    description="Get the current time.",
                    properties={
                        "is_explicit_request": {
                            "type": "boolean",
                            "description": (
                                "Return `true` ONLY if the user explicitly asks for the current time or date.\n\n"
                                "- Explaining schedules or timelines.\n"
                                "- Mentioning time casually in conversation."
                            ),
                        }
                    },
                    required=["is_explicit_request"],
                ),
                search_knowledge_base_schema,
            ]
            raw_tools.extend(persona_arch_obj.get_tool_schemas(effective_engine))
    except Exception:
        raw_tools = []

    serialized_tools = []
    for t in raw_tools:
        if hasattr(t, "name"):
            serialized_tools.append({
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", ""),
                "properties": getattr(t, "properties", {}),
                "required": list(getattr(t, "required", []) or []),
            })
        elif isinstance(t, dict) and "name" in t:
            serialized_tools.append(t)

    import json as _json
    tools_json_str = _json.dumps(serialized_tools, ensure_ascii=False)
    tools_token_count = max(1, int(round(len(tools_json_str) / 3.8))) if serialized_tools else 0

    return {
        "persona_id": persona_id,
        "architecture": architecture.value,
        "editable": editable,
        "prompt": composed or "",
        "phase": phase if engine != "cascade" else None,
        "engine": engine,
        "tools": serialized_tools,
        "tools_token_count": tools_token_count,
    }


@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
    if CHALLENGE is not None:
        return await _challenge_connect(request)
    import json
    from urllib.parse import parse_qs, urlencode
    # Get the original query string from the incoming request (e.g., "model=...&voice=...")
    query_params_raw = request.url.query
    params_dict: Dict[str, str] = {
        k: v[-1] for k, v in parse_qs(query_params_raw, keep_blank_values=True).items()
    }

    # Finish reading and validating the request before allocating any handles.
    # Invalid requests must not occupy a session slot for the four-hour TTL.
    instructions = params_dict.pop("system_instruction", None)
    MAX_CONNECT_BODY_BYTES = 12 * 1024 * 1024
    MAX_AVATAR_IMAGE_B64_CHARS = 7_000_000  # ~5 MB binary
    MAX_VOICE_AUDIO_B64_CHARS = 4_000_000   # ~3 MB binary

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_CONNECT_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Request body exceeds 12 MB limit")
        except ValueError:
            pass
    raw_body = await request.body()
    if len(raw_body) > MAX_CONNECT_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body exceeds 12 MB limit")

    try:
        body = json.loads(raw_body) if raw_body else {}
        if not isinstance(body, dict):
            raise ValueError("Expected a configuration object")
        for field in (
            "system_instruction",
            "prompt_source",
            "persona_tone",
            "thinking_level",
            "vad_mode",
            "tts_style",
            "tts_accent",
            "tts_pitch",
            "tts_pace_label",
            "tts_voice_prompt",
            "avatar_name",
            "avatar_custom_image",
            "custom_voice_audio",
        ):
            if field in body and body[field] is not None and not isinstance(body[field], str):
                raise ValueError(f"Invalid {field}")
        for field in ("context_compression", "thinking", "vad", "avatar_enabled"):
            if field in body and not isinstance(body[field], bool):
                raise ValueError(f"Invalid {field}")
        clone_key = body.get("custom_voice_key")
        if clone_key is not None and not isinstance(clone_key, str):
            raise ValueError("Invalid custom_voice_key")
        bot_type = params_dict.get("bot_type", "tts-llm-stt")
        if bot_type not in diagnostic_buffer.BOT_TYPES:
            raise ValueError("Unknown bot_type")

        if body.get("prompt_source") == "preset" and params_dict.get("persona_id") != "custom":
            from persona_prompt_cards import get_session_preset
            preset = get_session_preset(params_dict.get("persona_id", ""),
                engine="cascade" if bot_type == "tts-llm-stt" else "live",
                tone=body.get("persona_tone", "professional"),
                language=params_dict.get("language") or params_dict.get("stt_language", "en-US"))
            if preset:
                body["system_instruction"] = preset
        if body.get("system_instruction", "").strip():
            instructions = body["system_instruction"].strip()

        if "tools" in body:
            tools_data = body["tools"]
            params_dict["tools"] = json.dumps(tools_data) if isinstance(tools_data, (dict, list)) else str(tools_data)
        for field in ("context_compression", "thinking", "vad", "avatar_enabled"):
            if field in body:
                params_dict[field] = "true" if body[field] else "false"
        if "context_compression_trigger_tokens" in body:
            try:
                raw_val = int(body["context_compression_trigger_tokens"])
                # Preserve the existing minimum and invalid-value fallback.
                params_dict["context_compression_trigger_tokens"] = str(max(5000, raw_val))
            except (ValueError, TypeError, OverflowError):
                params_dict["context_compression_trigger_tokens"] = "5000"
        if "thinking_level" in body:
            params_dict["thinking_level"] = body["thinking_level"]
        if "vad_mode" in body and body["vad_mode"]:
            params_dict["vad_mode"] = body["vad_mode"]
        if "avatar_name" in body and body["avatar_name"]:
            params_dict["avatar_name"] = body["avatar_name"]
        avatar_custom_image = body.get("avatar_custom_image")
        custom_voice_audio = body.get("custom_voice_audio")
        if avatar_custom_image and len(avatar_custom_image) > MAX_AVATAR_IMAGE_B64_CHARS:
            raise HTTPException(status_code=413, detail="Custom avatar image exceeds 5 MB limit")
        if custom_voice_audio and len(custom_voice_audio) > MAX_VOICE_AUDIO_B64_CHARS:
            raise HTTPException(status_code=413, detail="Custom voice sample exceeds 3 MB limit")
        for field in ("tts_style", "tts_accent", "tts_pitch", "tts_pace_label", "tts_voice_prompt"):
            if field in body and body[field]:
                params_dict[field] = body[field]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid session configuration")

    # Dynamically determine WebSocket scheme (ws vs wss) and host
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    ws_scheme = "wss" if scheme == "https" else "ws"
    host = request.headers.get("x-forwarded-host", request.url.netloc)

    try:
        session_id, viewer_token, connection_id = session_access.issue(
            params_dict.get("session_id"),
            request.headers.get("x-session-token"),
            instructions=instructions,
            avatar_custom_image=avatar_custom_image,
            custom_voice_audio=custom_voice_audio,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    profile_id = None
    try:
        params_dict.update(session_id=session_id, connection_id=connection_id)
        # Exchange credentials for a one-use handle only after validation.
        if clone_key:
            profile_id = voice_profiles.register(clone_key)
            if profile_id:
                params_dict["voice_profile_id"] = profile_id
        ws_url = f"{ws_scheme}://{host}/ws?{urlencode(params_dict)}"
        return {"ws_url": ws_url, "session_id": session_id, "diagnostic_token": viewer_token}
    except Exception:
        # These handles have not reached the caller. Release both on failure.
        voice_profiles.consume(profile_id)
        session_access.discard(session_id)
        raise HTTPException(status_code=500, detail="Unable to prepare session")


# ---------------------------------------------------------------------------
# Abhay negotiation challenge (APP_MODE=abhay-challenge only)
# ---------------------------------------------------------------------------


def _require_challenge() -> abhay_challenge.ChallengeSettings:
    if CHALLENGE is None:
        raise HTTPException(status_code=404, detail="Not found")
    return CHALLENGE


def _caller(request: Request) -> str:
    return abhay_challenge.client_ip(request.headers, request.client.host if request.client else None)


async def _read_small_json(request: Request, limit: int) -> Dict[str, Any]:
    """Parse a small JSON object body.

    Requiring application/json also means a cross-site page cannot post here
    without a CORS preflight, which this origin never approves.
    """
    import json
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Send JSON")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Request too large")
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="Request too large")
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid request")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid request")
    return body


async def _challenge_connect(request: Request) -> Dict[str, Any]:
    """Start a round. Only the ID and a language are accepted from the client;
    persona, model, voice, prompt and tools are fixed server-side."""
    from urllib.parse import urlencode
    settings = _require_challenge()
    if not settings.connect_limiter.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a minute and try again.")
    body = await _read_small_json(request, 2048)
    player_id = abhay_challenge.normalize_player_id(body.get("player_id"))
    if player_id is None:
        raise HTTPException(status_code=400, detail=abhay_challenge.INVALID_ID_MESSAGE)
    language = body.get("language")
    if language not in abhay_challenge.LANGUAGES:
        language = abhay_challenge.DEFAULT_LANGUAGE
    if settings.active_sessions() >= settings.max_concurrent:
        raise HTTPException(status_code=503, detail="All showroom slots are busy. Try again in a minute.")
    # One round per ID. This is an early, friendly check; the claim taken when
    # the call opens is what enforces it.
    try:
        standing = await asyncio.to_thread(settings.store.status, player_id, int(time.time() * 1000))
    except Exception as exc:
        _safe_print(f"Challenge ID check failed: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=503, detail=abhay_challenge.UNAVAILABLE_MESSAGE)
    if standing == abhay_challenge.ID_PLAYED:
        raise HTTPException(status_code=409, detail=abhay_challenge.ID_PLAYED_MESSAGE)
    if standing == abhay_challenge.ID_BUSY:
        raise HTTPException(status_code=409, detail=abhay_challenge.ID_BUSY_MESSAGE)
    from persona_prompt_cards import get_session_preset
    try:
        instructions = get_session_preset(
            abhay_challenge.PERSONA_ID, engine="live", tone=settings.tone, language=language
        )
    except ValueError:
        instructions = None
    if not instructions:
        raise HTTPException(status_code=500, detail="The challenge is unavailable right now.")
    instructions = abhay_challenge.with_challenge_rules(instructions)
    try:
        session_id, session_token, connection_id = session_access.issue(
            instructions=instructions,
            challenge={"player_id": player_id, "language": language},
            ttl_s=settings.duration_s + abhay_challenge.RECENT_RESULT_TTL_S,
        )
    except RuntimeError:
        raise HTTPException(status_code=503, detail="The showroom is full. Try again in a minute.")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    ws_scheme = "wss" if scheme == "https" else "ws"
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    # The player ID stays server-side: URLs end up in logs.
    query = urlencode({"bot_type": "gemini-live", "session_id": session_id, "connection_id": connection_id})
    return {
        "ws_url": f"{ws_scheme}://{host}/ws?{query}",
        "session_id": session_id,
        "session_token": session_token,
        "duration_s": settings.duration_s,
        "player": abhay_challenge.mask_player_id(player_id),
    }


@app.get("/api/challenge/config")
async def challenge_config(request: Request) -> Dict[str, Any]:
    settings = _require_challenge()
    if not settings.board_limiter.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests")
    return settings.public_config()


@app.get("/api/challenge/leaderboard")
async def challenge_leaderboard(
    request: Request,
    limit: int = Query(default=10, ge=1, le=abhay_challenge.MAX_BOARD_LIMIT),
) -> Dict[str, Any]:
    """Public board, IDs masked. A valid organizer token also returns the full
    IDs of the top ``reveal_top_n`` rows (the winners)."""
    settings = _require_challenge()
    if not settings.board_limiter.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests")
    admin_token = request.headers.get("x-admin-token")
    revealed = False
    if admin_token is not None:
        if not settings.admin.verify_token(admin_token):
            raise HTTPException(status_code=401, detail="Organizer session expired")
        revealed = True
    try:
        board = await asyncio.to_thread(settings.board.get)
    except Exception as exc:
        _safe_print(f"Leaderboard unavailable: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=503, detail="Leaderboard temporarily unavailable")
    entries = abhay_challenge.public_entries(
        board["entries"][:limit], reveal_top_n=settings.reveal_top_n if revealed else 0
    )
    return {
        "entries": entries,
        "total_players": board["total_players"],
        "revealed": revealed,
        "reveal_top_n": settings.reveal_top_n,
        "stale": bool(board.get("stale")),
        "server_time_ms": int(time.time() * 1000),
    }


@app.post("/api/challenge/admin/login")
async def challenge_admin_login(request: Request) -> Dict[str, Any]:
    settings = _require_challenge()
    if not settings.admin.enabled:
        raise HTTPException(status_code=404, detail="Organizer access is not configured")
    caller = _caller(request)
    if not settings.login_limiter.allow(caller) or not settings.login_hourly_limiter.allow(caller):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    body = await _read_small_json(request, 1024)
    if not settings.admin.check_password(body.get("password")):
        # Never log what was typed.
        _safe_print("Organizer login rejected")
        raise HTTPException(status_code=401, detail="Incorrect password")
    token, expires_at = settings.admin.issue_token()
    return {"token": token, "expires_at_ms": expires_at * 1000, "reveal_top_n": settings.reveal_top_n}


@app.post("/api/challenge/finish")
async def challenge_finish(request: Request) -> Dict[str, Any]:
    """End the caller's own round now and return its score (or the score of a
    round that already ended). Authorized by the session token from /connect."""
    settings = _require_challenge()
    if not settings.finish_limiter.allow(_caller(request)):
        raise HTTPException(status_code=429, detail="Too many requests")
    body = await _read_small_json(request, 1024)
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        raise HTTPException(status_code=400, detail="Invalid request")
    if not session_access.authorized(session_id, request.headers.get("x-session-token")):
        raise HTTPException(status_code=403, detail="Session access denied")
    run = abhay_challenge.ACTIVE_RUNS.get(session_id)
    if run is not None and run.started:
        result = await run.finish("ended_by_player")
    else:
        result = abhay_challenge.recent_result(session_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No result for this round yet")
    return {"result": result}


@app.get("/connect/system-prompt")
async def get_system_prompt():
    if CHALLENGE is not None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"system_prompt": SYSTEM_PROMPT}

def studio_only():
    """Voice Studio tooling that the challenge never exposes: session logs
    include Abhay's tool results, which say when he has reached his floor."""
    if CHALLENGE is not None:
        raise HTTPException(status_code=404, detail="Not found")


def diagnostic_session(request: Request, session_id: str = Query(min_length=1, max_length=128)):
    studio_only()
    if not session_access.authorized(session_id, request.headers.get("x-session-token")):
        raise HTTPException(status_code=403, detail="Session access denied")
    return session_id


@app.get("/api/logs")
async def get_diagnostic_logs(session_id: str = Depends(diagnostic_session), limit: int = Query(default=500, ge=1, le=1500)):
    """Recent logs and latency percentiles.

    A session identifier is mandatory; unscoped process logs are excluded.
    """
    from diagnostic_buffer import get_recent_diagnostic_logs, get_latency_summary
    return {
        "logs": get_recent_diagnostic_logs(limit, session_id=session_id),
        "latency_summary": get_latency_summary(session_id=session_id),
        "session_id": session_id,
    }

@app.get("/api/metrics/latency")
async def get_latency_metrics_endpoint(session_id: str = Depends(diagnostic_session)):
    from diagnostic_buffer import get_latency_summary
    return get_latency_summary(session_id=session_id)

@app.post("/api/logs/clear")
async def clear_diagnostic_logs_endpoint(session_id: str = Depends(diagnostic_session)):
    """Clear this session only."""
    from diagnostic_buffer import clear_diagnostic_logs
    clear_diagnostic_logs(session_id=session_id)
    return {"status": "cleared", "session_id": session_id}

@app.get("/api/trace/current")
async def get_current_trace_endpoint(session_id: str = Depends(diagnostic_session)):
    from tracing import GLOBAL_LANGSMITH_TRACER
    return {"trace_url": GLOBAL_LANGSMITH_TRACER.get_current_trace_url(session_id)}


@app.get("/api/visits")
async def get_studio_visits_endpoint():
    studio_only()
    import visit_counter
    return visit_counter.get_visits()


@app.post("/api/visits")
async def record_studio_visit_endpoint(request: Request):
    studio_only()
    import json
    import visit_counter
    page_load_id: Optional[str] = None
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
            if isinstance(body, dict) and isinstance(body.get("page_load_id"), str):
                page_load_id = body["page_load_id"]
    except Exception:
        pass
    return visit_counter.record_visit(page_load_id=page_load_id)


# Mount the static files directory
possible_dist_dirs = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../demos/voice-studio/dist")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "demos/voice-studio/dist")),
    os.path.abspath("/app/demos/voice-studio/dist"),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../client/dist")),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "client/dist")),
    os.path.abspath("/app/client/dist"),
]

client_dist_dir = next((d for d in possible_dist_dirs if os.path.exists(d)), None)

# Pages the challenge UI answers; every other unknown path is a 404.
CHALLENGE_PAGES = ("", "board")


def _mount_challenge_ui(dist_dir: Optional[str]) -> None:
    """Serve only the challenge build (``dist/challenge``).

    The studio bundle is deliberately not served here: it embeds every persona
    prompt, including the one that states Abhay's floor price.
    """
    ui_dir = os.path.join(dist_dir, "challenge") if dist_dir else None
    page = os.path.join(ui_dir, "challenge.html") if ui_dir else None
    if not page or not os.path.exists(page):
        _safe_print("Challenge UI build not found (expected dist/challenge/challenge.html); serving the API only.")
        return
    app.mount("/assets", StaticFiles(directory=os.path.join(ui_dir, "assets")), name="assets")
    personas_dir = os.path.join(ui_dir, "personas")
    if os.path.exists(personas_dir):
        app.mount("/personas", StaticFiles(directory=personas_dir), name="personas")

    @app.get("/favicon.svg")
    async def read_challenge_favicon():
        fav_path = os.path.join(ui_dir, "favicon.svg")
        if os.path.exists(fav_path):
            return FileResponse(fav_path)
        return Response(status_code=404)

    @app.get("/{page_path:path}")
    async def read_challenge_page(page_path: str):
        if page_path.strip("/") not in CHALLENGE_PAGES:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(page, headers={"Cache-Control": "no-store"})


if CHALLENGE is not None:
    _mount_challenge_ui(client_dist_dir)
elif client_dist_dir:
    app.mount("/assets", StaticFiles(directory=os.path.join(client_dist_dir, "assets")), name="assets")
    
    personas_dir = os.path.join(client_dist_dir, "personas")
    if os.path.exists(personas_dir):
        app.mount("/personas", StaticFiles(directory=personas_dir), name="personas")

    @app.get("/favicon.svg")
    async def read_favicon():
        fav_path = os.path.join(client_dist_dir, "favicon.svg")
        if os.path.exists(fav_path):
            return FileResponse(fav_path)
        return Response(status_code=404)

    @app.get("/diagnostics")
    async def read_diagnostics():
        diag_path = os.path.join(client_dist_dir, "diagnostics.html")
        headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        if os.path.exists(diag_path):
            return FileResponse(diag_path, headers=headers)
        return FileResponse(os.path.join(client_dist_dir, "index.html"), headers=headers)

    @app.get("/{catch_all:path}")
    async def read_index(catch_all: str):
        headers = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}
        return FileResponse(os.path.join(client_dist_dir, "index.html"), headers=headers)

async def main():
    port = int(os.environ.get("PORT", 7860))
    config = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
