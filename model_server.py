#!/usr/bin/env python3
import base64
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL_NAME = os.environ.get("CALLREDACT_WHISPER_MODEL", "small").strip() or "small"
CURRENT_MODEL_NAME = MODEL_NAME
TMPDIR = os.environ.get("CALLREDACT_TMPDIR", "/dev/shm/callredact-vast")
MAX_UPLOAD = int(os.environ.get("CALLREDACT_MAX_UPLOAD", str(128 * 1024 * 1024)))

# Bound each Whisper inference pass so long recordings do not create a single
# large GPU/CPU workload. Values can be overridden from the Vast template.
def _env_float(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


CHUNK_SECONDS = _env_float("CALLREDACT_CHUNK_SECONDS", 120.0, 30.0, 300.0)
CHUNK_OVERLAP = _env_float("CALLREDACT_CHUNK_OVERLAP", 10.0, 0.0, 30.0)
if CHUNK_OVERLAP >= CHUNK_SECONDS:
    CHUNK_OVERLAP = min(10.0, CHUNK_SECONDS / 4.0)


def current_vast_instance_id():
    """Return the exact Vast worker instance ID injected into this container.

    Vast Serverless workers receive CONTAINER_ID in their runtime environment.
    Returning only this self-reported ID preserves CallRedact's ownership rule:
    never enumerate or destroy workers merely because they share an endpoint.
    """
    raw = (os.environ.get("CONTAINER_ID") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None

DIGITS = {
    "zero":"0","oh":"0","o":"0","one":"1","two":"2","three":"3","four":"4",
    "five":"5","six":"6","seven":"7","eight":"8","nine":"9",
}
SEPARATORS = {"dash","hyphen","space","pause"}
CARD_CONTEXT = {
    "card","credit","debit","visa","mastercard","amex","american","express",
    "number","expiration","expiry","expire","payment","billing",
}
CVV_CONTEXT_PHRASES = (
    "cvv","cvc","security code","verification code","card code","code on the back",
    "three digit code","three digits","four digit code","four digits",
)


def clean_word(w):
    return re.sub(r"[^a-z0-9']+", "", (w or "").lower())


def token_digits(token):
    t = clean_word(token)
    if t in DIGITS:
        return DIGITS[t]
    if t.isdigit() and 1 <= len(t) <= 19:
        return t
    return ""


def flatten_words(result):
    words = []
    for seg in result.get("segments") or []:
        ww = seg.get("words") or []
        if ww:
            for w in ww:
                txt = clean_word(str(w.get("word", "")))
                if txt:
                    words.append({
                        "text": txt,
                        "start": float(w.get("start", seg.get("start", 0.0)) or 0.0),
                        "end": float(w.get("end", seg.get("end", 0.0)) or 0.0),
                    })
        else:
            toks = [clean_word(x) for x in str(seg.get("text", "")).split()]
            toks = [x for x in toks if x]
            s = float(seg.get("start", 0.0) or 0.0)
            e = float(seg.get("end", s) or s)
            step = max(0.01, (e - s) / max(1, len(toks)))
            for i, t in enumerate(toks):
                words.append({"text": t, "start": s + i * step, "end": min(e, s + (i + 1) * step)})
    return words


def expand_digit_tokens(words):
    out = []
    i = 0
    while i < len(words):
        w = words[i]
        t = w["text"]
        if t in ("double", "triple") and i + 1 < len(words):
            d = token_digits(words[i + 1]["text"])
            if len(d) == 1:
                out.append({
                    "digits": d * (2 if t == "double" else 3),
                    "start": w["start"], "end": words[i + 1]["end"], "word_index": i,
                })
                i += 2
                continue
        d = token_digits(t)
        if d:
            out.append({"digits": d, "start": w["start"], "end": w["end"], "word_index": i})
        elif t in SEPARATORS:
            out.append({"digits": "", "separator": True, "start": w["start"], "end": w["end"], "word_index": i})
        else:
            out.append({"digits": "", "separator": False, "start": w["start"], "end": w["end"], "word_index": i})
        i += 1
    return out


def luhn(v):
    if not v.isdigit():
        return False
    total = 0
    parity = len(v) % 2
    for i, ch in enumerate(v):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def context_window(words, a, b, radius=12):
    return " ".join(w["text"] for w in words[max(0, a-radius):min(len(words), b+radius+1)])


def card_context_score(c):
    hits = len(set(c.split()).intersection(CARD_CONTEXT))
    if "credit card" in c or "card number" in c:
        hits += 2
    return hits


def cvv_context(c):
    return any(p in c for p in CVV_CONTEXT_PHRASES)


def detect_findings(result, duration):
    words = flatten_words(result)
    expanded = expand_digit_tokens(words)
    findings = []
    i = 0
    while i < len(expanded):
        if not expanded[i].get("digits"):
            i += 1
            continue
        j = i
        digits = ""
        start = expanded[i]["start"]
        end = expanded[i]["end"]
        first = expanded[i]["word_index"]
        last = first
        while j < len(expanded):
            ent = expanded[j]
            if ent.get("digits"):
                digits += ent["digits"]
                end = ent["end"]
                last = ent["word_index"]
                j += 1
                continue
            if ent.get("separator"):
                j += 1
                continue
            break
        context = context_window(words, first, last, 14)
        score_ctx = card_context_score(context)
        n = len(digits)
        if 13 <= n <= 19:
            valid = luhn(digits)
            score = min(100, (55 if valid else 20) + min(30, score_ctx * 8) + (8 if n in (15,16) else 3))
            confidence = "critical" if valid and score_ctx >= 2 else ("high" if valid or score_ctx >= 3 else "medium")
            findings.append({
                "type":"possible_pan", "confidence":confidence,
                "start_ms":int(max(0, start-2.5)*1000),
                "end_ms":int(min(duration or end+2.5, end+2.5)*1000),
                "context_type":("luhn+" if valid else "numeric+") + ("card_context" if score_ctx else "weak_context"),
                "score":score,
            })
        elif 3 <= n <= 4 and cvv_context(context):
            score = 92 if ("cvv" in context or "cvc" in context or "security code" in context) else 82
            findings.append({
                "type":"possible_cvv", "confidence":"critical" if score >= 90 else "high",
                "start_ms":int(max(0, start-2.5)*1000),
                "end_ms":int(min(duration or end+2.5, end+2.5)*1000),
                "context_type":"cvv_context", "score":score,
            })
        i = max(i + 1, j)

    findings.sort(key=lambda x:(x["type"], x["start_ms"], -x["score"]))
    out = []
    for f in findings:
        if out and out[-1]["type"] == f["type"] and f["start_ms"] <= out[-1]["end_ms"] + 1500:
            out[-1]["end_ms"] = max(out[-1]["end_ms"], f["end_ms"])
            if f["score"] > out[-1]["score"]:
                out[-1].update(score=f["score"], confidence=f["confidence"], context_type=f["context_type"])
        else:
            out.append(dict(f))
    return out[:20]


def merge_findings(findings):
    findings = sorted(findings, key=lambda x:(x["type"], x["start_ms"], -x.get("score", 0)))
    out = []
    for f in findings:
        if out and out[-1]["type"] == f["type"] and f["start_ms"] <= out[-1]["end_ms"] + 1800:
            out[-1]["end_ms"] = max(out[-1]["end_ms"], f["end_ms"])
            if f.get("score", 0) > out[-1].get("score", 0):
                out[-1].update(score=f.get("score",0), confidence=f.get("confidence","medium"), context_type=f.get("context_type","detector"))
        else:
            out.append(dict(f))
    return out[:20]


def probe(path):
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return max(0.0, float(p.stdout.strip())) if p.returncode == 0 else 0.0
    except Exception:
        return 0.0


def load_audio_slice(path, start, length):
    import numpy as np
    p = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", path,
         "-t", f"{length:.3f}", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=max(120, int(length * 2)),
    )
    if p.returncode != 0:
        raise RuntimeError("ffmpeg decode failed: " + p.stderr.decode("utf-8", "replace")[-1000:])
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


class ScanSource(BaseModel):
    pbx_id: str = "unknown"
    scan_id: int = 0
    item_id: int = 0
    uniqueid: str = ""
    run_mode: str = "manual"


class ScanPayload(BaseModel):
    audio_b64: str
    filename: str = "recording.bin"
    duration: float = 0.0
    request_id: str = ""
    model: str = ""
    source: ScanSource = Field(default_factory=ScanSource)


app = FastAPI(title="CallRedact Vast model server", docs_url=None, redoc_url=None)
model = None
device_name = "cuda"



def load_requested_model(requested):
    """Load requested Whisper model if it differs from the current one."""
    global model, device_name, CURRENT_MODEL_NAME
    name = str(requested or CURRENT_MODEL_NAME or MODEL_NAME).strip().lower() or "small"
    allowed = {"tiny","base","small","medium","large","large-v2","large-v3"}
    if name not in allowed:
        name = "small"
    if model is not None and name == CURRENT_MODEL_NAME:
        return
    import torch
    import whisper
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")
    device_name = torch.cuda.get_device_name(0)
    started = time.time()
    print(f"CALLREDACT_MODEL_INFO loading Whisper {name} on {device_name}", flush=True)
    model = whisper.load_model(name, device="cuda", download_root="/root/.cache/whisper")
    CURRENT_MODEL_NAME = name
    print(f"CALLREDACT_MODEL_READY model={name} gpu={device_name} load_seconds={time.time()-started:.1f}", flush=True)

@app.on_event("startup")
def load_model():
    global model, device_name
    try:
        import torch
        import whisper
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available")
        device_name = torch.cuda.get_device_name(0)
        started = time.time()
        print(f"CALLREDACT_MODEL_INFO loading Whisper {MODEL_NAME} on {device_name}", flush=True)
        model = whisper.load_model(MODEL_NAME, device="cuda", download_root="/root/.cache/whisper")
        print(f"CALLREDACT_MODEL_READY model={MODEL_NAME} gpu={device_name} load_seconds={time.time()-started:.1f}", flush=True)
    except Exception as exc:
        print(f"CALLREDACT_MODEL_ERROR {type(exc).__name__}: {exc}", flush=True)
        raise


@app.get("/health")
def health():
    return {"ok": model is not None, "model": CURRENT_MODEL_NAME, "gpu": device_name}


@app.post("/scan")
def scan(payload: ScanPayload):
    print(
        f"CALLREDACT_REQUEST model={str(payload.model or MODEL_NAME)} "
        f"current={CURRENT_MODEL_NAME} "
        f"request_id={payload.request_id} "
        f"pbx_id={payload.source.pbx_id} "
        f"scan_id={payload.source.scan_id} "
        f"item_id={payload.source.item_id}",
        flush=True,
    )
    try:
        load_requested_model(payload.model)
        print(
            f"CALLREDACT_MODEL_ACTIVE requested={str(payload.model or MODEL_NAME)} current={CURRENT_MODEL_NAME}",
            flush=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Whisper model load failed: {exc}")
    if model is None:
        raise HTTPException(status_code=503, detail="Whisper model is not ready")
    source = payload.source
    print(
        f"CALLREDACT_SCAN_START pbx_id={source.pbx_id[:64]} scan_id={source.scan_id} item_id={source.item_id} request_id={payload.request_id[:160]} instance_id={current_vast_instance_id() or 'missing'}",
        flush=True,
    )
    try:
        raw = base64.b64decode(payload.audio_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="audio_b64 is invalid base64")
    if not raw:
        raise HTTPException(status_code=400, detail="recording is empty")
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail=f"recording exceeds {MAX_UPLOAD} byte limit")

    os.makedirs(TMPDIR, mode=0o700, exist_ok=True)
    suffix = os.path.splitext(payload.filename or "recording.bin")[1][:12] or ".bin"
    fd, path = tempfile.mkstemp(prefix="audio-", suffix=suffix, dir=TMPDIR)
    os.close(fd)
    started = time.time()
    try:
        with open(path, "wb") as fh:
            fh.write(raw)
        del raw
        duration = probe(path) or float(payload.duration or 0.0) or 300.0
        chunk = CHUNK_SECONDS
        overlap = CHUNK_OVERLAP
        print(
            f"CALLREDACT_SCAN_PLAN duration={duration:.1f}s chunk={chunk:.1f}s overlap={overlap:.1f}s",
            flush=True,
        )
        starts = []
        pos = 0.0
        while pos < duration:
            starts.append(pos)
            if pos + chunk >= duration:
                break
            pos += chunk - overlap

        all_findings = []
        for start in starts:
            length = min(chunk, duration - start)
            print(
                f"CALLREDACT_CHUNK_START start={start:.1f}s length={length:.1f}s",
                flush=True,
            )
            audio = load_audio_slice(path, start, length)
            result = model.transcribe(
                audio,
                verbose=False,
                word_timestamps=True,
                temperature=0,
                fp16=True,
            )
            findings = detect_findings(result, length)
            offset = int(start * 1000)
            for finding in findings:
                finding = dict(finding)
                finding["start_ms"] += offset
                finding["end_ms"] += offset
                all_findings.append(finding)
            del audio, result
            try:
                import gc
                import torch
                gc.collect()
                torch.cuda.empty_cache()
            except Exception:
                pass
            print(
                f"CALLREDACT_CHUNK_DONE start={start:.1f}s length={length:.1f}s",
                flush=True,
            )

        return {
            "findings": merge_findings(all_findings),
            "processing_ms": int((time.time() - started) * 1000),
            "device": device_name,
            "model": MODEL_NAME,
            "transcripts_persisted": False,
            "request_id": payload.request_id,
            "instance_id": current_vast_instance_id(),
            "source": {
                "pbx_id": source.pbx_id,
                "scan_id": source.scan_id,
                "item_id": source.item_id,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        print(
            f"CALLREDACT_MODEL_ERROR {type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            flush=True,
        )
        raise HTTPException(status_code=500, detail="remote scan failed")
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass
