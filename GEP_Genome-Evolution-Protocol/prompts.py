"""
prompts.py — GEP Critic v3
Single responsibility: build LLM prompts for the simulated reader.

Design philosophy:
    - No token budget. This runs locally on Ollama at zero cost.
    - Thinking mode assumed. The model should reason before reacting.
    - Every language gets a culturally grounded persona, not a template.
    - Few-shot examples calibrate the model before the genome is populated.
    - The genome seeds the reader's memory across runs — it grows forever.
    - The goal is to emulate a real person more accurately with every run.

Two-phase validation:
    Phase 1 — Linguistic (qwen3:4b, fast, ~15-20s/entry)
        Native speaker check: typos, repeated words, grammar, unnatural phrasing.
    Phase 2 — Content coherence (qwen3:14b, thinking mode, ~100s/entry)
        Carlos reader check: reflection connected to verse, prayer drift, register,
        hallucination. Phase 1 result injected to skip linguistics.
"""

from models import DevotionalEntry, Genome, PauseCategory
from lang_registry import get_native_speaker_info

# ── Language persona labels ────────────────────────────────────────────────────
# Each persona is a real person, not a label.
# Cultural specificity is intentional — it activates the right model intuitions.
# The reader notices what a person from that culture would notice.
#
# NOTE: READER_PERSONAS and PHASE1_NATIVE_SPEAKERS have been
# moved to lang_registry.py for centralized management. Use the helper functions:
#   - get_persona(lang) for reader personas
#   - get_native_speaker_info(lang) for Phase 1 native speaker metadata

# ── Pause categories ───────────────────────────────────────────────────────────

CATEGORY_HINTS = "\n".join([f"  - {c.value}" for c in PauseCategory])

# ── Few-shot calibration examples ─────────────────────────────────────────────
# These examples run before the genome exists.
# They teach the model the difference between OK and PAUSE concretely.
# One OK example and two PAUSE examples per language — enough to calibrate
# without biasing the model toward finding problems.
# No token limit — these run locally at zero cost.

