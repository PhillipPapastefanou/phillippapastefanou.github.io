import difflib
import json
import re
import os
import time
from collections import Counter

import requests

EMAIL = "pp.papastefanou@gmail.com"
PUBLICATIONS_FILE = "src/my_publications_v2.js"
GEOGRAPHY_OUTPUT_FILE = "src/citation_geography.json"
AUTHOR_OPENALEX_ID = "A5030770438"  # Phillip Papastefanou, ORCID 0000-0002-4613-2565
TITLE_MATCH_THRESHOLD = 0.8
# Copernicus discussion-journal DOIs sometimes resolve to a referee/author comment
# thread rather than the actual paper (e.g. "Comment on: <real title>"). Reject those.
DISCUSSION_PREFIXES = (
    "comment on",
    "reply to",
    "response to",
    "interactive comment on",
    "author comment on",
    "author's response to",
    "referee comment on",
)


def is_discussion_thread_title(title):
    normalized = normalize_title(title)
    return any(normalized.startswith(prefix) for prefix in DISCUSSION_PREFIXES)


def load_publications(filepath):
    """Loads id/title/doi for every journal article and conference paper."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    pubs = []
    for varname in ("journalArticles", "conferencePapers"):
        m = re.search(rf"const {varname}\s*=\s*(\[.*?\n\]);", content, re.DOTALL)
        if not m:
            continue
        for pub in json.loads(m.group(1)):
            pubs.append({"id": pub["id"], "title": pub["title"], "doi": pub.get("doi")})
    return pubs


def normalize_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def title_similarity(a, b):
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def work_to_result(result):
    return {
        "id": result["id"],
        "cited_by_count": result.get("cited_by_count", 0),
        "counts_by_year": result.get("counts_by_year", []),
    }


def get_openalex_work_by_doi(doi, title):
    """Looks up a work directly by DOI, validating the returned title still matches
    (some stored DOIs point to the wrong record, e.g. a discussion comment)."""
    clean_doi = doi.replace("https://doi.org/", "").strip()
    url = f"https://api.openalex.org/works/doi:{clean_doi}"
    try:
        response = requests.get(url, params={"mailto": EMAIL}, timeout=15)
        if response.status_code != 200:
            return None
        result = response.json()
        if not result.get("title"):
            return None
        if is_discussion_thread_title(result["title"]):
            return None
        if title_similarity(title, result["title"]) < TITLE_MATCH_THRESHOLD:
            return None
        return work_to_result(result)
    except Exception as e:
        print(f"  Error looking up DOI {doi}: {e}")
        return None


def get_openalex_work_by_title(title):
    """Searches OpenAlex by title and picks the best fuzzy match among the top results."""
    url = "https://api.openalex.org/works"
    params = {
        "search": title,
        "per-page": 5,
        "mailto": EMAIL,
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        results = data.get("results")
        if not results:
            return None
        best, best_score = None, 0.0
        for result in results:
            if not result.get("title") or is_discussion_thread_title(result["title"]):
                continue
            score = title_similarity(title, result["title"])
            if score > best_score:
                best, best_score = result, score
        if best is None or best_score < TITLE_MATCH_THRESHOLD:
            return None
        return work_to_result(best)
    except Exception as e:
        print(f"  Error searching OpenAlex for '{title[:40]}...': {e}")
        return None


def get_openalex_work(pub):
    """Tries the stored DOI first (fast, precise), falling back to fuzzy title search."""
    if pub.get("doi"):
        work = get_openalex_work_by_doi(pub["doi"], pub["title"])
        if work:
            return work
    return get_openalex_work_by_title(pub["title"])


def analyze_citing_countries(openalex_id):
    """Fetches citing papers for a given OpenAlex work ID and extracts first-author countries."""
    url = "https://api.openalex.org/works"
    params = {
        "filter": f"cites:{openalex_id}",
        "select": "id,title,authorships",
        "per-page": 200,
        "mailto": EMAIL,
    }
    countries = []
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        for work in data.get("results", []):
            authors = work.get("authorships", [])
            if authors:
                institutions = authors[0].get("institutions", [])
                if institutions:
                    country_code = institutions[0].get("country_code")
                    if country_code:
                        countries.append(country_code)
    except Exception as e:
        print(f"  Error fetching citing works for {openalex_id}: {e}")
    return countries


def get_author_summary_stats(author_id):
    """Fetches aggregate citation/h-index/i10-index stats for the author from OpenAlex."""
    url = f"https://api.openalex.org/authors/{author_id}"
    response = requests.get(url, params={"mailto": EMAIL}, timeout=15)
    data = response.json()
    stats = data["summary_stats"]
    return {
        "citations": data["cited_by_count"],
        "h_index": stats["h_index"],
        "i10_index": stats["i10_index"],
    }


def js_escape(value):
    return json.dumps(value, ensure_ascii=False)


def update_publication_metrics(content, pub_id, occurrence_index, total_citations, citation_history):
    """Replaces the metrics block (total_citations + citation_history) following the
    occurrence_index-th (0-based) appearance of "id": "pub_id" in the file."""
    id_pattern = re.compile(r'"id":\s*"' + re.escape(pub_id) + r'"')
    matches = list(id_pattern.finditer(content))
    if occurrence_index >= len(matches):
        raise ValueError(f"Not enough occurrences of id {pub_id}: found {len(matches)}")
    start = matches[occurrence_index].end()

    tc_pattern = re.compile(r'("total_citations":\s*)(\d+)')
    m = tc_pattern.search(content, start)
    if not m:
        raise ValueError(f"Could not find total_citations after id {pub_id}")
    content = content[: m.start(2)] + str(total_citations) + content[m.end(2) :]

    ch_pattern = re.compile(r'("citation_history":\s*)(\[.*?\])', re.DOTALL)
    m = ch_pattern.search(content, start)
    if not m:
        raise ValueError(f"Could not find citation_history after id {pub_id}")
    history_json = json.dumps(citation_history, ensure_ascii=False)
    content = content[: m.start(2)] + history_json + content[m.end(2) :]

    return content


def update_author_metrics(content, stats):
    block_pattern = re.compile(
        r'("metrics":\s*\{\s*"citations":\s*)(\d+)(,\s*"h_index":\s*)(\d+)(,\s*"i10_index":\s*)(\d+)'
    )
    m = block_pattern.search(content)
    if not m:
        raise ValueError("Could not find authorProfile.metrics block")
    replacement = (
        m.group(1)
        + str(stats["citations"])
        + m.group(3)
        + str(stats["h_index"])
        + m.group(5)
        + str(stats["i10_index"])
    )
    return content[: m.start()] + replacement + content[m.end() :]


if __name__ == "__main__":
    print("Loading publications...")
    pubs = load_publications(PUBLICATIONS_FILE)
    print(f"Found {len(pubs)} publications to analyze.\n")

    with open(PUBLICATIONS_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    all_countries = []
    seen_counts = {}
    updated, skipped = [], []

    for pub in pubs:
        title = pub["title"]
        pid = pub["id"]
        print(f"Processing: {title[:50]}...")

        work = get_openalex_work(pub)
        if not work:
            print("  -> Could not find paper in OpenAlex.")
            skipped.append(pid)
            time.sleep(1)
            continue

        print(f"  -> {work['id']}: {work['cited_by_count']} citations")

        countries = analyze_citing_countries(work["id"])
        all_countries.extend(countries)

        idx = seen_counts.get(pid, 0)
        try:
            content = update_publication_metrics(
                content, pid, idx, work["cited_by_count"], work["counts_by_year"]
            )
            updated.append(pid)
        except ValueError as e:
            print(f"  -> Skipped update: {e}")
            skipped.append(pid)
        seen_counts[pid] = idx + 1

        time.sleep(1)

    print("\nFetching author-level summary stats from OpenAlex...")
    author_stats = get_author_summary_stats(AUTHOR_OPENALEX_ID)
    print(f"  -> citations={author_stats['citations']}, h_index={author_stats['h_index']}, i10_index={author_stats['i10_index']}")
    content = update_author_metrics(content, author_stats)

    with open(PUBLICATIONS_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\nUpdated {len(updated)}/{len(pubs)} publications. Skipped: {skipped}")

    # Citation geography (kept for potential future use, e.g. a map view)
    final_counts_dict = dict(Counter(all_countries).most_common())
    os.makedirs(os.path.dirname(GEOGRAPHY_OUTPUT_FILE), exist_ok=True)
    with open(GEOGRAPHY_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_counts_dict, f, indent=4)
    print(f"Citation geography saved to {GEOGRAPHY_OUTPUT_FILE}")
