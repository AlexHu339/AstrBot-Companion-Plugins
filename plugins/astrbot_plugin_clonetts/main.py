import random
import asyncio
import json

from pydantic import Field
from pydantic.dataclasses import dataclass

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.config import AstrBotConfig
from astrbot.core.provider.entities import LLMResponse

from .tts_api.dy_tts_api import tts_http_stream
from .voice_support import expression_context, save_receipt, track_delivery


def merge_voice(chain, record, followup=""):
    """Replace text while retaining images, quotes, mentions and other media."""
    merged, inserted = [], False
    for component in chain:
        if isinstance(component, Comp.Plain):
            if not inserted:
                merged.append(record)
                if followup:
                    merged.append(Comp.Plain(followup))
                inserted = True
        else:
            merged.append(component)
    if not inserted:
        merged.insert(1 if merged and isinstance(merged[0], Comp.Reply) else 0, record)
        if followup:
            merged.append(Comp.Plain(followup))
    return merged


@register(
    "astrbot_plugin_clonetts",
    "Radiant303",
    "基于火山引擎音色克隆(ICL)的文本转语音插件",
    "3.0.1+companion.1",
)
class CloneTTSPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # --- 配置读取与防御性校验 ---
        self.enable_tts = bool(config.get("enable_tts", True))
        self.enable_llm_tool = bool(config.get("enable_llm_tool", True))
        self.enable_llm_response = bool(config.get("enable_llm_response", False))
        self.enable_tts_tool_minmax = bool(config.get("enable_tts_tool_minmax", True))
        self.explicit_min_length = max(1, int(config.get("explicit_min_length", 1)))
        self.explicit_max_length = max(
            self.explicit_min_length,
            min(5000, int(config.get("explicit_max_length", 1000))),
        )
        self.blocked_words = config.get("blocked_words", [])
        self.llm_recognition = config.get("llm_recognition", "")

        # 概率 & 长度：强制转为数值并限制在合理范围
        try:
            self.tts_probability = max(
                0, min(100, float(config.get("tts_probability", 80)))
            )
        except (TypeError, ValueError):
            logger.warning("tts_probability 配置值无效，已使用默认值 100")
            self.tts_probability = 80

        try:
            self.max_length = max(1, int(config.get("max_length", 50)))
        except (TypeError, ValueError):
            logger.warning("max_length 配置值无效，已使用默认值 50")
            self.max_length = 50

        try:
            self.min_length = max(1, int(config.get("min_length", 5)))
        except (TypeError, ValueError):
            logger.warning("min_length 配置值无效，已使用默认值 5")
            self.min_length = 5

        # 凭证类字段：确保为非空字符串
        self.api_key = str(config.get("api_key") or "")
        self.voice_type = str(config.get("voice_type") or "")

        # 音频参数
        try:
            self.speed_ratio = max(-50, min(100, int(config.get("speed_ratio", 0))))
        except (TypeError, ValueError):
            self.speed_ratio = 0

        try:
            self.loudness_rate = max(-50, min(100, int(config.get("loudness_rate", 0))))
        except (TypeError, ValueError):
            self.loudness_rate = 0
        try:
            self.sample_rate = int(config.get("sample_rate", 24000))
        except (TypeError, ValueError):
            self.sample_rate = 24000

        # 启动时预检关键配置
        missing = [
            name
            for name, val in [
                ("api_key", self.api_key),
                ("voice_type", self.voice_type),
            ]
            if not val
        ]
        if missing and self.enable_tts:
            logger.warning(
                f"CloneTTS 以下必要配置为空，语音合成可能失败: {', '.join(missing)}"
            )
        self.context.add_llm_tools(CloneTTSTool(plugin=self))

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        from .tts_api.speech_meter import meter_for

        meter = meter_for(__file__)
        if meter:
            meter.interrupt_media("voice")
        logger.info("CloneTTS plugin enabled")

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        result = event.get_result()
        if result is None or not result.is_llm_result() or not result.chain:
            return
        pending = event.get_extra("clonetts_pending")
        if pending:
            if self.enable_tts:
                record = Comp.Record.fromBase64(pending["audio"])
                record.text = pending["text"]
                result.chain = merge_voice(
                    result.chain, record, pending.get("followup", "")
                )
                event.set_extra("clonetts_delivering", pending)
                track_delivery(event, pending, record)
            event.set_extra("clonetts_pending", None)
            return
        if (
            not self.enable_tts
            or not self.api_key
            or not self.voice_type
            or event.get_extra("companion_native_tts_sent", False)
            or event.get_extra("companion_proactive_tts_allowed", True) is False
            or not self.probability(self.tts_probability)
        ):
            return
        spoken = "".join(
            c.text for c in result.chain if isinstance(c, Comp.Plain)
        ).strip()
        if not self.min_length <= len(spoken) <= self.max_length or any(
            w in spoken for w in self.blocked_words
        ):
            return
        persona = "default"
        try:
            prompt, background, persona = await expression_context(self, event)
            provider_id = (
                self.llm_recognition
                or await self.context.get_current_chat_provider_id(
                    umo=event.unified_msg_origin
                )
            )
            response = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    system_prompt=prompt
                    + "\n[语音合成（TTS）控制]只描述如何朗读给定正文；沿用人格、当前关系与情绪。理解嘴硬、玩笑和反话，不改写正文，不另设身份。只输出80字以内的中文语气指令，不复述背景资料。",
                    prompt=json.dumps(
                        {"spoken_text": spoken, "background": background},
                        ensure_ascii=False,
                    ),
                    max_tokens=160,
                    request_max_retries=1,
                ),
                timeout=45,
            )
            style = (response.completion_text or "")[:300]
        except Exception:
            logger.warning("Voice style context unavailable; using neutral delivery")
            style = "自然朗读原文，保留原本的轻重语气，不另设身份"
        audio = await tts_http_stream(self, spoken, style)
        if not audio:
            save_receipt(event, persona, "failed", spoken)
            return
        record = Comp.Record.fromBase64(audio)
        record.text = spoken
        result.chain = merge_voice(result.chain, record)
        delivery = {"text": spoken, "persona": persona}
        event.set_extra("clonetts_delivering", delivery)
        save_receipt(event, persona, "ready", spoken)
        track_delivery(event, delivery, record)

    def probability(self, percent) -> bool:
        """以给定百分比概率返回 True（0 = 永不触发，100 = 必定触发）。"""
        try:
            p = float(percent)
        except (TypeError, ValueError):
            return False
        p = max(0.0, min(100.0, p))
        return random.random() < (p / 100)

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("CloneTTS plugin 已经停用/卸载")

    @filter.on_llm_response(priority=100000)
    async def handle_silence(self, event: AstrMessageEvent, resp: LLMResponse):
        pending = event.get_extra("clonetts_pending")
        if not pending:
            return
        if self.enable_llm_response:
            followup = (resp.completion_text or "").strip()
            if followup == pending["text"]:
                followup = ""
            pending["followup"] = followup
        else:
            pending["followup"] = ""
        # Keep the actual spoken text visible to memory and emotion hooks.
        resp.completion_text = pending["text"] + (
            "\n" + pending["followup"] if pending["followup"] else ""
        )
        event.set_extra("voice_silence_mode", False)

    @filter.after_message_sent(priority=50)
    async def voice_sent(self, event: AstrMessageEvent):
        delivery = event.get_extra("clonetts_delivering")
        if (
            delivery
            and not event.get_extra("clonetts_receipt_done")
            and event.get_extra("clonetts_voice_delivered", False)
        ):
            save_receipt(
                event, delivery.get("persona", "default"), "sent", delivery["text"]
            )
            event.set_extra("clonetts_receipt_done", True)