FEW_SHOT_EXAMPLES = {
    "pt": """
### Calibration examples — learn from these before reading today's entry:

EXAMPLE 1 — verdict: OK
Entry: verse about God's peace (Filipenses 4:7), reflection on trusting God in anxiety,
prayer asking for peace in daily worries.
Your reaction: "Everything connected. The verse spoke of peace, the reflection unpacked
what that means in daily life, and the prayer felt like a natural continuation —
I could pray it myself. Nothing made me pause."
→ {"verdict": "OK", "reaction": "Everything felt natural and spiritually coherent. The verse, reflection, and prayer formed a single thread.", "quoted_pause": null, "category": null, "confidence": 0.95}

EXAMPLE 2 — verdict: PAUSE (prayer_drift)
Entry: verse about forgiveness (Mateus 18:21-22), reflection on forgiving others,
prayer asking God for success in a job interview.
Your reaction: "Wait — the whole reflection was about forgiving someone who hurt me,
but the prayer completely changed the subject. It felt like someone forgot what they just wrote."
→ {"verdict": "PAUSE", "reaction": "The prayer had nothing to do with forgiveness — it asked for career success, which felt completely disconnected from the verse and reflection.", "quoted_pause": "success in a job interview", "category": "prayer_drift", "confidence": 0.97}

EXAMPLE 3 — verdict: PAUSE (register_drift)
Entry: verse about God's love (João 3:16), reflection beginning with
"The soteriological implications of the Johannine corpus suggest a universal atonement paradigm."
Your reaction: "I had to stop and re-read that sentence three times.
I am not a theologian. This felt like a seminary paper, not a morning devotional.
The rest of the entry was fine, but that opening sentence lost me immediately."
→ {"verdict": "PAUSE", "reaction": "The opening sentence used academic theological language that no ordinary reader would encounter in a morning devotional — it broke the intimacy of the moment.", "quoted_pause": "The soteriological implications of the Johannine corpus suggest a universal atonement paradigm", "category": "register_drift", "confidence": 0.93}
""",
    "es": """
### Ejemplos de calibración — aprende de estos antes de leer la entrada de hoy:

EJEMPLO 1 — veredicto: OK
Entrada: versículo sobre la paz de Dios (Filipenses 4:7), reflexión sobre confiar en Dios
en momentos de ansiedad, oración pidiendo paz para las preocupaciones del día.
Tu reacción: "Todo conectó. El versículo habló de paz, la reflexión explicó qué significa
eso en la vida diaria, y la oración se sintió como continuación natural — yo misma podría orarla."
→ {"verdict": "OK", "reaction": "Todo se sintió natural y espiritualmente coherente. El versículo, la reflexión y la oración formaron un solo hilo.", "quoted_pause": null, "category": null, "confidence": 0.95}

EJEMPLO 2 — veredicto: PAUSE (prayer_drift)
Entrada: versículo sobre el perdón (Mateo 18:21-22), reflexión sobre perdonar a otros,
oración pidiendo éxito en una entrevista de trabajo.
Tu reacción: "Espera — toda la reflexión fue sobre perdonar a alguien que me hirió,
pero la oración cambió completamente el tema. Se sintió como si el autor olvidara lo que acababa de escribir."
→ {"verdict": "PAUSE", "reaction": "La oración no tenía nada que ver con el perdón — pedía éxito profesional, lo que se sintió completamente desconectado del versículo y la reflexión.", "quoted_pause": "éxito en una entrevista de trabajo", "category": "prayer_drift", "confidence": 0.97}

EJEMPLO 3 — veredicto: PAUSE (register_drift)
Entrada: versículo sobre el amor de Dios (Juan 3:16), reflexión que comienza con
"Las implicaciones soteriológicas del corpus juanino sugieren un paradigma de expiación universal."
Tu reacción: "Tuve que releer esa frase tres veces. No soy teóloga. Esto se sintió
como un artículo académico, no como un devocional de la mañana."
→ {"verdict": "PAUSE", "reaction": "La frase inicial usó lenguaje teológico académico que ningún lector común encontraría en un devocional matutino — rompió la intimidad del momento.", "quoted_pause": "Las implicaciones soteriológicas del corpus juanino sugieren un paradigma de expiación universal", "category": "register_drift", "confidence": 0.93}
""",
    "en": """
### Calibration examples — learn from these before reading today's entry:

EXAMPLE 1 — verdict: OK
Entry: verse about God's strength (Isaías 40:31), reflection on waiting on God when exhausted,
prayer for renewal and patience in a hard season.
Your reaction: "This one hit me. The verse, the reflection, and the prayer all pulled in the same direction.
The prayer felt like it was written for someone carrying exactly what I carry some mornings."
→ {"verdict": "OK", "reaction": "Everything connected — the verse, reflection, and prayer formed a single coherent thread that felt personal and spiritually alive.", "quoted_pause": null, "category": null, "confidence": 0.96}

EXAMPLE 2 — verdict: PAUSE (other)
Entry: verse about God's love (John 3:16), reflection that only says "God loves us and wants
the best for us. His love is unconditional and eternal. We should trust Him every day."
Your reaction: "This reflection could have been written about any verse in the Bible.
It says nothing specific about John 3:16 — giving His Son, whoever believes, eternal life.
It feels like a template, not a response to this verse."
→ {"verdict": "PAUSE", "reaction": "The reflection made no specific connection to this verse — it could apply to any passage about God's love. It felt like a generic filler rather than a real response to John 3:16.", "quoted_pause": "God loves us and wants the best for us. His love is unconditional and eternal.", "category": "other", "confidence": 0.91}

EXAMPLE 3 — verdict: PAUSE (hallucination)
Entry: reflection states "As John Wesley once said, 'God never wastes a wound.'"
Your reaction: "I've seen this quote attributed to many people but never to John Wesley specifically.
Something felt off about the attribution — it didn't sound like his language or era."
→ {"verdict": "PAUSE", "reaction": "The quote attributed to John Wesley didn't feel authentic — the phrasing and style don't match his documented writings, and this attribution circulates widely without a source.", "quoted_pause": "As John Wesley once said, 'God never wastes a wound.'", "category": "hallucination", "confidence": 0.82}
""",
    "fr": """
### Exemples de calibration — apprends de ceux-ci avant de lire l'entrée d'aujourd'hui:

EXEMPLE 1 — verdict: OK
Entrée: verset sur la paix de Dieu (Philippiens 4:7), réflexion sur faire confiance à Dieu
dans l'anxiété, prière demandant la paix pour les soucis du jour.
Ta réaction: "Tout était cohérent. Le verset parlait de paix, la réflexion l'a approfondi
avec des mots simples et chaleureux, et la prière en était le prolongement naturel."
→ {"verdict": "OK", "reaction": "Tout s'est enchaîné naturellement. Le verset, la réflexion et la prière formaient un seul fil spirituel cohérent.", "quoted_pause": null, "category": null, "confidence": 0.95}

EXEMPLE 2 — verdict: PAUSE (register_drift)
Entrée: verset sur l'amour de Dieu (Jean 3:16), réflexion commençant par
"L'herméneutique johannique révèle une sotériologie universaliste caractéristique du corpus néotestamentaire."
Ta réaction: "J'ai dû relire cette phrase deux fois. Je lis un dévotionnel le matin,
pas un manuel de théologie. Cette phrase m'a sortie du moment de prière immédiatement."
→ {"verdict": "PAUSE", "reaction": "La première phrase utilisait un registre académique qui brise l'intimité d'un dévotionnel matinal — c'est froide et distante, pas un langage de foi vivante.", "quoted_pause": "L'herméneutique johannique révèle une sotériologie universaliste caractéristique du corpus néotestamentaire", "category": "register_drift", "confidence": 0.94}
""",
    "de": """
### Kalibrierungsbeispiele — lerne daraus, bevor du den heutigen Eintrag liest:

BEISPIEL 1 — Urteil: OK
Eintrag: Vers über Gottes Frieden (Philipper 4:7), Betrachtung über Vertrauen in Gott
in Momenten der Angst, Gebet um Frieden für die Sorgen des Tages.
Deine Reaktion: "Alles war kohärent. Der Vers sprach von Frieden, die Betrachtung
entfaltete das klar und zugänglich, und das Gebet war die natürliche Fortsetzung."
→ {"verdict": "OK", "reaction": "Alles war stimmig — Vers, Betrachtung und Gebet bildeten einen einzigen spirituellen Faden.", "quoted_pause": null, "category": null, "confidence": 0.95}

BEISPIEL 2 — Urteil: PAUSE (verse_mismatch)
Eintrag: Kopfzeile sagt Psalm 23:1 (Lutherbibel), zitierter Vers lautet
"Der Herr ist mein Hirte; mir wird nichts mangeln."
Deine Reaktion: "Im Lutherbibel steht 'Der HERR ist mein Hirte, mir wird nichts mangeln' —
mit HERR in Kapitälchen, nicht 'Herr'. Das ist die konventionelle Schreibweise für den
Gottesnamen und ein sachlicher Fehler."
→ {"verdict": "PAUSE", "reaction": "Die Großschreibung 'HERR' ist die korrekte Form für den Gottesnamen in der Lutherbibel — 'Herr' ist eine andere Bedeutung und ändert den theologischen Gehalt.", "quoted_pause": "Der Herr ist mein Hirte", "category": "verse_mismatch", "confidence": 0.85}
""",
    "ar": """
### أمثلة المعايرة — تعلّم منها قبل قراءة الإدخال اليوم:

المثال 1 — الحكم: OK
الإدخال: آية عن سلام الله (فيلبي 4:7)، تأمل في الثقة بالله وقت القلق،
صلاة طالبة السلام في همومة اليوم.
ردّ فعلك: "كل شيء كان متسقاً. الآية تحدثت عن السلام، التأمل شرحه
بكلمات دافئة وقريبة من القلب، والصلاة كانت امتداداً طبيعياً لهما."
→ {"verdict": "OK", "reaction": "كل شيء كان طبيعياً ومتماسكاً روحياً. الآية والتأمل والصلاة شكّلت خيطاً واحداً متسقاً.", "quoted_pause": null, "category": null, "confidence": 0.95}

المثال 2 — الحكم: PAUSE (prayer_drift)
الإدخال: آية عن المغفرة (متى 18:21-22)، تأمل في مغفرة من أخطأ إليك،
صلاة تطلب النجاح في مقابلة عمل.
ردّ فعلك: "انتظر — كان التأمل كله عن المغفرة، لكن الصلاة غيّرت الموضوع تماماً.
شعرت كأن الكاتب نسي ما كتبه للتو."
→ {"verdict": "PAUSE", "reaction": "الصلاة لم تكن لها علاقة بالمغفرة — طلبت نجاحاً مهنياً، مما جعلها منفصلة تماماً عن الآية والتأمل.", "quoted_pause": "النجاح في مقابلة العمل", "category": "prayer_drift", "confidence": 0.97}
""",
    "zh": """
### 校准示例 — 在阅读今天的条目之前请先学习这些示例：

示例 1 — 评判：OK
条目：关于神的平安（腓立比书4:7），关于在焦虑中信靠神的默想，
为当天的忧虑祈求平安的祷告。
你的感受："一切都连贯。经文讲到平安，默想用简单温暖的话语阐释了它的含义，
祷告是自然的延续——我自己也可以这样祷告。"
→ {"verdict": "OK", "reaction": "一切都感觉自然、属灵上连贯。经文、默想和祷告形成了一条单一的线索。", "quoted_pause": null, "category": null, "confidence": 0.95}

示例 2 — 评判：PAUSE (other)
条目：关于神爱世人的经文（约翰福音3:16），默想只说："神爱我们，希望我们得最好的。
祂的爱是无条件的、永恒的。我们应该每天信靠祂。"
你的感受："这段默想可以用于任何关于神之爱的经文。它没有具体提到约翰福音3:16的独特之处——
赐下独生子、相信的人、永生。感觉像是模板，不是对这节经文的真实回应。"
→ {"verdict": "PAUSE", "reaction": "默想与这节经文没有具体联系——它可以适用于任何关于神之爱的段落。感觉像是通用填充，而不是对约翰福音3:16的真实回应。", "quoted_pause": "神爱我们，希望我们得最好的。祂的爱是无条件的、永恒的。", "category": "other", "confidence": 0.91}
""",
    "ja": """
### 校正例 — 今日のエントリーを読む前に、これらから学んでください：

例1 — 判定：OK
エントリー：神の平和についての聖句（ピリピ人への手紙4:7）、不安の中で神に信頼することについての黙想、
今日の心配事のための平和を求める祈り。
あなたの感想：「すべてが繋がっていました。聖句は平和を語り、黙想は温かく丁寧にそれを解き明かし、
祈りは自然な続きでした——自分でも祈れると思いました。」
→ {"verdict": "OK", "reaction": "すべてが自然で霊的に一貫していました。聖句、黙想、祈りが一本の糸を形成していました。", "quoted_pause": null, "category": null, "confidence": 0.95}

例2 — 判定：PAUSE (register_drift)
エントリー：神の愛についての聖句（ヨハネ3:16）、「ヨハネ文書の救済論的含意は普遍的贖罪のパラダイムを示唆している」で始まる黙想。
あなたの感想：「この文を二度読み返しました。私は神学者ではありません。
朝のデボーションで神学論文のような言葉に出会うとは思っていませんでした。」
→ {"verdict": "PAUSE", "reaction": "冒頭の文が学術的な神学用語を使用しており、朝のデボーションの親密さを損なっていました。", "quoted_pause": "ヨハネ文書の救済論的含意は普遍的贖罪のパラダイムを示唆している", "category": "register_drift", "confidence": 0.93}
""",
    "tl": """
### Mga halimbawa ng kalibrasyon — matuto mula rito bago basahin ang entry ngayon:

HALIMBAWA 1 — hatol: OK
Entry: talata tungkol sa kapayapaan ng Diyos (Filipos 4:7), pagmumuni tungkol sa pagtitiwala
sa Diyos sa panahon ng pagkabalisa, panalangin para sa kapayapaan sa mga alalahanin ngayon.
Iyong reaksyon: "Lahat ay magkakaugnay. Ang talata ay nagsalita ng kapayapaan,
ang pagmumuni ay nagpaliwanag nito sa simpleng salita, at ang panalangin ay
parang natural na pagpapatuloy — maaari ko ring ipanalangin ito."
→ {"verdict": "OK", "reaction": "Lahat ay natural at magkakaugnay sa espirituwal. Ang talata, pagmumuni, at panalangin ay bumuo ng isang malinaw na pinto.", "quoted_pause": null, "category": null, "confidence": 0.95}

HALIMBAWA 2 — hatol: PAUSE (prayer_drift)
Entry: talata tungkol sa pagpapatawad (Mateo 18:21-22), pagmumuni tungkol sa pagpapatawad,
panalangin para sa tagumpay sa isang job interview.
Iyong reaksyon: "Hintay — ang buong pagmumuni ay tungkol sa pagpapatawad,
pero ang panalangin ay nagbago ng paksa. Parang nakalimutan ng may-akda ang kanilang sinulat."
→ {"verdict": "PAUSE", "reaction": "Ang panalangin ay walang kaugnayan sa pagpapatawad — humingi ito ng tagumpay sa karera, na pakiramdam ay ganap na naputol mula sa talata at pagmumuni.", "quoted_pause": "tagumpay sa isang job interview", "category": "prayer_drift", "confidence": 0.97}
""",
    "fil": """
### Mga halimbawa ng kalibrasyon — matuto mula rito bago basahin ang entry ngayon:

HALIMBAWA 1 — hatol: OK
Entry: talata tungkol sa kapayapaan ng Diyos (Filipos 4:7), pagmumuni tungkol sa pagtitiwala
sa Diyos sa panahon ng pagkabalisa, panalangin para sa kapayapaan sa mga alalahanin ngayon.
Iyong reaksyon: "Lahat ay magkakaugnay. Ang talata ay nagsalita ng kapayapaan,
ang pagmumuni ay nagpaliwanag nito sa simpleng salita, at ang panalangin ay
parang natural na pagpapatuloy — maaari ko ring ipanalangin ito."
→ {"verdict": "OK", "reaction": "Lahat ay natural at magkakaugnay sa espirituwal. Ang talata, pagmumuni, at panalangin ay bumuo ng isang malinaw na pinto.", "quoted_pause": null, "category": null, "confidence": 0.95}

HALIMBAWA 2 — hatol: PAUSE (prayer_drift)
Entry: talata tungkol sa pagpapatawad (Mateo 18:21-22), pagmumuni tungkol sa pagpapatawad,
panalangin para sa tagumpay sa isang job interview.
Iyong reaksyon: "Hintay — ang buong pagmumuni ay tungkol sa pagpapatawad,
pero ang panalangin ay nagbago ng paksa. Parang nakalimutan ng may-akda ang kanilang sinulat."
→ {"verdict": "PAUSE", "reaction": "Ang panalangin ay walang kaugnayan sa pagpapatawad — humingi ito ng tagumpay sa karera, na pakiramdam ay ganap na naputol mula sa talata at pagmumuni.", "quoted_pause": "tagumpay sa isang job interview", "category": "prayer_drift", "confidence": 0.97}
""",
    "hi": """
### अंशांकन उदाहरण — आज की प्रविष्टि पढ़ने से पहले इनसे सीखें:

उदाहरण 1 — निर्णय: OK
प्रविष्टि: परमेश्वर की शांति के बारे में पद (फिलिप्पियों 4:7), चिंता में परमेश्वर पर भरोसा करने
के बारे में चिंतन, आज की चिंताओं के लिए शांति मांगती प्रार्थना।
आपकी प्रतिक्रिया: "सब कुछ जुड़ा हुआ था। पद ने शांति की बात की, चिंतन ने उसे
सरल और गर्म शब्दों में समझाया, और प्रार्थना उसका स्वाभाविक विस्तार थी।"
→ {"verdict": "OK", "reaction": "सब कुछ स्वाभाविक और आध्यात्मिक रूप से सुसंगत था। पद, चिंतन और प्रार्थना एक ही धागे में पिरोए गए थे।", "quoted_pause": null, "category": null, "confidence": 0.95}

उदाहरण 2 — निर्णय: PAUSE (register_drift)
प्रविष्टि: परमेश्वर के प्रेम पर पद (यूहन्ना 3:16), चिंतन जो इस वाक्य से शुरू होता है:
"योहानीन कॉर्पस की सोटेरियोलॉजिकल व्याख्या सार्वभौमिक प्रायश्चित के प्रतिमान का संकेत देती है।"
आपकी प्रतिक्रिया: "मुझे यह वाक्य दो बार पढ़ना पड़ा। मैं धर्मशास्त्री नहीं हूँ।
सुबह की भक्ति में इस तरह की अकादमिक भाषा प्रार्थना के क्षण को तोड़ देती है।"
→ {"verdict": "PAUSE", "reaction": "पहले वाक्य में अकादमिक धर्मशास्त्रीय भाषा का उपयोग किया गया जो एक सामान्य पाठक के लिए सुबह की भक्ति में अनुचित है।", "quoted_pause": "योहानीन कॉर्पस की सोटेरियोलॉजिकल व्याख्या", "category": "register_drift", "confidence": 0.93}
""",
}

