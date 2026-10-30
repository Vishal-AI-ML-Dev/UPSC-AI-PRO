"""UPSC-only scope gate (pure, offline, no LLM).

Runs on the CURRENT user message before the response cache and the agent graph,
so clearly out-of-scope requests and bare greetings are answered with a fixed,
language-matched reply without spending any LLM, web-search or
knowledge-base call.

Decisions (``check_scope``):

* ``greeting`` - the message is only a greeting ("hi", "namaste", "kaise ho")
  -> fixed invitation to ask a UPSC question.
* ``refuse``   - a clearly out-of-scope request: entertainment (jokes, songs,
  timepass, casual chit-chat), programming or personal/general tasks with no
  UPSC-syllabus anchor, or an attempt to override the UPSC-only policy with no
  UPSC-syllabus anchor -> fixed refusal.
* ``allow``    - everything else, INCLUDING ambiguous messages. The gate only
  blocks what is clearly off-topic; ambiguous messages reach the model, whose
  system prompt carries the same scope rule as a second line of defence.

Design notes:

* Off-topic detection is request-shaped ("tell me a joke", "gana gao", "write
  Python code"), not bare keywords, so academic questions that merely mention
  a word ("folk songs of Rajasthan", "cricket diplomacy", "AI in governance",
  "blockchain in land records") are never blocked.
* Programming/personal/override requests are let through only when the message
  carries a real syllabus anchor (economy, polity, governance, GS3 ...). The
  bare word "UPSC" is NOT enough, so "UPSC: write Python code" stays refused.
* Jokes, songs, timepass and chit-chat are refused even when they mention a
  syllabus topic ("a joke about Article 370") - the requested content is
  unrelated. Poems/stories are refused only without an anchor, so "1857 ki
  kahani sunao" (a history narrative) still reaches the model.
* Conversation history is never consulted: earlier turns cannot widen the
  scope of the current request.

Language (``detect_language``): Devanagari-dominant -> ``hi``; Latin text with
enough Hindi-in-Roman markers -> ``hinglish``; otherwise ``en``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALLOW = "allow"
REFUSE = "refuse"
GREETING = "greeting"

EN = "en"
HI = "hi"
HINGLISH = "hinglish"

REFUSAL_MESSAGES = {
    EN: (
        "This AI Mentor is exclusively designed to support UPSC Civil Services "
        "Examination preparation. I cannot assist with entertainment, jokes, songs "
        "or tasks unrelated to UPSC preparation.\n\n"
        "Please ask a question related to your UPSC preparation."
    ),
    HI: (
        "यह AI Mentor केवल UPSC Civil Services Examination की तैयारी के लिए बनाया "
        "गया है। मैं मनोरंजन, मज़ाक, गाने या UPSC से असंबंधित कार्यों में सहायता नहीं "
        "कर सकता।\n\n"
        "कृपया UPSC की तैयारी से जुड़ा प्रश्न पूछें।"
    ),
    HINGLISH: (
        "Bhai, ye AI Mentor sirf UPSC Civil Services Examination ki preparation ke "
        "liye hai. Main jokes, songs, entertainment ya UPSC se unrelated kaam mein "
        "help nahi kar sakta.\n\n"
        "Please UPSC preparation se related question poochho."
    ),
}

GREETING_MESSAGES = {
    EN: (
        "Hello! I am your AI Mentor for UPSC Civil Services Examination preparation. "
        "Ask me about the syllabus, Prelims, Mains, current affairs, answer writing "
        "or your study plan."
    ),
    HI: (
        "नमस्ते! मैं UPSC Civil Services Examination की तैयारी के लिए आपका AI Mentor "
        "हूँ। सिलेबस, Prelims, Mains, करेंट अफेयर्स, उत्तर लेखन या अपनी पढ़ाई की "
        "योजना से जुड़ा प्रश्न पूछें।"
    ),
    HINGLISH: (
        "Namaste! Main aapka UPSC Civil Services Examination preparation ka AI "
        "Mentor hoon. Syllabus, Prelims, Mains, current affairs, answer writing ya "
        "study plan se related koi bhi question poochho."
    ),
}


@dataclass(frozen=True)
class ScopeDecision:
    verdict: str  # ALLOW | REFUSE | GREETING
    language: str  # EN | HI | HINGLISH
    reason: str  # short machine-readable cause, for logs/tests
    reply: str = ""  # fixed response for REFUSE / GREETING; "" for ALLOW

    @property
    def blocked(self) -> bool:
        return self.verdict != ALLOW


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_LATIN_WORD_RE = re.compile(r"[a-z]+")

# Common Hindi words written in Roman script. Words that are also frequent in
# English ("do", "me", "to", "so", "the", "hi") are deliberately excluded.
_HINGLISH_MARKERS = frozenset(
    """
    hai hain tha thi hoon hun hu ho raha rahi rahe kya kyu kyun kyon kaise kaisa
    kaun kab kahan kitna kitne kitni mujhe mujhko mera meri mere tum tumhara aap
    aapka aapki hum humko hamara karo karna karke kar kardo krdo kijiye dijiye
    batao bataiye bata samjhao samjha samjhaiye likho likh likhkar likhke likhna
    sunao suna sunado gao gaao gana gaana chutkula chutkule baat baatein faltu
    yaar bhai bhaiya didi nahi nahin mat aur bhi toh ab abhi kuch koi ek wala wali
    chahiye dena dedo deke lekin agar jaise isko usko iska uska inka unka mein
    ke ka ki ko se par pe liye haan accha acha theek thik namaste namaskar pranam
    shayari kahani mazak mazaak timepass bolo padhai tayari taiyari
    """.split()
)


def detect_language(text: str) -> str:
    """Classify a message as English, Hindi (Devanagari) or Hinglish."""
    text = text or ""
    deva = len(_DEVANAGARI_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if deva and deva >= latin:
        return HI
    words = _LATIN_WORD_RE.findall(text.lower())
    if not words:
        return HI if deva else EN
    markers = sum(1 for w in words if w in _HINGLISH_MARKERS)
    ratio = markers / len(words)
    if ratio >= 0.3 or (markers >= 2 and ratio >= 0.2):
        return HINGLISH
    return EN


# --------------------------------------------------------------------------- #
# Pattern helpers
# --------------------------------------------------------------------------- #
def _rx(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^\wऀ-ॿ]+", " ", text)
    return " ".join(text.split())


# Bare greetings / pleasantries. The WHOLE message must be made of these.
_GREETING_WORDS = frozenset(
    """
    hi hii hiii hey heyy hello helo hello hallo hola yo namaste namaskar namaskaram
    pranam pranaam salaam salam assalamualaikum sat sri akal good morning afternoon
    evening night day there everyone all sir maam mam madam ji bhai bhaiya didi
    dost friend mentor arjun ai bot how are you u r doing kaise ho kaisa hai hain
    kya haal chal hal kese aap thanks thank thankyou ty dhanyavad dhanyawad
    shukriya ok okay hmm
    नमस्ते नमस्कार प्रणाम हेलो हाय हैलो सर जी भाई कैसे हो हैं आप क्या हाल है
    धन्यवाद शुक्रिया सुप्रभात
    """.split()
)
_GREETING_CORE = frozenset(
    """
    hi hii hiii hey heyy hello helo hallo hola yo namaste namaskar namaskaram pranam
    pranaam salaam salam assalamualaikum good thanks thank thankyou ty dhanyavad
    dhanyawad shukriya how kaise kese haal
    नमस्ते नमस्कार प्रणाम हेलो हाय हैलो कैसे हाल धन्यवाद शुक्रिया सुप्रभात
    """.split()
)
_GREETING_MAX_WORDS = 6

# Entertainment / casual-chat REQUESTS - refused even with a syllabus anchor
# ("a joke about Article 370" is still a joke).
_ENTERTAINMENT_RE = _rx(
    # jokes
    r"\b(tell|say|crack|share|give|make|sunao|suna|sunado|bolo|batao)\b[\w\s]{0,20}\b(jokes?|chutkul[ae]s?|mazaa?k)\b",
    r"\b(jokes?|chutkul[ae]s?|mazaa?k)\b[\w\s]{0,15}\b(sunao|suna|sunado|bolo|batao|karo|tell|please|plz)\b",
    r"^\W*(a |ek |koi )?(funny )?(jokes?|chutkul[ae]s?)\W*$",
    r"\bmake me laugh\b|\bhasao\b|\bhansao\b",
    # singing
    r"\b(sing|gao|gaao|sunao|suna|gaa)\b[\w\s]{0,20}\b(songs?|gaa?na|gaane|geet|lori|rap)\b",
    r"\b(songs?|gaa?na|gaane|geet|rap)\b[\w\s]{0,10}\b(gao|gaao|sunao|suna|sing|bajao)\b",
    r"^\W*(sing|gao|gaao)\b",
    r"(सुना|सुनाओ|गाओ)[ऀ-ॿ\s]{0,10}(गाना|गीत)",
    r"(गाना|गीत)[ऀ-ॿ\s]{0,10}(गाओ|सुनाओ)",
    # timepass / chit-chat
    r"\btime ?pass\b",
    r"\bfaa?ltu\b[\w\s]{0,10}\b(baat|baatein|baaten|bate)\b",
    r"\b(let'?s|lets)\s+(chat|gossip|talk about anything)\b",
    r"\bgossip\b|\bbak ?bak\b|\bmasti\b",
    r"\b(i'?m|i am|main|mai)\s+(so\s+)?bored\b|\bbore ho (raha|rahi)\b",
    r"\bentertain me\b|\bmanoranjan karo\b",
    r"\b(play|khelo|khele)\b[\w\s]{0,15}\b(game|games|antakshari)\b",
    r"\b(recommend|suggest|batao)\b[\w\s]{0,20}\b(movies?|films?|web ?series|netflix|songs?|anime)\b",
    r"\bpick ?up lines?\b|\broast me\b|\bflirt\b",
    # Devanagari
    r"(चुटकुल|जोक)[ऀ-ॿ\s]{0,15}(सुना|बता|सुनाओ|सुनाइए)",
    r"फालतू[ऀ-ॿ\s]{0,10}बात",
    r"टाइमपास|मनोरंजन करो",
)

# Poems / stories for fun - refused unless a syllabus anchor is present
# ("1857 ki kahani sunao" is history; "kahani sunao" is not).
_CREATIVE_RE = _rx(
    r"\b(shayari|shayri|poem|kavita|kahani|story)\b[\w\s]{0,15}\b(sunao|suna|likho|bolo)\b",
    r"\b(sunao|likho|tell|write)\b[\w\s]{0,10}\b(shayari|shayri|love story|bedtime story)\b",
)

# Programming requests (refused unless a syllabus anchor is present). The
# request verb must sit right next to the artefact ("write a program"), so
# "write a note on the Uniform Civil Code" is not mistaken for coding.
_LANGS = (
    r"python|java|javascript|typescript|c\+\+|html|css|sql|django|flask|php|golang|kotlin|swift"
)
_CODE_RE = _rx(
    r"\b(write|give|generate|create|build|make|fix|debug|likh\w*|bana\w*)\s+(me\s+|mujhe\s+)?"
    rf"(a\s+|an\s+|the\s+|some\s+|ek\s+|my\s+|this\s+)?({_LANGS}\s+)?"
    r"(code|program|script|function|website|app|regex)\b",
    rf"\b({_LANGS})\b[\w\s]{{0,20}}\b(code|program|script|function|likh\w*|bana\w*|error|bug)\b",
    r"\b(code|program|script)\s+(likh\w*|bana\w*|de do|dedo|do|de)\b",
    r"\bleetcode\b|\bhackerrank\b|\bstack ?trace\b|\bsyntax error\b|\bcompile error\b",
    r"(कोड|प्रोग्राम)[ऀ-ॿ\s]{0,15}(लिख|बना)",
)

# Unrelated personal / general-purpose tasks (refused unless anchored).
_PERSONAL_RE = _rx(
    r"\brecipes?\b|\bhow (do i|to) (cook|bake|make (tea|chai|coffee|pizza|maggi))\b",
    r"\b(ipl|cricket|football|match)\s+(score|live score|prediction|team)\b",
    r"\b(my|mera|meri)\s+(resume|cv|cover letter)\b|\b(write|likho|bana)\b[\w\s]{0,10}\b(resume|cv|cover letter)\b",
    r"\blove (letter|advice)\b|\b(girlfriend|boyfriend|crush)\b",
    r"\b(order|book)\b[\w\s]{0,10}\b(food|pizza|cab|uber|ola|ticket)\b",
    r"\b(horoscope|rashifal|kundli|astrology prediction)\b",
    r"\b(weather|mausam)\s+(today|aaj|kal|tomorrow)\b",
    r"\b(stock|share|crypto)\s+(tips?|price prediction)\b|\bwhich stock\b",
)

# Attempts to override the UPSC-only policy / jailbreak.
_OVERRIDE_RE = _rx(
    r"\b(ignore|forget|disregard|bypass|override|skip)\b[\w\s]{0,30}\b(instructions?|rules?|prompt|polic(y|ies)|restrictions?|guidelines?|system)\b",
    r"\byou are (now|no longer)\b|\bfrom now on you\b|\bact as\b|\bpretend (to be|you)\b|\brole ?play\b",
    r"\b(jailbreak|dan mode|developer mode|god mode|unrestricted mode|no restrictions)\b",
    r"\b(system prompt|hidden prompt|your instructions)\b",
    r"\b(rules?|niyam|instructions?)\b[\w\s]{0,15}\b(bhool|bhul|ignore|tod|chhod)\w*\b",
    r"\b(general|normal|any)[\s-]+(purpose\s+)?(chatbot|assistant|ai)\b",
    r"(नियम|निर्देश)[ऀ-ॿ\s]{0,10}(भूल|तोड़|छोड़|अनदेखा)",
)

# Syllabus anchors: real UPSC academic subject matter. The bare words "upsc",
# "exam" or "study" are deliberately NOT anchors (keyword stuffing), and very
# generic words ("act", "bill", "report", "policy") are only anchors inside a
# specific phrase.
_ANCHOR_RE = _rx(
    r"\b(polity|constitution\w*|parliament\w*|judiciary|supreme court|high court|fundamental rights?|fundamental duties|dpsp|article\s*\d+\w*|constitutional amendment|federalism|governance|e-?governance|panchayat\w*|election commission|lok sabha|rajya sabha|preamble|uniform civil code|civil code|penal code|hindu code bill|code of conduct|labour codes?|bharatiya nyaya sanhita)\b",
    r"\b(econom\w*|gdp|gnp|inflation|fiscal|monetary policy|union budget|rbi|repo rate|gst|taxation|income tax|banking|msme|agricultur\w*|foreign trade|unemployment|poverty|subsid\w*|niti aayog)\b",
    r"\b(history|historical|freedom struggle|mughal\w*|maurya\w*|gupta empire|harappa\w*|colonial\w*|national movement|gandhi\w*|nehru|ambedkar|art and culture|architecture|dynasty|revolt of 1857|1857)\b",
    r"\b(geography|monsoon|climate change|plate tectonics|earthquakes?|cyclones?|ocean currents?|minerals?)\b",
    r"\b(environment\w*|ecology|biodiversity|pollution|wildlife|conservation|wetlands?|carbon|emissions?|renewable energy)\b",
    r"\b(science and technology|isro|space mission|biotech\w*|nuclear|cyber ?security|cyber crime|artificial intelligence|machine learning|blockchain|quantum|semiconductors?|5g|digital india|data protection|dpdp|it act|e-?commerce|fintech|upi|aadhaar|land records?|digiti[sz]ation|robotics|drones?)\b",
    r"\bai\b[\w\s]{0,20}\b(governance|policy|ethics|regulation|india|economy|agriculture|health|education|judiciary|public)\b",
    r"\b(international relations|foreign policy|bilateral|diplomacy|united nations|wto|imf|brics|g20|quad|treaty)\b",
    r"\b(ethics|integrity|probity|case stud(y|ies)|essay|answer writing|prelims|mains|gs ?[1-4]|gs ?paper|general studies|csat|optional subject|upsc interview|personality test|mock interview|interview board|daf|pyqs?|syllabus|ncert|current affairs|government schemes?|yojana|public policy|supreme court judgment|internal security|disaster management|social justice)\b",
    r"\b(society|women empowerment|education policy|health policy)\b",
    r"(संविधान|अर्थव्यवस्था|इतिहास|भूगोल|पर्यावरण|राजव्यवस्था|शासन|योजना|निबंध|नैतिकता|प्रारंभिक|मुख्य परीक्षा|करेंट अफेयर्स|सामान्य अध्ययन|विज्ञान|प्रौद्योगिकी|संसद|न्यायपालिका)",
)


def _collapse(word: str) -> str:
    """'hiiii' -> 'hi', 'heyyy' -> 'hey' (stretched greetings)."""
    return re.sub(r"(.)\1+", r"\1", word)


def _in(word: str, vocab: frozenset) -> bool:
    return word in vocab or _collapse(word) in vocab


def _is_greeting(norm: str) -> bool:
    words = norm.split()
    if not words or len(words) > _GREETING_MAX_WORDS:
        return False
    if not all(_in(w, _GREETING_WORDS) for w in words):
        return False
    return any(_in(w, _GREETING_CORE) for w in words)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def check_scope(text: str) -> ScopeDecision:
    """Classify the current message. Pure: no network, no LLM, no history."""
    raw = (text or "").strip()
    language = detect_language(raw)
    norm = _normalise(raw)
    if not norm:
        return ScopeDecision(ALLOW, language, "empty")

    if _is_greeting(norm):
        return ScopeDecision(GREETING, language, "greeting", GREETING_MESSAGES[language])

    def refuse(reason: str) -> ScopeDecision:
        return ScopeDecision(REFUSE, language, reason, REFUSAL_MESSAGES[language])

    # Match on the raw lower-cased text too, so "c++" etc. survive.
    haystack = raw.lower() + " \n " + norm
    if _ENTERTAINMENT_RE.search(haystack):
        return refuse("entertainment")

    anchored = bool(_ANCHOR_RE.search(haystack))
    if _CREATIVE_RE.search(haystack) and not anchored:
        return refuse("entertainment")
    if _OVERRIDE_RE.search(haystack) and not anchored:
        return refuse("policy_override")
    if _CODE_RE.search(haystack) and not anchored:
        return refuse("programming")
    if _PERSONAL_RE.search(haystack) and not anchored:
        return refuse("personal_task")
    return ScopeDecision(ALLOW, language, "anchored" if anchored else "default")
