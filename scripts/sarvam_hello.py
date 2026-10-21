"""One Sarvam chat completion. Reads SARVAM_API_KEY from the environment or .env; never prints it."""
import os
import sys

from dotenv import load_dotenv
from sarvamai import SarvamAI

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()
key = os.environ.get("SARVAM_API_KEY", "").strip()
if not key:
    sys.exit("SARVAM_API_KEY is not set. Add it to .env and run again.")

client = SarvamAI(api_subscription_key=key)
resp = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[{"role": "user", "content": "Say hello in Hindi, Tamil and English, one short line each."}],
)
print(resp.choices[0].message.content)