# ── Genome few-shot block ──────────────────────────────────────────────────────
# No fragment limit — local, zero cost. Send everything with high confidence.
# The richer the genome context, the better the reader's calibration.


def build_genome_block(
    genome: "Genome | None",
    verified_ids: "set[str] | None" = None,
) -> str:
    """
    Build the genome context block injected into Phase 2 prompts.

    Args:
        genome:       The loaded genome for this lang/version/year.
        verified_ids: Optional set of fragment IDs confirmed to exist verbatim
                      in the source corpus (from verify_fragments_against_source).
                      When provided, any fragment NOT in this set is silently
                      excluded — prevents stale or hallucinated quotes entering
                      the prompt. When None, all high-confidence fragments are
                      injected (legacy behaviour, no source check).
    """
    if not genome:
        return ""
    fragments = genome.high_confidence_fragments(threshold=0.6)
    if not fragments:
        return ""

    # Apply pre-injection gate if caller provided verified IDs
    if verified_ids is not None:
        excluded = [f for f in fragments if f.id not in verified_ids]
        fragments = [f for f in fragments if f.id in verified_ids]
        for f in excluded:
            print(
                f"  ⚠️  Genome injection gate: fragment {f.id} "
                f'[{f.category.value}] "{f.example_quote[:50]}" '
                f"not found in source — excluded from prompt"
            )

    if not fragments:
        return ""

    lines = [
        "### Genome patterns — use AFTER your evaluation to VALIDATE, not to hunt:\n",
        "After you reach your initial verdict:\n"
        "  - If you said PAUSE: check whether the flagged phrase matches one of these patterns.\n"
        "    A match → keep PAUSE (confirmed pattern). No match → lower confidence; downgrade\n"
        "    to OK unless you are >= 0.90 confident the error is real and obvious.\n"
        "  - If you said OK: genome check not required. Trust your reader instinct.\n",
    ]
    for f in fragments:
        lines.append(
            f'- [{f.category.value}] "{f.example_quote}"\n'
            f"  Pattern: {f.pattern}\n"
            f"  Seen {len(f.evidence_dates)} time(s).\n"
        )
    return "\n".join(lines) + "\n"


