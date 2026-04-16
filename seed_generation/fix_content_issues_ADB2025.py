"""Regenerate problematic entries in Devocional_year_2025_tl_ADB.json.

Fixes:
- dup_words_reflexion / dup_words_oracion (4 dates)
- reflexion too short                     (3 dates)
"""
import json, os, re
import anthropic

LANG    = 'tl'
VERSION = 'ADB'
SEED_FILE   = '2025/seeds/seed_tl_ADB_for_2025.json'
OUTPUT_FILE = '2025/yearly_devotionals/Devocional_year_2025_tl_ADB.json'

PROBLEM_DATES = [
    '2025-08-07',   # dup_words_reflexion: "pag-asa, pag-asa,"
    '2025-11-20',   # dup_words_oracion:   "ating ating"
    '2025-11-25',   # dup_words_oracion:   "araw araw"
    '2025-11-26',   # reflexion too short: 583 chars
    '2025-11-27',   # reflexion too short: 686 chars
    '2026-02-21',   # dup_words_reflexion: "laban laban"
    '2026-04-15',   # reflexion too short: 615 chars
]

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
api_key = os.environ.get('ANTHROPIC_API_KEY', '')
if not api_key:
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    with open(env_file) as f:
        for line in f:
            if line.startswith('ANTHROPIC_API_KEY='):
                api_key = line.split('=', 1)[1].strip().strip('"')
                os.environ['ANTHROPIC_API_KEY'] = api_key
                break
if not api_key:
    raise RuntimeError('ANTHROPIC_API_KEY not found')

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
with open(SEED_FILE, encoding='utf-8') as f:
    seed = json.load(f)
with open(OUTPUT_FILE, encoding='utf-8') as f:
    merged = json.load(f)

client = anthropic.Anthropic(api_key=api_key)


def repair_json(raw_text: str):
    text = re.sub(r'```(?:json)?', '', raw_text).strip('` \n')
    for cand in [text]:
        for fix in [cand, re.sub(r',(\s*[}\]])', r'\1', cand)]:
            try:
                return json.loads(fix)
            except Exception:
                pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        cand = m.group()
        for fix in [cand, re.sub(r',(\s*[}\]])', r'\1', cand)]:
            try:
                return json.loads(fix)
            except Exception:
                pass
    return None


def build_id(date_key: str, seed_entry: dict) -> str:
    cita = seed_entry['versiculo']['cita']
    return re.sub(r'\s+', '', cita).replace(':', '') + VERSION + date_key.replace('-', '')


def generate_entry(date_key: str, seed_entry: dict) -> dict | None:
    cita   = seed_entry['versiculo']['cita']
    texto  = seed_entry['versiculo']['texto']
    prompt = (
        f'Sumulat ng isang Tagalog na Kristiyanong debosyonal batay sa "{cita}" '
        f'mula sa Ang Dating Biblia (ADB): "{texto}"\n\n'
        'Ibalik LAMANG ang isang wastong JSON object na may eksaktong dalawang susi:\n'
        '"reflexion": Malalim na teolohikal na pagninilay, hindi bababa sa 1200 na karakter, '
        '5 buong talata sa Tagalog. HUWAG mag-ulit ng magkaparehong salita nang dalawang beses '
        'nang magkakasunod (hal. "ating ating", "pag-asa, pag-asa", "araw araw").\n'
        '"oracion": Panalangin na hindi bababa sa 200 salita sa Tagalog, nagtatapos ng eksakto '
        'sa "Sa pangalan ni Hesus, amen." HUWAG mag-ulit ng magkaparehong salita nang dalawang '
        'beses nang magkakasunod.\n'
        'Walang ibang teksto. Ibalik lamang ang JSON object.'
    )
    for attempt in range(3):
        try:
            response = client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=4096,
                messages=[{'role': 'user', 'content': prompt}],
            )
            data = repair_json(response.content[0].text.strip())
            if not data:
                print(f'  attempt {attempt+1}: JSON parse failed', flush=True)
                continue
            reflexion = data.get('reflexion', '').strip()
            oracion   = data.get('oracion', '').strip()
            if len(reflexion) < 800:
                print(f'  attempt {attempt+1}: reflexion too short ({len(reflexion)})', flush=True)
                continue
            if len(oracion) < 150:
                print(f'  attempt {attempt+1}: oracion too short ({len(oracion)})', flush=True)
                continue
            entry = {
                'id':           build_id(date_key, seed_entry),
                'date':         date_key,
                'language':     LANG,
                'version':      VERSION,
                'versiculo':    f'{cita} {VERSION}: "{texto}"',
                'reflexion':    reflexion,
                'para_meditar': seed_entry.get('para_meditar', []),
                'oracion':      oracion,
                'tags':         seed_entry.get('tags', ['devotional']),
            }
            return entry
        except Exception as ex:
            print(f'  attempt {attempt+1}: error: {ex}', flush=True)
    return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
failed = []
for date_key in PROBLEM_DATES:
    if date_key not in seed:
        print(f'SKIP {date_key}: not in seed', flush=True)
        continue
    seed_entry = seed[date_key]
    cita = seed_entry['versiculo']['cita']
    print(f'Generating {date_key} ({cita})...', flush=True)
    entry = generate_entry(date_key, seed_entry)
    if entry:
        merged['data'][LANG][date_key] = [entry]
        print(f'  OK reflexion={len(entry["reflexion"])} oracion_end={repr(entry["oracion"][-60:])}', flush=True)
    else:
        print(f'  FAILED {date_key}', flush=True)
        failed.append(date_key)

# Save sorted
merged['data'][LANG] = dict(sorted(merged['data'][LANG].items()))
with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)
total = len(merged['data'][LANG])
print(f'\nSaved → {OUTPUT_FILE}  (total: {total})', flush=True)
if failed:
    print(f'Still FAILED: {failed}', flush=True)
else:
    print('All regenerated successfully!', flush=True)
