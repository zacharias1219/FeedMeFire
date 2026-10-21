"""Roast languages. English plus a short list of Indian languages Sarvam supports."""
from __future__ import annotations

from .schemas import DISCLAIMER

LANGUAGES: dict[str, dict[str, str]] = {
    "en": {"name": "English", "native": "English", "disclaimer": DISCLAIMER},
    # Disclaimer translations are plain statements of the English text; have a native speaker review before launch.
    "hi": {"name": "Hindi", "native": "हिन्दी",
           "disclaimer": "AI द्वारा बनाया गया मनोरंजन, जो केवल आपके चुने हुए नमूने पर आधारित है। यह कोई मनोवैज्ञानिक आकलन या आपकी वास्तविक देखने की हिस्ट्री का रिकॉर्ड नहीं है।"},
    "ta": {"name": "Tamil", "native": "தமிழ்",
           "disclaimer": "இது AI உருவாக்கிய பொழுதுபோக்கு; நீங்கள் தேர்ந்தெடுத்த மாதிரியை மட்டுமே அடிப்படையாகக் கொண்டது. இது உளவியல் மதிப்பீடோ உங்கள் உண்மையான பார்வை வரலாற்றின் பதிவோ அல்ல."},
    "te": {"name": "Telugu", "native": "తెలుగు",
           "disclaimer": "ఇది AI రూపొందించిన వినోదం, మీరు ఎంచుకున్న నమూనా ఆధారంగా మాత్రమే. ఇది మానసిక అంచనా కాదు, మీ నిజమైన వీక్షణ చరిత్ర రికార్డు కూడా కాదు."},
    "bn": {"name": "Bengali", "native": "বাংলা",
           "disclaimer": "এটি AI-তৈরি বিনোদন, শুধুমাত্র আপনার বেছে নেওয়া নমুনার ওপর ভিত্তি করে। এটি কোনো মনস্তাত্ত্বিক মূল্যায়ন বা আপনার প্রকৃত দেখার ইতিহাসের রেকর্ড নয়।"},
    "mr": {"name": "Marathi", "native": "मराठी",
           "disclaimer": "हे AI ने तयार केलेले मनोरंजन आहे, जे केवळ तुम्ही निवडलेल्या नमुन्यावर आधारित आहे. हे मानसशास्त्रीय मूल्यांकन किंवा तुमच्या प्रत्यक्ष पाहण्याच्या इतिहासाची नोंद नाही."},
}


def language_list() -> list[dict[str, str]]:
    return [{"code": c, "name": v["name"], "native": v["native"]} for c, v in LANGUAGES.items()]


def disclaimer_for(code: str) -> str:
    return LANGUAGES.get(code, LANGUAGES["en"])["disclaimer"]