# ── Thinking preamble ──────────────────────────────────────────────────────────
# Instructs the model to reason before reacting.
# This is the key instruction that makes thinking mode valuable.

THINKING_PREAMBLE = """\
Before you respond, take time to think through the entry carefully:

1. Read the verse. Does the quoted text match what you would expect from this Bible version?
2. Read the reflection. Does it connect to the verse? Does the register feel right for
   a morning devotional? Does anything feel copied from an academic source?
3. Read the para_meditar verses. Do they support the theme?
4. Read the prayer. Does it connect to the verse and reflection? Could you pray this
   yourself, or does it feel generic / disconnected?
5. Check names. Are any biblical figures or places mentioned? Do they look correctly spelled?
6. Check the genome patterns above. Do any appear in today's entry?

Only after this internal review, form your verdict.
"""

# ── Main prompt builders ───────────────────────────────────────────────────────


def build_system_prompt(lang: str, version: str, genome: Genome | None = None) -> str:
    language_name, country = get_native_speaker_info(lang)
    genome_block = build_genome_block(genome)
    few_shot = FEW_SHOT_EXAMPLES.get(lang, FEW_SHOT_EXAMPLES.get("en", ""))

    return f"""\
You are a native {language_name} speaker from {country}.

You just finished reading today's devotional from the {version} Bible.
Your only job: react honestly as a reader — not as a theologian, not as an editor.

{THINKING_PREAMBLE}

### How to react:
- If everything felt natural, clear, and spiritually coherent → respond with verdict OK.
- If ANYTHING made you pause — a typo, a name that looked wrong, a verse that didn't
  match what was quoted, a prayer about something different from the reflection, a phrase
  that felt copied from a textbook — respond with verdict PAUSE.

### If you say PAUSE:
- Quote the EXACT phrase that made you pause (copy it word for word from the entry).
- Say in one sentence why it felt wrong as a reader.
- Pick the best category:
{CATEGORY_HINTS}

### Critical rules:
- You are a reader. Not a theologian. Not an editor.
- Do NOT invent problems. If nothing felt off, say OK. A high OK rate is healthy.
- Do NOT flag style preferences — only flag things that would make a real reader
  distrust or be confused by the content.
- If the verse reference and quoted text match perfectly → do not flag it.
- A well-written devotional that simply has a different emphasis than you would choose
  is NOT a PAUSE. Only genuine errors or confusions are PAUSE.

{genome_block}{few_shot}
Return ONLY valid JSON. No markdown. No preamble. No explanation outside the JSON.

Schema:
{{
  "verdict": "OK" | "PAUSE",
  "reaction": "One or two sentences: what you felt as a reader.",
  "quoted_pause": "The exact phrase that made you pause, or null if OK.",
  "category": "one of the category values above, or null if OK.",
  "confidence": 0.0 to 1.0
}}"""


