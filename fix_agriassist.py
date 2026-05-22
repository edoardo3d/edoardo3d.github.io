#!/usr/bin/env python3
"""
fix_agriassist.py — riorganizza la card AgriAssist nel portfolio.

Cosa fa:
1. Fa una copia di backup di index.html -> index.html.bak
2. Trova la card AgriAssist
3. Identifica dashboard desktop (immagine più larga) e telefoni (verticali)
4. Riorganizza: dashboard sopra full-width, telefoni affiancati sotto
5. Inietta CSS necessario nel <style> esistente o ne crea uno nuovo

Uso:
    python3 fix_agriassist.py
    python3 fix_agriassist.py path/to/index.html
"""

import sys
import re
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# CSS da iniettare
# ---------------------------------------------------------------------------
CSS_INJECTION = """
/* === AgriAssist mockup layout fix === */
.agri-mockups-fixed {
  display: flex !important;
  flex-direction: column !important;
  gap: 2rem !important;
  align-items: center !important;
  width: 100% !important;
}
.agri-mockups-fixed .agri-dashboard-wrap {
  width: 100%;
  max-width: 900px;
  display: flex;
  justify-content: center;
}
.agri-mockups-fixed .agri-dashboard-wrap img,
.agri-mockups-fixed .agri-dashboard-wrap iframe {
  width: 100%;
  height: auto;
  max-width: 900px;
  border-radius: 8px;
}
.agri-mockups-fixed .agri-phones-row {
  display: flex;
  flex-direction: row;
  gap: 1.5rem;
  justify-content: center;
  align-items: flex-start;
  flex-wrap: nowrap;
  width: 100%;
}
.agri-mockups-fixed .agri-phones-row img,
.agri-mockups-fixed .agri-phones-row iframe {
  flex: 0 1 280px;
  width: 280px;
  max-width: 280px;
  height: auto;
}
@media (max-width: 700px) {
  .agri-mockups-fixed .agri-phones-row {
    flex-wrap: wrap;
  }
  .agri-mockups-fixed .agri-phones-row img,
  .agri-mockups-fixed .agri-phones-row iframe {
    flex: 0 1 45%;
    width: 45%;
  }
}
/* === end AgriAssist fix === */
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_agriassist_section(html: str):
    """
    Trova il blocco HTML della card AgriAssist.
    Cerca pattern noti: id/class contenenti 'agri', oppure heading 'AgriAssist'.
    Ritorna (start_idx, end_idx, matched_pattern) o None.
    """
    patterns = [
        # Sezioni con id/class che menzionano agri/agriassist
        r'<(section|article|div)[^>]*(?:id|class)\s*=\s*["\'][^"\']*agri[^"\']*["\'][^>]*>',
        # Sezione che contiene un heading AgriAssist
        r'<(section|article|div)[^>]*>\s*(?:<[^>]+>\s*)*<h[1-6][^>]*>\s*AgriAssist',
    ]

    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE)
        if not m:
            continue
        start = m.start()
        tag = m.group(1).lower()
        # Trova il tag di chiusura bilanciato
        end = find_matching_close(html, start, tag)
        if end:
            return start, end, m.group(0)
    return None


def find_matching_close(html: str, start_idx: int, tag: str):
    """Trova l'indice di chiusura del tag bilanciato a partire da start_idx."""
    open_re = re.compile(rf'<{tag}\b', re.IGNORECASE)
    close_re = re.compile(rf'</{tag}\s*>', re.IGNORECASE)
    depth = 0
    i = start_idx
    while i < len(html):
        open_m = open_re.search(html, i)
        close_m = close_re.search(html, i)
        if not close_m:
            return None
        if open_m and open_m.start() < close_m.start():
            depth += 1
            i = open_m.end()
        else:
            depth -= 1
            i = close_m.end()
            if depth == 0:
                return i
    return None


