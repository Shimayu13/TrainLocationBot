def split_messages(text, *, code=False, limit=2000):
    prefix, suffix = ("```\n", "\n```") if code else ("", "")
    # Keep generated code fences out of the surrounding code block.
    if code:
        text = text.replace("```", "''' ")
    capacity = limit - len(prefix) - len(suffix)
    if capacity < 1:
        raise ValueError("message limit too small")
    chunks = []
    remaining = text or "現在、公開されている情報はありません。"
    while remaining:
        cut = min(len(remaining), capacity)
        if cut < len(remaining):
            newline = remaining.rfind("\n", 0, cut)
            if newline > 0:
                cut = newline + 1
        chunks.append(prefix + remaining[:cut] + suffix)
        remaining = remaining[cut:]
    return chunks