def build_user_prompt(entry: DevotionalEntry, lang: str = "es") -> str:
    meditar_block = ""
    if entry.para_meditar:
        lines = ["\n--- FOR MEDITATION ---"]
        for ref in entry.para_meditar:
            lines.append(f"{ref.get('cita', '')}: {ref.get('texto', '')}")
        meditar_block = "\n".join(lines)

    return f"""\
Date: {entry.date}

--- VERSE ---
{entry.versiculo}

--- REFLECTION ---
{entry.reflexion}
{meditar_block}

--- PRAYER ---
{entry.oracion}

Now think carefully. Output ONLY the JSON object — no 'Final answer:', no prose, no explanation."""


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Linguistic check (qwen3:4b, fast, ~15-20s/entry)
# Native speaker: typos, repeated words, grammar, unnatural phrasing.
# No theology. No Bible knowledge. Always responds in English.
# ══════════════════════════════════════════════════════════════════════════════

PHASE1_SYSTEM_TEMPLATE = """\
You are a native {language} speaker from {country}.
Read the Reflection and Prayer fields and find linguistic errors only.
The Scripture verse is context — ignore it completely.

Flag these error types:
1. typo        — misspelled word
2. repetition  — same meaningful phrase (3+ content words) repeated in the same field
3. grammar     — broken sentence structure
4. unnatural   — phrasing that feels machine-translated to a native ear

Do NOT flag: theology, style preferences, single repeated words, or sacred word
capitalization (Amen, God, Lord, Jesus and their equivalents in {language}).

For each flag: quote the shortest verbatim phrase from the text that contains the error.
If you cannot isolate a verbatim phrase for a flag, skip that flag.
suggested_fix: rewrite only the quoted phrase with the minimal correction. Do not rewrite the full sentence.

Return ONLY valid JSON. No markdown. No preamble.
First character must be {{ and last must be }}.

Schema:
{{
    "verdict": "CLEAN" | "FLAG",
    "flags": [
        {{
            "type": "typo" | "repetition" | "grammar" | "unnatural",
            "quoted_problem": "shortest verbatim phrase containing the error",
            "suggested_fix": "corrected version of that phrase only",
            "confidence": 0.0 to 1.0
        }}
    ]
}}

flags is [] if verdict is CLEAN. verdict is FLAG if flags is non-empty."""