def extract_images_and_iframes(section_html: str):
    """
    Estrae tutti gli <img> e <iframe> dalla sezione, con i loro attributi.
    Ritorna lista di dict: {tag, full, src/srcdoc snippet, width_hint}
    """
    items = []
    # Match <img ...> auto-closing or with closing tag
    for m in re.finditer(r'<img\b[^>]*/?>', section_html, re.IGNORECASE):
        items.append({
            'kind': 'img',
            'full': m.group(0),
            'start': m.start(),
            'end': m.end(),
        })
    for m in re.finditer(r'<iframe\b[^>]*>.*?</iframe>', section_html, re.IGNORECASE | re.DOTALL):
        items.append({
            'kind': 'iframe',
            'full': m.group(0),
            'start': m.start(),
            'end': m.end(),
        })
    items.sort(key=lambda x: x['start'])
    return items


def classify_mockup(tag_html: str):
    """
    Classifica un elemento come 'dashboard' o 'phone' basandosi su euristiche:
    - alt/title/class che menzionano dashboard, desktop, web -> dashboard
    - alt/title/class che menzionano phone, mobile, app, chat, home -> phone
    - aspect ratio (se width/height esplicite): largo -> dashboard, alto -> phone
    """
    lower = tag_html.lower()

    dashboard_keywords = ['dashboard', 'desktop', 'web', 'nkosi', 'admin', 'agriarchive']
    phone_keywords = ['phone', 'mobile', 'mockup', 'chat', 'onboarding', 'home',
                      'amina', 'agrichat', 'app-', 'smartphone']

    for kw in dashboard_keywords:
        if kw in lower:
            return 'dashboard'
    for kw in phone_keywords:
        if kw in lower:
            return 'phone'

    # Aspect ratio fallback via width/height attrs
    w = re.search(r'\bwidth\s*=\s*["\']?(\d+)', tag_html)
    h = re.search(r'\bheight\s*=\s*["\']?(\d+)', tag_html)
    if w and h:
        try:
            if int(w.group(1)) > int(h.group(1)) * 1.2:
                return 'dashboard'
            else:
                return 'phone'
        except ValueError:
            pass

    return 'unknown'


def inject_css(html: str) -> str:
    """Inietta il CSS prima del </style> esistente, o crea un nuovo <style> nel <head>."""
    if '/* === AgriAssist mockup layout fix ===' in html:
        print("  → CSS già presente, salto l'iniezione")
        return html

    # Cerca l'ultimo </style>
    style_close = list(re.finditer(r'</style>', html, re.IGNORECASE))
    if style_close:
        last = style_close[-1]
        return html[:last.start()] + CSS_INJECTION + "\n" + html[last.start():]

    # Nessuno <style>: aggiungi nel <head>
    head_close = re.search(r'</head>', html, re.IGNORECASE)
    if head_close:
        css_block = f"\n<style>\n{CSS_INJECTION}\n</style>\n"
        return html[:head_close.start()] + css_block + html[head_close.start():]

    # Nessun <head>: aggiungi in cima
    return f"<style>\n{CSS_INJECTION}\n</style>\n" + html


