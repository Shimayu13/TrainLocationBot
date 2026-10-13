"""Compare fixed, non-private prompts on the local model. No Discord IO."""
import argparse
import asyncio
import json
from pathlib import Path
import time

from trainbot.ai import AIChat
from trainbot.config import Settings

BASELINE_INSTRUCTIONS = (
    "日本語で簡潔に会話するDiscordアシスタントです。"
    "私が使用しているモデルはApple Foundation Model Core 3(Advanced)であり、Appleの利用規約に従って動作します。"
)
PROMPTS = [
    ("explanation", "Pythonの仮想環境は何のために使うのですか？初心者にも分かるように説明してください。"),
    ("ideas", "冷蔵庫に卵、キャベツ、ご飯があります。夕食のアイデアを3つ、作り方と一緒に提案してください。"),
    ("brief", "Pythonの仮想環境を使う理由を一文で短く教えてください。"),
]


async def evaluate(output):
    settings = Settings.load()
    ai = AIChat(settings)
    await asyncio.to_thread(ai.initialize)
    if not ai.available:
        raise SystemExit(ai.status)
    rows = []
    for label, prompt in PROMPTS:
        for variant in ["before", "after"]:
            session = ai.sdk.LanguageModelSession(model=ai.model, instructions=BASELINE_INSTRUCTIONS) if variant == "before" else ai._new_session()
            kwargs = {} if variant == "before" else {"options": ai._generation_options()}
            started = time.monotonic()
            try:
                response = str(await asyncio.wait_for(session.respond(prompt, **kwargs), settings.ai_timeout))
                row = {"task": label, "variant": variant, "prompt": prompt, "response": response,
                       "characters": len(response), "seconds": round(time.monotonic() - started, 2)}
                print(f"{label} {variant}: {len(response)} characters, {row['seconds']}s", flush=True)
            except Exception as error:
                row = {"task": label, "variant": variant, "error": type(error).__name__}
                print(label, variant, "ERROR", type(error).__name__, flush=True)
            rows.append(row)
            output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    print("Results:", output, flush=True)
    if any("error" in row for row in rows):
        raise SystemExit(1)


async def evaluate_modes(output):
    ai = AIChat(Settings.load())
    await asyncio.to_thread(ai.initialize)
    if not ai.available:
        raise SystemExit(ai.status)
    prompt = "Pythonの仮想環境の役割と使い方を、初心者にも分かるように説明してください。"
    rows = []
    for mode in ["lite", "flash", "plus"]:
        key = (0, 0, 0)
        await ai.set_mode(key, mode)
        started = time.monotonic()
        response = await ai.respond(key, prompt)
        row = {"mode": mode, "prompt": prompt, "response": response, "characters": len(response), "seconds": round(time.monotonic() - started, 2)}
        rows.append(row)
        output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        print(mode, row["characters"], "characters", row["seconds"], "seconds", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", action="store_true", help="Compare Lite, Flash, and Plus on the same prompt")
    args = parser.parse_args()
    asyncio.run(evaluate_modes(args.output) if args.modes else evaluate(args.output))


if __name__ == "__main__":
    main()