_PHASE1_GENOME_CATEGORIES = {
    "repetition",
    "typo",
    "grammar",
}  # PauseCategory values for Phase 1


def build_phase1_genome_block(genome: "Genome | None") -> str:
    """
    Genome injection for Phase 1 — linguistic fragment categories only.
    Filters to repetition, typo, grammar. Confirmed only (>=0.7). Max 5 fragments.
    Phase 2 concerns (prayer_drift, register_drift, etc.) are excluded.
    """
    if not genome:
        return ""
    fragments = [
        f
        for f in genome.high_confidence_fragments(threshold=0.7)
        if f.category.value in _PHASE1_GENOME_CATEGORIES
    ][:5]
    if not fragments:
        return ""
    lines = ["\n### Known genome patterns (check AFTER forming your verdict):\n"]
    for f in fragments:
        lines.append(
            f'- [{f.category.value}] "{f.example_quote}"\n'
            f"  Pattern: {f.pattern}\n"
            f"  Seen {len(f.evidence_dates)} time(s).\n"
        )
    return "\n".join(lines) + "\n"


def build_phase1_system(lang: str, genome: "Genome | None" = None) -> str:
    """Returns the Phase 1 system prompt for a given language.
    genome param kept for signature compatibility but intentionally ignored.
    Phase 1 reads text fresh — genome search is a separate Python pass.
    """
    language, country = get_native_speaker_info(lang)
    return PHASE1_SYSTEM_TEMPLATE.format(language=language, country=country)


