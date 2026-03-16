import requests
import json
import difflib
import re
import os

# --- 0. HELPER FUNCTIONS ---
def generate_clever_id(authors_str, year, title):
    first_author = authors_str.split(',')[0].strip() if authors_str else "Unknown"
    last_name = first_author.split(' ')[-1]
    last_name = re.sub(r'[^a-zA-Z0-9]', '', last_name).lower()
    if not last_name: 
        last_name = "unknown"

    year_str = str(year) if year else "nd"

    clean_title = re.sub(r'[^a-zA-Z0-9\s]', '', str(title)).strip().lower()
    words = clean_title.split()
    first_word = words[0] if words else "untitled"

    return f"{last_name}_{year_str}_{first_word}"

def is_similar(title1, title2, threshold=0.85):
    t1, t2 = str(title1).lower()[:60], str(title2).lower()[:60]
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

def get_full_abstract(title):
    """Fetches the full abstract from Semantic Scholar using the paper title."""
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        # Using the params dictionary handles all the messy URL-encoding for us!
        params = {"query": title, "limit": 1, "fields": "abstract"}
        response = requests.get(url, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("data") and len(data["data"]) > 0:
                full_abstract = data["data"][0].get("abstract")
                if full_abstract:
                    return full_abstract
    except Exception as e:
        print(f"  -> Could not fetch full abstract from Semantic Scholar: {e}")
        
    return None

# --- 1. CONFIGURATION ---
API_KEY = "8042247b46f853b9b9589d7980bee98c846ccf94826b978cc7fe24c28ef425f3"
AUTHOR_ID = "v55L2AoAAAAJ" # Replace with your target Author ID

conf_keywords = ["conference", "proceedings", "symposium", "workshop", "intl", "convention", "egu", "agu", "egusphere"]
processed_titles = {}

# --- 2. FETCH PROFILE METADATA ---
print(f"Fetching overall profile metadata for Author ID: {AUTHOR_ID}...")

start = 0
total_processed = 0
author_profile_data = {}

while True:
    profile_params = {
        "engine": "google_scholar_author",
        "author_id": AUTHOR_ID,
        "api_key": API_KEY,
        "start": start,
        "num": 100 
    }
    
    response = requests.get("https://serpapi.com/search", params=profile_params)
    data = response.json()
    
    if "error" in data:
        print(f"API Error: {data['error']}")
        break
    
    if start == 0 and "author" in data:
        author_info = data["author"]
        cited_by = data.get("cited_by", {}).get("table", [])
        
        metrics = {"citations": 0, "h_index": 0, "i10_index": 0}
        for item in cited_by:
            if "citations" in item: metrics["citations"] = item["citations"].get("all", 0)
            if "h_index" in item: metrics["h_index"] = item["h_index"].get("all", 0)
            if "i10_index" in item: metrics["i10_index"] = item["i10_index"].get("all", 0)

        author_profile_data = {
            "name": author_info.get("name", "Unknown"),
            "affiliations": author_info.get("affiliations", "None listed"),
            "website": author_info.get("website", ""),
            "metrics": metrics
        }
        
    articles = data.get("articles", [])
    if not articles:
        break

    for article in articles:
        try:
            citation_id = article.get("citation_id")
            print(f"Fetching deep metadata for: {article.get('title', 'Unknown')[:30]}...")
            
            citation_params = {
                "engine": "google_scholar_author",
                "view_op": "view_citation",
                "citation_id": citation_id,
                "api_key": API_KEY
            }
            
            cit_resp = requests.get("https://serpapi.com/search", params=citation_params)
            cit_data = cit_resp.json().get("citation", {})
            
            # Smart Link Extraction
            link = cit_data.get("link")
            if not link or "scholar.google.com" in link:
                resources = cit_data.get("resources", [])
                if resources:
                    link = resources[0].get("link")
            if not link:
                link = article.get("link", f"https://scholar.google.com/citations?user={AUTHOR_ID}")
            
            # Basic Info
            raw_title = cit_data.get("title", article.get("title", "Unknown Title"))
            raw_authors = cit_data.get("authors", article.get("authors", "Unknown Authors"))
            
            raw_date = cit_data.get("publication_date", article.get("year", "N/A"))
            year_match = re.search(r'\d{4}', str(raw_date))
            raw_year = year_match.group(0) if year_match else "N/A"
            
            raw_journal = cit_data.get("journal", cit_data.get("source", "Unknown Journal"))
            volume = cit_data.get("volume", "")
            issue = cit_data.get("issue", "")
            pages = cit_data.get("pages", "")
            publisher = cit_data.get("publisher", "")
            
            # --- THE SMART ABSTRACT FALLBACK ---
            raw_abstract = cit_data.get("description", "No abstract available.")
            if raw_abstract.endswith('…') or raw_abstract.endswith('\u2026') or raw_abstract.endswith('...'):
                print(f"  -> Abstract truncated. Fetching full text from Semantic Scholar...")
                full_abstract = get_full_abstract(raw_title)
                if full_abstract:
                    raw_abstract = full_abstract
                    print("  -> Success!")
            
            citation_stats = cit_data.get("total_citations", {})
            total_citations = citation_stats.get("cited_by", {}).get("total", 0)
            citation_history = citation_stats.get("table", [])
            
            clever_id = generate_clever_id(raw_authors, raw_year, raw_title)
            
            new_pub = {
                "id": clever_id,
                "title": raw_title,
                "authors": raw_authors,
                "journal": raw_journal,
                "publication_details": {
                    "full_date": raw_date,
                    "year": raw_year,
                    "volume": volume,
                    "issue": issue,
                    "pages": pages,
                    "publisher": publisher
                },
                "metrics": {
                    "total_citations": total_citations,
                    "citation_history": citation_history
                },
                "abstract": raw_abstract,
                "link": link
            }
            
            is_dup = False
            for ext_title in list(processed_titles.keys()):
                if is_similar(new_pub["title"], ext_title):
                    if processed_titles[ext_title]["journal"] == "Unknown Journal" and new_pub["journal"] != "Unknown Journal":
                        processed_titles[ext_title] = new_pub
                    is_dup = True
                    break
            
            if not is_dup:
                processed_titles[new_pub["title"]] = new_pub
                total_processed += 1

        except Exception as e:
            print(f"Error processing paper {citation_id}: {e}")
            
    if "serpapi_pagination" in data and "next" in data["serpapi_pagination"]:
        start += 100
    else:
        break

# --- 4. SORT AND SAVE ---
journal_articles = []
conferences = []

for pub_data in processed_titles.values():
    venue = pub_data["journal"].lower()
    if any(word in venue for word in conf_keywords):
        conferences.append(pub_data)
    else:
        journal_articles.append(pub_data)

try:
    os.makedirs("src", exist_ok=True)
    
    with open("src/my_publications.js", 'w', encoding='utf-8') as f:
        f.write("const authorProfile = " + json.dumps(author_profile_data, indent=4) + ";\n\n")
        f.write("const journalArticles = " + json.dumps(journal_articles, indent=4) + ";\n\n")
        f.write("const conferencePapers = " + json.dumps(conferences, indent=4) + ";\n")
        
    print(f"\nSuccess! Saved {len(journal_articles)} Journals and {len(conferences)} Conferences.")
    print(f"Total API credits used approximately: {total_processed + (start//100) + 1}")
except Exception as e:
    print(f"\nFailed to save file: {e}")