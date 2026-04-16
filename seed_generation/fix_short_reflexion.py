"""Regenerate 4 short-reflexion devotional entries."""
import json, os, re, sys
import anthropic

LANG = 'tl'
VERSION = 'ASND'
SHORT_DATES = ['2026-10-16', '2026-11-14', '2027-03-18', '2027-07-17']

api_key = os.environ.get('ANTHROPIC_API_KEY', '')
if not api_key:
    # Try reading .env
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    with open(env_file) as f:
        for line in f:
            if line.startswith('ANTHROPIC_API_KEY='):
                api_key = line.split('=', 1)[1].strip().strip('"')
                os.environ['ANTHROPIC_API_KEY'] = api_key
                break

with open('2026/seeds/seed_tl_ASND_for_2026.json', encoding='utf-8') as f:
    seed = json.load(f)
with open('2026/yearly_devotionals/Devocional_year_2026_tl_ASND.json', encoding='utf-8') as f:
    merged = json.load(f)

client = anthropic.Anthropic(api_key=api_key)

def repair_json(raw_text):
    text = re.sub(r'```(?:json)?', '', raw_text).strip()
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

def build_id(date_key, seed_entry, version):
    cita = seed_entry['versiculo']['cita']
    return re.sub(r'\s+', '', cita).replace(':', '') + version + date_key.replace('-', '')

for date_key in SHORT_DATES:
    seed_entry = seed[date_key]
    cita = seed_entry['versiculo']['cita']
    print(f'Generating {date_key} ({cita})...', flush=True)
    prompt = (
        f'Write a Tagalog (tl) Christian devotional on "{cita}". '
        'Return ONLY a valid JSON object with exactly these two keys:\n'
        '"reflexion": Deep theological reflection, minimum 1200 characters, 5 full paragraphs in Tagalog.\n'
        '"oracion": Prayer with minimum 200 words in Tagalog, ending with exactly "Sa pangalan ni Jesus, amen."\n'
        'No other text. Return only the JSON object.'
    )
    saved = False
    for attempt in range(3):
        try:
            response = client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=4096,
                messages=[{'role': 'user', 'content': prompt}]
            )
            data = repair_json(response.content[0].text.strip())
            if not data:
                print(f'  attempt {attempt+1}: parse fail', flush=True)
                continue
            reflexion = data.get('reflexion', '').strip()
            oracion   = data.get('oracion', '').strip()
            if len(reflexion) < 800:
                print(f'  attempt {attempt+1}: reflexion too short ({len(reflexion)})', flush=True)
                continue
            devo = {
                'id':           build_id(date_key, seed_entry, VERSION),
                'date':         date_key,
                'language':     LANG,
                'version':      VERSION,
                'versiculo':    cita + ' ' + VERSION + ': "' + seed_entry['versiculo']['texto'] + '"',
                'reflexion':    reflexion,
                'para_meditar': seed_entry.get('para_meditar', []),
                'oracion':      oracion,
                'tags':         seed_entry.get('tags', ['devotional']),
            }
            merged['data']['tl'][date_key] = [devo]
            print(f'  OK reflexion={len(reflexion)} ends={repr(oracion[-50:])}', flush=True)
            saved = True
            break
        except Exception as ex:
            print(f'  error: {ex}', flush=True)
    if not saved:
        print(f'  FAILED {date_key}', flush=True)

merged['data']['tl'] = dict(sorted(merged['data']['tl'].items()))
with open('2026/yearly_devotionals/Devocional_year_2026_tl_ASND.json', 'w', encoding='utf-8') as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)
print(f'\nSaved. Total: {len(merged["data"]["tl"])}', flush=True)
