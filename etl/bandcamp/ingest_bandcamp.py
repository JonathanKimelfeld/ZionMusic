import os
import re
import requests
import time
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_soup(url):
    print(f"🌐 Fetching {url}...")
    time.sleep(1.0) # Politeness delay
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, 'html.parser')

def search_bandcamp(name):
    """
    Search Bandcamp for an artist URL.
    Returns the first matching URL (e.g. "https://infectedmushroom.bandcamp.com")
    """
    # Bandcamp doesn't have a clean search API, so we use their search page
    search_url = f"https://bandcamp.com/search?q={requests.utils.quote(name)}&item_type=b" # b=band
    soup = get_soup(search_url)
    
    results = soup.select(".result-info .heading a")
    if not results:
        print(f"⚠️ No Bandcamp results found for {name}")
        return None
        
    # Get the URL of the first result
    # Often looks like: https://artistname.bandcamp.com?from=search...
    raw_url = results[0]['href']
    clean_url = raw_url.split('?')[0]
    print(f"   Found Bandcamp URL: {clean_url}")
    return clean_url

def parse_credits(text):
    """
    Extracts names and roles from credit text.
    Patterns: 
    - "Produced by X"
    - "Bass: Y"
    - "Z - Guitar"
    """
    # Simple regex heuristic
    # Look for lines like "Role: Name" or "Name: Role"
    # This is hard because Bandcamp credits are free text.
    
    # Let's look for known roles
    roles = ["producer", "produced", "mixed", "mastered", "bass", "guitar", "drums", "vocals", "artwork", "lyrics", "composed"]
    
    found = []
    
    for line in text.split('\n'):
        line = line.strip()
        if not line: continue
        
        lower = line.lower()
        for role in roles:
            if role in lower:
                # Naive extraction: usually "Role by Name" or "Role: Name"
                # Remove the role keyword and punctuation
                # This needs refinement for production use.
                pass
                
    # Ideally we'd use an NLP entity extractor here. 
    # For now, let's just return the raw text block if we find keywords, 
    # or skip this advanced feature until we have a better parser.
    return []

def ingest_bandcamp_artist(url):
    """
    Scrapes a Bandcamp artist page.
    1. Checks location (must be Israel).
    2. Upserts artist.
    3. Scrapes albums for credits (finding other nodes).
    """
    soup = get_soup(url)
    
    # 1. Location Check
    location_tag = soup.select_one(".band-name-location .location")
    location = location_tag.text.strip() if location_tag else ""
    
    print(f"   📍 Location: {location}")
    if "Israel" not in location and "Tel Aviv" not in location and "Jerusalem" not in location:
        print(f"⚠️ Skipping non-Israeli Bandcamp artist: {url} ({location})")
        return

    # 2. Artist Details
    name_tag = soup.select_one("#band-name-location .title") or soup.select_one("#name-section h1 a") # fallback
    name = name_tag.text.strip() if name_tag else "Unknown"
    
    # Upsert Artist Node
    with driver.session() as s:
        # We use the URL as the unique ID for Bandcamp nodes
        s.run("""
        MERGE (n:Group {bandcampUrl: $url})
        ON CREATE SET n.name = $name, n.importedFrom = 'bandcamp', n.country = 'IL'
        ON MATCH SET n.name = coalesce(n.name, $name)
        """, url=url, name=name)
        
        print(f"💾 Upserted {name} (Bandcamp)")

    # 3. Find Albums -> Credits
    # Bandcamp artist pages list albums. We need to visit them to get credits.
    # Grid of albums: .music-grid-item a
    
    albums = soup.select(".music-grid-item a")
    # Limit to latest 3 albums to save time/requests?
    for album in albums[:3]:
        href = album['href']
        if not href.startswith("http"):
            href = url.rstrip("/") + href
            
        ingest_album_credits(href, parent_url=url)

def ingest_album_credits(album_url, parent_url):
    soup = get_soup(album_url)
    title = soup.select_one(".trackTitle").text.strip() if soup.select_one(".trackTitle") else "Unknown Album"
    
    print(f"   💿 Scanning album: {title}")
    
    # Credits are usually in .tralbum-credits
    credits_div = soup.select_one(".tralbum-credits")
    if not credits_div:
        return
        
    text = credits_div.text
    
    # We want to find names. 
    # Example: "Bass by Yossi Sassi"
    # Regex approach:
    # (Role) (by|:) (Name)
    
    patterns = [
        r"(?i)(produced|mixed|mastered|artwork|lyrics|music|composed)\s+by\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        r"(?i)(vocals|bass|guitar|drums|keyboards|synth|percussion)[:\-\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"
    ]
    
    links = []
    for pat in patterns:
        matches = re.findall(pat, text)
        for role, name in matches:
            links.append((name.strip(), role.upper().replace(" ", "_")))
            
    # Also look for LINKS to other artists (e.g. "feat. X" linking to X's bandcamp)
    # .tralbum-credits a
    for link in credits_div.select("a"):
        href = link['href']
        name = link.text.strip()
        if "bandcamp.com" in href:
            links.append((name, "COLLABORATED_WITH", href))
    
    # Ingest found connections
    with driver.session() as s:
        for item in links:
            # item is either (name, role) or (name, role, url)
            p_name = item[0]
            role = item[1]
            p_url = item[2] if len(item) > 2 else None
            
            print(f"      🔗 Found connection: {p_name} ({role})")
            
            # Upsert Person (we don't know if it's a person or group, assume person for credits)
            # If we have a URL, use it as ID. If not, use Name (weak ID).
            # Using Name as ID is risky for "David Cohen", but for Bandcamp scraping it's often all we have.
            # Ideally we merge later.
            
            if p_url:
                s.run("""
                MATCH (g:Group {bandcampUrl: $gurl})
                MERGE (p:Person {bandcampUrl: $purl})
                ON CREATE SET p.name = $pname, p.importedFrom = 'bandcamp'
                MERGE (p)-[r:MEMBER_OF]->(g)
                SET r.role = $role, r.source = 'bandcamp_credits'
                """, gurl=parent_url, purl=p_url, pname=p_name, role=role)
            else:
                # Weak link by name
                s.run("""
                MATCH (g:Group {bandcampUrl: $gurl})
                MERGE (p:Person {name: $pname})
                ON CREATE SET p.importedFrom = 'bandcamp_text'
                MERGE (p)-[r:MEMBER_OF]->(g)
                SET r.role = $role, r.source = 'bandcamp_credits'
                """, gurl=parent_url, pname=p_name, role=role)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python etl/bandcamp/ingest_bandcamp.py <Artist Name or URL>")
        sys.exit(1)
        
    arg = sys.argv[1]
    
    if "bandcamp.com" in arg:
        url = arg
    else:
        url = search_bandcamp(arg)
        
    if url:
        ingest_bandcamp_artist(url)