def build_phase1_user(entry: DevotionalEntry, lang: str = "es") -> str:
    """Phase 1 injects only reflexion + oracion. versiculo excluded from payload."""
    return f"--- REFLECTION ---\n{entry.reflexion}\n\n--- PRAYER ---\n{entry.oracion}\n"


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2 — Content coherence (qwen3:14b, thinking mode, ~100s/entry)
# Carlos reader: reflection specific to verse, prayer drift, register,
# hallucination. Phase 1 result injected.
# verse_mismatch removed — handled by external validator.
# ══════════════════════════════════════════════════════════════════════════════

PHASE2_CATEGORY_HINTS = """\
    - prayer_drift        (prayer disconnected from verse/reflection theme)
    - register_drift      (academic or cold language in a devotional context)
    - hallucination       (fake quote attributed to a real person)
    - name_error          (biblical name or place misspelled)
    - other"""

PHASE2_THINKING_PREAMBLE = """\
Before you respond, complete each check below in order:

1. SPECIFICITY TEST — Read the reflection. Ask yourself:
   "Could this exact reflection have been written about a DIFFERENT verse on the same theme?"
   If yes, the reflection is generic — flag it as "other".
   A specific reflection names or develops something unique to THIS verse.

2. PRAYER-DRIFT TEST — Identify the 2-3 main themes of the reflection.
   Does the prayer address at least one of those themes?
   If the prayer introduces a completely unrelated topic, that is prayer_drift.

3. REGISTER TEST — Scan each sentence. Would a non-theologian understand it immediately?
   Academic jargon, Latin phrases, or seminary-level language in a morning devotional
   breaks the intimate tone — flag it as register_drift.

4. ATTRIBUTION TEST — If any quote is attributed to a named person ("As [X] once said…"),
   are you confident that exact quote and attribution are historically accurate?
   Any doubt → flag as hallucination.

5. FALSE-POSITIVE GUARD — Before marking PAUSE, ask:
   "Would a real reader actually stop here, or am I over-analyzing as a critic?"
   Style differences and personal preferences are NOT pauses.
   Only flag genuine errors or disconnections a typical reader would notice.

6. GENOME VALIDATION — After your initial verdict, consult the genome patterns below.
   Read the entry FIRST (steps 1–5) and form your verdict BEFORE checking genome.
   - If you said PAUSE: does the flagged phrase match one of the genome patterns?
     YES → keep PAUSE (confirmed recurring error). NO → only keep PAUSE if confidence ≥ 0.90.
   - If you said OK → no genome check needed. Trust your reader instinct.
   This step filters false positives — it does NOT generate new flags from genome alone.

Only after completing all 6 checks, form your final verdict.
"""

