"""Thin wrapper around the OpenAI API that always returns parsed JSON."""
import json
import time

from . import config


class LLMError(Exception):
    pass


def ask_json(system: str, user: str, temperature: float = 0.2) -> dict:
    if not config.OPENAI_API_KEY or config.OPENAI_API_KEY == "your-openai-api-key-here":
        raise LLMError("OPENAI_API_KEY is missing in .env")
    try:
        from openai import OpenAI
    except ImportError:
        raise LLMError("the 'openai' package is not installed - run: pip install -r requirements.txt")

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    last = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature,
                response_format={"type": "json_object"},  # forces valid JSON, no markdown fences
            )
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError as e:
            last = e
        except Exception as e:  # network / rate limit
            last = e
            time.sleep(2 * (attempt + 1))
    raise LLMError(f"OpenAI call failed after 3 attempts: {last}")