@dataclass
class CloneTTSTool(FunctionTool[AstrAgentContext]):
    name: str = "clone_tts"  # 工具名称
    description: str = (
        "将文本转为语音发送的工具,当用户需要听到声音或者让你说话时候调用"  # 工具描述
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "需要转换为语音的文本",
                },
                "context_texts": {
                    "type": "string",
                    "description": "根据需要转换为语音的文本，深度分析其语境、潜台词和情感流动，然后生成一组用于调整 AI 语音生成的“单句整体风格化”指令。输出一个输出一个指令字符串，旨在指导语音模型调整**语速、情绪/语气、音量、音感/音色**。1. **深度拆解**：分析句子的字面意思与深层含义（如：自嘲、反讽、压抑后的爆发、无奈的叹息等）。 2. **情感定位**：确定核心情绪基调（如：痛心、欢乐、骄傲、苦涩、挑衅）。 3. **动态规划**：判断句子内部是否有情绪转折（如：从前半句的平静到后半句的激动），如果有，需拆分为多条指令分别描述。 4. **指令生成**：将分析结果转化为用户示例中的自然语言指令格式。**必须**只输出一个字符串。 - **不要**输出任何分析过程、解释或额外的文字。 - 指令必须使用自然语言，模仿人类对配音演员的口吻（例如：“你可以用...语气吗？”，“...再...一点”，“嗓门再...点”）。 - 覆盖维度：确保生成的指令涵盖 **语速**、**情绪**、**语气**、**音量**、**音感** 中的五个维度。",
                },
            },
            "required": ["text", "context_texts"],
        }
    )

    plugin: object = Field(default=None, repr=False)

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        if (
            not self.plugin
            or not self.plugin.enable_tts
            or not self.plugin.enable_llm_tool
        ):
            return "语音功能当前关闭，这次没有生成。"
        event = context.context.event
        if event.get_extra("clonetts_pending"):
            return "本轮已经准备好一条语音，不要重复生成。"
        text = str(kwargs.get("text") or "").strip()
        if not text:
            return "语音正文不能为空。"
        if (
            self.plugin.enable_tts_tool_minmax
            and not self.plugin.explicit_min_length
            <= len(text)
            <= self.plugin.explicit_max_length
        ):
            return f"本次点名语音限{self.plugin.explicit_min_length}～{self.plugin.explicit_max_length}字；未生成，不截断原文。"
        identity = event.get_extra("companion_identity") or event.get_extra(
            "qqf_identity"
        )
        persona = str(identity[1]) if identity else "default"
        save_receipt(event, persona, "generating", text)
        style = str(kwargs.get("context_texts") or "自然朗读，沿用当前人格的语气")[:400]
        audio = await tts_http_stream(self.plugin, text, style)
        if not audio:
            save_receipt(event, persona, "failed", text)
            return "本次语音生成失败，没有发出；沿用当前人格如实说明。"
        event.set_extra(
            "clonetts_pending", {"audio": audio, "text": text, "persona": persona}
        )
        save_receipt(event, persona, "ready", text)
        return "语音已经生成，将随本轮回复统一发送；此刻还不能声称已送达。不要重复朗读或生成。"
