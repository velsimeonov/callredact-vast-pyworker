#!/usr/bin/env python3
import base64
import io
import os
import wave

from vastai import Worker, WorkerConfig, HandlerConfig, BenchmarkConfig, LogActionConfig

MODEL_SERVER_URL = "http://127.0.0.1"
MODEL_SERVER_PORT = 18000
MODEL_LOG_FILE = "/var/log/callredact-model.log"


def benchmark_payload():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * (16000 * 5))
    return {
        "audio_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "filename": "benchmark.wav",
        "duration": 5.0,
    }


def workload(payload):
    try:
        return max(1.0, float(payload.get("duration") or 1.0))
    except Exception:
        return 1.0


config = WorkerConfig(
    model_server_url=MODEL_SERVER_URL,
    model_server_port=MODEL_SERVER_PORT,
    model_log_file=MODEL_LOG_FILE,
    handlers=[
        HandlerConfig(
            route="/scan",
            allow_parallel_requests=False,
            max_queue_time=900.0,
            workload_calculator=workload,
            benchmark_config=BenchmarkConfig(generator=benchmark_payload, runs=1, concurrency=1),
        )
    ],
    log_action_config=LogActionConfig(
        on_load=["CALLREDACT_MODEL_READY"],
        on_error=["CALLREDACT_MODEL_ERROR", "Traceback (most recent call last):"],
        on_info=["CALLREDACT_MODEL_INFO"],
    ),
)

if __name__ == "__main__":
    Worker(config).run()
