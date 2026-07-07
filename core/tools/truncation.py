"""Output truncation utility — 共享截断逻辑。

fc_loop 的工具结果截断和 run_shell 的 shell 输出截断共用此函数。
"""


def truncate_output(
    text: str,
    max_size: int,
    *,
    unit: str = "chars",
    label: str = "",
    head_ratio: float = 0.5,
) -> str:
    """截断过长文本，保留首尾，中间插入截断标记。

    Args:
        text: 要截断的文本
        max_size: 上限（字符数或字节数，取决于 unit）
        unit: "chars"（字符数）或 "bytes"（UTF-8 字节数）
        label: 截断标记的自定义文本（可选，默认使用通用标记）
        head_ratio: 头部占比（0.0–1.0，默认 0.5 首尾各半）。
                    小于 0.5 时尾部更多（适合 shell 输出/日志），
                    大于 0.5 时头部更多（适合文件内容）。

    Returns:
        截断后的文本，未超出上限时返回原文本
    """
    if unit == "bytes":
        size = len(text.encode("utf-8"))
    else:
        size = len(text)

    if size <= max_size:
        return text

    head_size = int(max_size * head_ratio)
    tail_size = max_size - head_size
    head = text[:head_size]
    tail = text[-tail_size:]
    marker = label or "输出过大，已截断"

    return (
        f"{head}\n\n"
        f"…（{marker}）…\n\n"
        f"{tail}"
    )
