from scholarly import scholarly, ProxyGenerator
import json
import difflib
import time
import random
import re 

# --- 0. HELPER FUNCTION FOR CLEVER IDs ---
def generate_clever_id(authors_str, year, title):
    first_author = authors_str.split(' and ')[0].split(',')[0].strip()
    last_name = first_author.split(' ')[-1] 
    last_name = re.sub(r'[^a-zA-Z0-9]', '', last_name).lower()
    if not last_name:
        last_name = "unknown"

    year_str = str(year) if year != 'N/A' else "nd"

    clean_title = re.sub(r'[^a-zA-Z0-9\s]', '', title).strip().lower()
    words = clean_title.split()
    first_word = words[0] if words else "untitled"

    return f"{last_name}_{year_str}_{first_word}"

print("Setting up ScraperAPI proxy... this should be much faster and more reliable!")
pg = ProxyGenerator()
# REMINDER: Rotate the original key you shared, as it is now compromised.

#success = pg.ScraperAPI("3825590c81fedd4b12bc74045923ce8e")
#success = pg.FreeProxies(None)
success = pg.FreeProxies()

if success:
    scholarly.use_proxy(pg)
    print("Proxy setup complete! Connected to ScraperAPI.")
else:
    print("Failed to connect to ScraperAPI. Check your key or internet connection.")
    exit() # Stop the script if the API key fails

# --- 2. CONFIGURATION ---
author_id = "v55L2AoAAAAJ"
conf_keywords = ["conference", "proceedings", "symposium", "workshop", "intl", "convention", "egu", "agu", "egusphere"]
processed_titles = {}

def is_similar(title1, title2, threshold=0.85):
    t1, t2 = title1.lower()[:60], title2.lower()[:60]
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

# --- 3. FETCH DATA ---
try:
    print(f"Fetching profile for Author ID: {author_id}...")
    author = scholarly.search_author_id(author_id)
    author = scholarly.fill(author)
    total_pubs = len(author['publications'])
    print(f"Found {total_pubs} publications. Starting extraction...\n")

    for i, pub in enumerate(author['publications']):
        try:
            # We still keep a small delay so we don't overwhelm the free ScraperAPI tier
            wait = random.uniform(1.5, 3.0)
            time.sleep(wait)
            
            pub_filled = scholarly.fill(pub)
            bib = pub_filled['bib']
            
            raw_title = bib.get('title', 'Unknown Title')
            raw_authors = bib.get('author', 'Unknown Authors')
            raw_year = bib.get('pub_year', 'N/A')
            
            # Extract the abstract (defaults to a fallback message if empty)
            raw_abstract = bib.get('abstract', 'No abstract available.')
            
            # --- APPLY THE CLEVER ID HERE ---
            clever_id = generate_clever_id(raw_authors, raw_year, raw_title)
            
            new_pub = {
                "id": clever_id,
                "title": raw_title,
                "authors": raw_authors,
                "journal": bib.get('venue', bib.get('journal', 'Unknown Journal')),
                "year": raw_year,
                "abstract": raw_abstract, # Added abstract field
                "link": pub_filled.get('pub_url', f"https://scholar.google.com/citations?user={author_id}")
            }
            
            # Deduplication
            is_dup = False
            for ext_title in list(processed_titles.keys()):
                if is_similar(new_pub["title"], ext_title):
                    if processed_titles[ext_title]["journal"] == "Unknown Journal" and new_pub["journal"] != "Unknown Journal":
                        processed_titles[ext_title] = new_pub
                    is_dup = True
                    break
            
            if not is_dup:
                processed_titles[new_pub["title"]] = new_pub
                
            print(f"[{i+1}/{total_pubs}] Processed ({clever_id}): {new_pub['title'][:30]}...")

        except Exception as e:
            print(f"[{i+1}/{total_pubs}] Error on paper: {e}")

    # --- 4. SORT AND SAVE ---
    journal_articles = []
    conferences = []

    for pub_data in processed_titles.values():
        venue = pub_data["journal"].lower()
        if any(word in venue for word in conf_keywords):
            conferences.append(pub_data)
        else:
            journal_articles.append(pub_data)

    # Make sure this path exists on your computer!
    with open("src/my_publications.js", 'w', encoding='utf-8') as f:
        f.write("const journalArticles = " + json.dumps(journal_articles, indent=4) + ";\n\n")
        f.write("const conferencePapers = " + json.dumps(conferences, indent=4) + ";")

    print(f"\nSuccess! Filtered down to {len(journal_articles)} Journals and {len(conferences)} Conferences.")

except Exception as e:
    print(f"\nFatal error establishing connection: {e}")