def restructure_section(section_html: str):
    """
    Rifattorizza la sezione: trova mockup, ne identifica uno come dashboard
    e gli altri come phone, e li avvolge nel nuovo wrapper.
    Ritorna (new_section_html, info_string) oppure (None, errore).
    """
    items = extract_images_and_iframes(section_html)
    if len(items) < 2:
        return None, f"trovati solo {len(items)} mockup (img/iframe) nella card — atteso almeno 2"

    classified = []
    for it in items:
        cls = classify_mockup(it['full'])
        classified.append((cls, it))

    dashboards = [x for x in classified if x[0] == 'dashboard']
    phones = [x for x in classified if x[0] == 'phone']
    unknowns = [x for x in classified if x[0] == 'unknown']

    # Se ho 0 dashboard ma ho 3+ mockup, l'ultimo "unknown" più largo è probabilmente la dashboard
    if not dashboards and len(items) >= 3:
        # Prendi il primo unknown come dashboard
        if unknowns:
            dashboards = [unknowns[0]]
            unknowns = unknowns[1:]

    # Se ho 2 mockup e 0 dashboard, AgriAssist potrebbe avere solo telefoni
    # In quel caso non serve riorganizzare verticalmente
    if not dashboards:
        return None, ("non sono riuscito a identificare la dashboard tra i mockup. "
                      f"Trovati {len(phones)} phone, {len(unknowns)} unknown. "
                      "Aggiungi alt='dashboard' all'immagine desktop e rilancia.")

    # Tutti gli unknown rimasti li tratto come phone
    phones.extend(unknowns)

    if len(phones) < 1:
        return None, "non ho trovato almeno un telefono"

    dashboard_html = dashboards[0][1]['full']
    phone_htmls = [p[1]['full'] for p in phones]

    # Costruisci il nuovo blocco
    new_block = '<div class="agri-mockups-fixed">\n'
    new_block += '  <div class="agri-dashboard-wrap">\n'
    new_block += f'    {dashboard_html}\n'
    new_block += '  </div>\n'
    new_block += '  <div class="agri-phones-row">\n'
    for ph in phone_htmls:
        new_block += f'    {ph}\n'
    new_block += '  </div>\n'
    new_block += '</div>\n'

    # Rimuovi tutti gli img/iframe originali dalla sezione e sostituiscili con new_block
    # Strategia: cancella ogni occorrenza esatta (in ordine inverso per non sballare gli indici)
    new_section = section_html
    sorted_items = sorted(items, key=lambda x: x['start'], reverse=True)
    for it in sorted_items:
        new_section = new_section[:it['start']] + new_section[it['end']:]

    # Inserisci il new_block prima del primo tag di chiusura della sezione
    # cioè prima dell'ultimo </div> o </section> o </article>
    insert_match = list(re.finditer(r'</(section|article|div)\s*>', new_section, re.IGNORECASE))
    if insert_match:
        last = insert_match[-1]
        new_section = new_section[:last.start()] + new_block + new_section[last.start():]
    else:
        new_section += new_block

    info = (f"dashboard: 1, telefoni: {len(phones)}. "
            f"Classificazione: {[(c, 'img' if it['kind']=='img' else 'iframe') for c, it in classified]}")
    return new_section, info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("index.html")

    if not path.exists():
        print(f"❌ File non trovato: {path.resolve()}")
        print("   Lancia lo script dalla cartella che contiene index.html,")
        print("   oppure passa il percorso: python3 fix_agriassist.py /percorso/index.html")
        sys.exit(1)

    print(f"📄 Leggo {path.resolve()}")
    html = path.read_text(encoding='utf-8')
    print(f"   {len(html):,} caratteri")

    # Backup
    backup = path.with_suffix(path.suffix + '.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"💾 Backup salvato in {backup.name}")
    else:
        print(f"💾 Backup già esistente ({backup.name}), non sovrascrivo")

    # Trova la card AgriAssist
    found = find_agriassist_section(html)
    if not found:
        print("❌ Non ho trovato la card AgriAssist nell'HTML.")
        print("   Cerco sezioni con id/class che contengono 'agri' o un heading <h*>AgriAssist</h*>.")
        print("   Se la card si chiama diversamente, aprilo e dimmi com'è strutturata.")
        sys.exit(2)

    start, end, matched = found
    section_html = html[start:end]
    print(f"✅ Card AgriAssist trovata ({end - start:,} caratteri)")
    print(f"   Match iniziale: {matched[:120]}...")

    # Rifattorizza
    new_section, info = restructure_section(section_html)
    if new_section is None:
        print(f"❌ Errore nella rifattorizzazione: {info}")
        sys.exit(3)
    print(f"🔧 Rifattorizzazione: {info}")

    # Sostituisci e inietta CSS
    new_html = html[:start] + new_section + html[end:]
    new_html = inject_css(new_html)

    # Scrivi
    path.write_text(new_html, encoding='utf-8')
    print(f"✅ Salvato {path.name}")
    print()
    print("Apri il file nel browser per vedere il risultato.")
    print(f"Se qualcosa non va, ripristina con: cp {backup.name} {path.name}")


if __name__ == '__main__':
    main()
