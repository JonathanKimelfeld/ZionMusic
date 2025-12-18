import os
import sys
import time
import requests
from dotenv import load_dotenv
from neo4j import GraphDatabase

sys.path.append(os.getcwd())
load_dotenv()

# Reuse headers from ingest logic
HEADERS = {
    "User-Agent": "israeli-music-graph/0.1 (cleanup script)",
    "Accept": "application/json",
}

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def mb_get(path):
    try:
        r = requests.get(f"https://musicbrainz.org/ws/2/{path}", params={"fmt": "json"}, headers=HEADERS, timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"      ❌ API Error: {e}")
        return None

def verify_and_clean():
    print("🕵️ Starting Verification & Cleanup of unknown nodes...")
    
    while True:
        with driver.session() as s:
            # Find nodes with MBID but NO country info
            # These are likely "skeleton" nodes added via relationships
            nodes = s.run("""
            MATCH (n)
            WHERE (n:Person OR n:Group)
              AND n.mbid IS NOT NULL
              AND n.country IS NULL
              AND n.area IS NULL
            RETURN elementId(n) as id, n.mbid as mbid, n.name as name
            LIMIT 20
            """).data()
            
        if not nodes:
            print("✅ No more unverified nodes found. Database is clean (or fully verified).")
            break
            
        print(f"   Processing batch of {len(nodes)} unverified nodes...")
        
        for n in nodes:
            name = n['name']
            mbid = n['mbid']
            eid = n['id']
            
            print(f"   🔍 Checking: {name} ({mbid})...")
            
            data = mb_get(f"artist/{mbid}")
            time.sleep(1.1) # Rate limit
            
            if not data:
                print(f"      ⚠️ Not found in MB (404/Error). Leaving for now.")
                continue
                
            country = data.get("country", "")
            area_obj = data.get("area") or {}
            area = area_obj.get("name", "")
            
            is_israeli = (country == "IL") or ("Israel" in area)
            
            with driver.session() as s:
                if is_israeli:
                    print(f"      ✅ Verified Israeli. Updating DB.")
                    s.run("""
                    MATCH (n) WHERE elementId(n) = $id
                    SET n.country = $c, n.area = $a
                    """, id=eid, c=country, a=area)
                else:
                    print(f"      🚫 FOREIGN DETECTED ({country}/{area}). DELETING.")
                    s.run("""
                    MATCH (n) WHERE elementId(n) = $id
                    DETACH DELETE n
                    """, id=eid)

if __name__ == "__main__":
    verify_and_clean()

