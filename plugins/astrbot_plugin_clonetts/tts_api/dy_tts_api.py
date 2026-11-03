import base64
import io
import json
import uuid
import wave
import time

import aiohttp

from astrbot.api import logger

try:
    from .speech_meter import meter_for
except ImportError:
    meter_for = None

# python version: ==3.11


async def tts_http_stream(self, text,context_texts):
    request_id = str(uuid.uuid4())
    service = "seed-icl-2.0" if self.voice_type.startswith("S_") else "seed-tts-2.0"
    meter = None
    try:
        meter = meter_for(__file__) if meter_for else None
        if meter:
            meter.begin(request_id, service, at=time.time())
    except Exception:
        logger.warning("Speech accounting start failed; audio request remains available")
        meter = None
    usage_chars, finished, rejected, audio_data = None, False, False, bytearray()
    headers = {
        "X-Api-Key": self.api_key, # 新版控制台 API Key
        "X-Api-Resource-Id": "seed-icl-2.0" if self.voice_type.startswith("S_") else "seed-tts-2.0", # 支持预设音色
        "X-Api-Request-Id": request_id,
        "X-Control-Require-Usage-Tokens-Return": "*",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }
    ctx_list = []
    if context_texts:
        if isinstance(context_texts, str):
            ctx_list = [context_texts]
        elif isinstance(context_texts, list):
            ctx_list = context_texts
        else:
            logger.warning(f"Invalid context_texts type: {type(context_texts)}, expected str or list.")

    additions_payload = {
            "explicit_language": "zh-cn",
            "disable_markdown_filter": True,
            "enable_latex_tn": True,
        }
    if ctx_list:
        additions_payload["context_texts"] = ctx_list
    additions_json_str = json.dumps(additions_payload, ensure_ascii=False)
    params = {
        "user": {
            "uid": f"{uuid.uuid4()}",
        },
        "req_params": {
            "text": text,
            "model": "seed-tts-2.0-expressive", # seed-tts-2.0-standard不支持使用语音指令context_texts
            "speaker": self.voice_type,
            "audio_params": {
                "format": "pcm",
                "sample_rate": self.sample_rate,
                "enable_timestamp": True,
                "speech_rate": self.speed_ratio,
                "loudness_rate": self.loudness_rate,
            },
            "additions":additions_json_str,
        },
    }

    async with aiohttp.ClientSession() as session:
        url = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
        try:
            async with session.post(url, headers=headers, json=params) as response:
                if response.status != 200:
                    rejected = 400 <= response.status < 500
                    logger.error(f"TTS request rejected: HTTP {response.status}")
                    return None

                # 处理 HTTP Chunked stream，每个 chunk 为一个 JSON 对象
                async for line in response.content:
                    if line:
                        line_str = line.decode("utf-8").strip()
                        if not line_str:
                            continue

                        try:
                            data = json.loads(line_str)
                            reported = (data.get("usage") or {}).get("text_words")
                            if isinstance(reported, (int, float)) and not isinstance(reported, bool) and reported >= 0:
                                usage_chars = reported
                            code = data.get("code", 0)
                            if code == 0 and data.get("data"):
                                chunk_audio = base64.b64decode(data["data"])
                                audio_data.extend(chunk_audio)
                            elif code == 20000000:
                                finished = True
                                logger.info("语音合成完毕")
                                break
                            elif code != 0:
                                logger.error(
                                    f"TTS 合成出错: code={code}, message={data.get('message')}"
                                )
                                return None
                        except json.JSONDecodeError:
                            logger.error(
                                "JSON decoding error in TTS response"
                            )
                            continue

                # 返回base64编码数据
                if not audio_data:
                    logger.warning(f"流结束但未收到任何音频数据:{audio_data}")
                    return None

                try:
                    # Volcengine PCM is mono signed 16-bit little-endian audio.
                    # Package the complete stream once; no ffmpeg/ffprobe needed.
                    if len(audio_data) % 2:
                        raise ValueError("Incomplete 16-bit PCM sample")
                    with io.BytesIO() as output:
                        with wave.open(output, "wb") as wav:
                            wav.setnchannels(1)
                            wav.setsampwidth(2)
                            wav.setframerate(self.sample_rate)
                            wav.writeframes(audio_data)
                        return base64.b64encode(output.getvalue()).decode("ascii")
                except Exception as e:
                    logger.error(f"PCM/WAV packaging failed: {e}")

        except Exception as e:
            logger.error(f"Request failed: {e}")
        finally:
            if meter:
                try:
                    meter.finish(
                        request_id,
                        characters=usage_chars if usage_chars is not None else len(text) if finished else None,
                        seconds=len(audio_data) / 2 / self.sample_rate if audio_data else None,
                        char_basis="provider" if usage_chars is not None else "text_estimate",
                        duration_basis="pcm",
                        state="completed" if finished else "rejected" if rejected else "uncertain",
                    )
                except Exception:
                    logger.warning("Speech accounting settlement failed; request needs reconciliation")
