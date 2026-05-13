"""Skill trigger regex patterns and framework definitions."""
import re

IMG_TRIGGERS = re.compile(
    r"\b(generier\w*|mal\w*|zeichn\w*|illustrier\w*|erstell\w*|erzeug\w*|"
    r"generate|draw|paint|illustrate|create|render|"
    r"bild\w*|foto\w*|image|picture|photo\w*|wallpaper|artwork|illustration|"
    r"zeichnung|gemälde\w*|portr[äa]\w*|szene\w*|"
    r"fotorealistisch\w*|photorealistic|cinematic|"
    r"[öo]lgem[äa]lde\w*|aquarell\w*|watercolor|"
    r"hochdetailliert|highly.detailed|hyperrealistic|"
    r"digital.art|concept.art|3d.render)\b",
    re.IGNORECASE,
)

VIDEO_TRIGGERS = re.compile(
    r"\b(video|videos|animier\w*|animate|clip|kurzfilm|film\w*|bewegt\w*|motion|"
    r"dreh\w*|render.*video|video.*render|erzeug.*video|video.*erzeug)\b",
    re.IGNORECASE,
)

IMAGE_EDIT_TRIGGERS = re.compile(
    # explicit edit verbs (DE)
    r"\b(bearbeit|änder|editier|modifizier|verände|verwandl|transformier|konvertier|anpass|korrigier)\w*\b|"
    # common short DE verbs — NUR mit Bild-Kontext (bild/image/foto muss im Text sein)
    r"\b(bild|image|foto|photo|picture)\b.{0,60}\b(mach|mache|färb|farb|setz|setze|wechsel|tausch|entfern|füg|passe)\w*\b|"
    r"\b(mach|mache|färb|farb|setz|setze|wechsel|tausch|entfern|füg|passe)\w*\b.{0,60}\b(bild|image|foto|photo|picture)\b|"
    # compound DE words containing edit intent
    r"\b(bildbearbeitung|bildkorrektur|farbkorrektur|retusche|retouch)\w*\b|"
    # explicit edit verbs (EN)
    r"\b(edit|modify|change|transform|convert|adjust|recolor|recolour|replace|remove|add|make|turn|swap|set)\b|"
    # image + verb combinations
    r"\b(bild|image|photo|picture)\b.{0,30}\b(edit|modify|change|transform)\b|"
    r"\b(edit|modify|change|transform)\b.{0,30}\b(bild|image|photo|picture)\b|"
    # body parts / visual elements (strong edit signal when image is present)
    r"\b(augen|auge|eyes?|eye|haare?|haar|hair|haut|skin|lippen|lips?|gesicht|face|"
    r"hintergrund|background|kleidung|clothes?|shirt|jacke|jacket|himmel|sky|"
    r"bart|beard|brille|glasses?|mund|mouth)\b|"
    # color instructions
    r"\b(farbe|colour|color|rot|red|blau|blue|grün|green|grau|grey|gray|gelb|yellow|"
    r"schwarz|black|weiß|white|braun|brown|lila|purple|pink|orange|türkis|teal|golden?)\b.*"
    r"\b(augen|eyes?|haar|hair|haut|skin|hintergrund|background|lippen|lips?|gesicht|face)\b|"
    r"\b(augen|eyes?|haar|hair|haut|skin|hintergrund|background|lippen|lips?|gesicht|face)\b.*"
    r"\b(farbe|colour|color|rot|red|blau|blue|grün|green|grau|grey|gray|gelb|yellow|"
    r"schwarz|black|weiß|white|braun|brown|lila|purple|pink|orange|türkis|teal|golden?)\b|"
    r"@.*edit\b|"
    r"\b(ersetz|replace)\w*\b",
    re.IGNORECASE,
)

IMAGE_UPSCALE_TRIGGERS = re.compile(
    # Explizite Upscale-Verben (de/en)
    r"\b(upscale|upscaling|upscaled|hochskalier\w*|hochauflös\w*|"
    r"vergröße?r\w*|gröber|skaliere?\s+hoch|"
    r"upsample\w*|enlarge\w*|enhance\s+resolution)\b|"
    # Faktor-Hinweise direkt nach „bild/image" oder mit Faktor-Schlüsselwort
    r"\b(bild|image|foto|photo)\b.{0,30}\b([234])\s*[xX]\b|"
    r"\b([234])\s*[xX]\b.{0,30}\b(bild|image|foto|photo)\b|"
    r"\bfaktor\s*([234])\b|"
    r"\b(zweifach|dreifach|vierfach|2-fach|3-fach|4-fach)\b",
    re.IGNORECASE,
)


PROMPT_OPTIMIZE_TRIGGERS = re.compile(
    r"\b(optimize|improve|refine|enhance|rewrite|restructure|upgrade)\b.{0,50}\b(prompt|instruction|system prompt|soul|query|text)\b|"
    r"\b(prompt|instruction|soul|text|query)\b.{0,50}\b(optimize|improve|refine|enhance|rewrite|better|fix)\b|"
    r"\b(optimiere|verbessere|verfeinere|schreibe um|überarbeite)\b.{0,50}\b(prompt|anweisung|text)\b|"
    r"\b(prompt|anweisung|text)\b.{0,50}\b(optimieren|verbessern|verfeinern)\b|"
    r"\bprompt.{0,20}(RTF|TAG|BAB|CARE|RISE)\b|"
    r"\b(RTF|TAG|BAB|CARE|RISE).{0,20}(framework|prompt)\b|"
    r"\b(optimize this|improve this|refine this|make this (better|clearer|sharper))\b|"
    r"\b(erstelle|erzeug|generiere|schreibe)\b.{0,30}\b(optimiert\w*|verbessert\w*|bess\w*)\b.{0,30}\b(prompt|anweisung)\b|"
    r"\b(prompt|anweisung)\b.{0,30}\b(erstellen|erzeugen|generieren|schreiben)\b|"
    r"\b(erstelle|generate|create)\b.{0,30}\b(prompt|optimierte)\b",
    re.IGNORECASE,
)

PROMPT_FRAMEWORKS = {
    "RTF": {
        "name": "Role-Task-Format",
        "steps": ["Role", "Task", "Format"],
        "best_for": "Creative, marketing, structured outputs",
    },
    "TAG": {
        "name": "Task-Action-Goal",
        "steps": ["Task", "Action", "Goal"],
        "best_for": "Management, KPIs, performance analysis",
    },
    "BAB": {
        "name": "Before-After-Bridge",
        "steps": ["Before", "After", "Bridge"],
        "best_for": "SEO, persuasion, transformation, change",
    },
    "CARE": {
        "name": "Context-Action-Result-Example",
        "steps": ["Context", "Action", "Result", "Example"],
        "best_for": "Storytelling, strategy, new products",
    },
    "RISE": {
        "name": "Role-Input-Steps-Expectation",
        "steps": ["Role", "Input", "Steps", "Expectation"],
        "best_for": "Complex strategy, roadmaps, knowledge work",
    },
}
