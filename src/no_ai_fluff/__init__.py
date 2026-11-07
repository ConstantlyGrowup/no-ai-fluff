"""no-ai-fluff：交付层中文语言净化引擎。

四阶流水线：
    Stage 0  masker     结构剥离 + 行内代码/公式 {{Pn}} 掩码冻结
    Stage 1  rtt        OpenRouter 翻译小模型往返解压（中 -> 桥接语 -> 中）
    Stage 2a radar      5 类通用表层雷达（仅提示，不放行）
    Stage 2b/3 patcher  宿主模型审校提示词 + JSON 补丁安全合并与占位符守卫
"""

__version__ = "0.1.0"

__all__ = ["Pipeline", "PipelineConfig"]


def __getattr__(name: str):  # 惰性导入，避免 `import no_ai_fluff.masker` 时连带加载网络模块
    if name in __all__:
        from . import pipeline

        return getattr(pipeline, name)
    raise AttributeError(name)