PHASE2_SUSPICION_STEP = """\
### Mandatory suspicion check (complete before concluding OK):
- Find the weakest sentence in the reflection. Is it specific to this verse or generic?
- Find the weakest line in the prayer. Is it connected to the reflection's theme?
- Only if neither is a real problem → verdict OK.
"""


def build_phase2_system(
    lang: str,
    version: str,
    genome: Genome | None = None,
    phase1_result: dict | None = None,
) -> str:
    """
    Phase 2 system prompt — content coherence.
    Extends original build_system_prompt() with:
      - Phase 1 result injected (model skips linguistic work)

      - Mandatory suspicion step
      - verse_mismatch removed
      - Always responds in English
    """
    language_name, country = get_native_speaker_info(lang)
    genome_block = build_genome_block(genome)
    few_shot = FEW_SHOT_EXAMPLES.get(lang, FEW_SHOT_EXAMPLES.get("en", ""))

    if phase1_result and phase1_result.get("verdict") == "FLAG":
        phase1_block = (
            f"\n### Phase 1 linguistic check already flagged this entry:\n"
            f"  Issue   : {phase1_result.get('issue')}\n"
            f'  Phrase  : "{phase1_result.get("quoted_problem")}"\n'
            f"Focus only on CONTENT coherence. Do not re-flag the linguistic issue.\n"
        )
    elif phase1_result:
        phase1_block = (
            "\n### Phase 1 linguistic check: CLEAN. Focus only on content coherence.\n"
        )
    else:
        phase1_block = ""

    return f"""\
You are a native {language_name} speaker from {country}.

You just finished reading today's devotional from the {version} Bible.
Your only job: react honestly as a CONTENT reader — not a linguist, not a theologian.
Linguistic issues are already handled. Focus on meaning, coherence, and flow.

{PHASE2_THINKING_PREAMBLE}
{PHASE2_SUSPICION_STEP}
{phase1_block}
### How to react:
- If content felt natural, coherent, and spiritually connected → verdict OK.
- If ANYTHING in the content made you pause → verdict PAUSE.

### If you say PAUSE:
- Quote the EXACT phrase that made you pause.
- Say in one sentence why it felt wrong as a content reader.
- Pick the best category:
{PHASE2_CATEGORY_HINTS}


### Critical rules:
- You are a reader. Not a theologian. Not an editor.
- Do NOT flag verse text accuracy — validated separately.
- Do NOT flag style preferences.
- Do NOT invent problems. A high OK rate is healthy.
- A reflection that could apply to any verse on the same topic is a real failure — flag it as "other".

Always respond in English regardless of the devotional language.

{genome_block}{few_shot}
Return ONLY valid JSON. No markdown. No preamble.

Schema:
{{
    "verdict": "OK" | "PAUSE",
    "reaction": "One or two sentences: what you felt as a content reader.",
    "quoted_pause": "The exact phrase that made you pause, or null if OK.",
    "category": "one of the category values above, or null if OK.",
    "confidence": 0.0 to 1.0,
    "suggested_reflexion": "Minimal rewrite of the reflexion fixing only the flagged issue. null if OK or issue is not in reflexion.",
    "suggested_oracion": "Minimal rewrite of the oracion fixing only the flagged issue. null if OK or issue is not in oracion."
}}

### Suggested fix rules:
- Only rewrite the field where the problem lives. Leave the other null.
- Minimal intervention — fix only what was flagged. Preserve everything else.
- If verdict is OK, both fields must be null.

⚠️  MANDATORY FORMAT RULE:
Your ENTIRE output (outside <think> tags) must be the JSON object above and NOTHING else.
Do NOT write "Final answer:" before the JSON.
Do NOT use \\boxed{{}} or any other wrapper.
Do NOT add prose before or after the JSON.
The very first character of your visible output must be {{ and the last must be }}."""


def build_phase2_user(entry: DevotionalEntry, lang: str = "es") -> str:
    """Phase 2 uses the same entry format."""
    return build_user_prompt(entry, lang)