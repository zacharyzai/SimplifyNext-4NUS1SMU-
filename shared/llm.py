"""shared/llm.py -- the one call surface for both LLM providers.

No other module may call boto3 or google-genai directly (CLAUDE.md). This
file is the only place that knows how to reach an LLM, switched via
LLM_PROVIDER=bedrock|gemini|none. It transcribes/narrates only -- it never
decides anything, so a caller passing bad output through it is still the
caller's bug, not this file's.

Every path here returns None instead of raising -- on a missing SDK, a
missing key, or an API failure. Callers must already treat None as "no
LLM available, use the deterministic template fallback" per
failure_mode_playbook.md; this file just makes that the only outcome.
"""
import os


def _load_dotenv():
    """Minimal stdlib .env loader -- repo root, KEY=VALUE per line, never
    overrides a var already set in the real environment. No new dependency
    for something this small."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
AWS_REGION = "ap-southeast-1"
AWS_PROFILE = "workshop"
GEMINI_MODEL = "gemini-3.6-flash"


def _bedrock_converse(system_prompt: str, user_text: str):
    try:
        import boto3
    except ImportError:
        print("WARNING: boto3 not installed -- skipping Bedrock call")
        return None
    try:
        session = boto3.Session(
            profile_name=os.getenv("AWS_PROFILE", AWS_PROFILE),
            region_name=os.getenv("AWS_DEFAULT_REGION", AWS_REGION),
        )
        client = session.client("bedrock-runtime")
        response = client.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_text}]}],
            inferenceConfig={"temperature": 0},
        )
        return response["output"]["message"]["content"][0]["text"].strip()
    except Exception as e:
        print(f"WARNING: Bedrock call failed ({e!r}) -- skipping")
        return None


def _gemini_converse(system_prompt: str, user_text: str):
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("WARNING: google-genai not installed -- skipping Gemini call")
        return None
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("WARNING: GEMINI_API_KEY not set -- skipping Gemini call")
        return None
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt, temperature=0
            ),
        )
        return (response.text or "").strip()
    except Exception as e:
        print(f"WARNING: Gemini call failed ({e!r}) -- skipping")
        return None


def converse(system_prompt: str, user_text: str):
    """Send one system-fenced prompt + user text to whichever provider
    LLM_PROVIDER selects. Returns the raw text response, or None if no
    provider is configured, the SDK isn't installed, or the call failed."""
    provider = os.getenv("LLM_PROVIDER", "none").lower()
    if provider == "bedrock":
        return _bedrock_converse(system_prompt, user_text)
    if provider == "gemini":
        return _gemini_converse(system_prompt, user_text)
    return None


if __name__ == "__main__":
    provider = os.getenv("LLM_PROVIDER", "none")
    result = converse("Reply with exactly: OK", "ping")
    print(f"LLM_PROVIDER={provider!r} -> {result!r}")
    assert provider == "none" and result is None or provider != "none"
    print("shared/llm.py OK (ran to completion without raising)")
