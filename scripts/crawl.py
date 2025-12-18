import os
import time
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

# Import our existing ETL logic
# We need to add the project root to sys.path to import from sibling directories if running as script
sys.path.append(os.getcwd())

from etl.musicbrainz.ingest_one import main as ingest_person
from etl.musicbrainz.ingest_band import main as ingest_band
from etl.discogs.ingest_artist import ingest_artist_rels as ingest_discogs, search_artist as search_discogs

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def get_unexpanded_nodes(session, limit=10):
    """
    Finds nodes that exist in our graph but haven't been 'crawled' yet.
    We'll use a property 'crawled' on the node to track this.
    """
    # Strategy:
    # 1. Find a Person who hasn't been crawled.
    #    Crawling a Person means: "Run ingest_one" to find all their OTHER bands.
    # 2. Find a Group that hasn't been crawled.
    #    Crawling a Group means: "Run ingest_band" to find all their OTHER members.
    
    cypher = """
    MATCH (n)
    WHERE (n:Person OR n:Group) 
      AND n.crawled IS NULL 
      AND (n.mbid IS NOT NULL OR n.discogsId IS NOT NULL)
    RETURN elementId(n) as id, n.mbid AS mbid, n.discogsId AS discogsId, n.name AS name, labels(n) AS labels
    LIMIT $limit
    """
    return session.run(cypher, limit=limit).data()

def mark_crawled(session, element_id):
    session.run("""
    MATCH (n) WHERE elementId(n) = $eid
    SET n.crawled = true, n.lastCrawled = datetime()
    """, eid=element_id)

def retry_with_backoff(func, *args, max_retries=3, delay=5):
    """
    Retries a function if it raises a connection error.
    """
    import time
    for i in range(max_retries):
        try:
            return func(*args)
        except Exception as e:
            if "Connection" in str(e) or "503" in str(e):
                if i < max_retries - 1:
                    wait = delay * (2 ** i)
                    print(f"      ⚠️ Connection error. Retrying in {wait}s... ({i+1}/{max_retries})")
                    time.sleep(wait)
                    continue
            raise e

def main():
    print("🕷️ Starting ZionMusic Crawler...")
    
    # Run loop
    while True:
        with driver.session() as s:
            nodes = get_unexpanded_nodes(s, limit=1)
        
        if not nodes:
            print("✅ Frontier empty. No more known nodes to expand.")
            print("   (To restart, ingest a NEW seed artist manually, or clear 'crawled' flags)")
            break
            
        target = nodes[0]
        mbid = target['mbid']
        discogs_id = target['discogsId']
        name = target['name']
        labels = target['labels']
        is_group = "Group" in labels
        is_person = "Person" in labels
        eid = target['id']
        
        print(f"\n🔍 Processing {labels[0]} '{name}' (MBID: {mbid}, Discogs: {discogs_id})...")
        
        try:
            # 1. Try MusicBrainz first (Primary Source)
            if mbid:
                if is_person:
                    print(f"   👤 Ingesting Person logic (finding other bands)...")
                    retry_with_backoff(ingest_person, mbid)
                elif is_group:
                    print(f"   🎸 Ingesting Band logic (finding other members)...")
                    retry_with_backoff(ingest_band, mbid)
            
            # 2. Try Discogs (Secondary/Fallback Source)
            # If we have a Discogs ID, use it.
            # DISABLED for now to reduce noise/duplicates until strictly needed
            # if discogs_id:
            #    print(f"   📀 Ingesting Discogs logic (ID: {discogs_id})...")
            #    retry_with_backoff(ingest_discogs, discogs_id)
                
            # ENRICHMENT: If MBID is missing but we have a Name, try to find MBID via MusicBrainz Search
            if not mbid and name:
                print(f"   🔎 MBID missing for '{name}'. Attempting to find it via MusicBrainz Search...")
                from etl.musicbrainz.ingest_one import mb_get, upsert_artist
                
                try:
                    search_res = retry_with_backoff(mb_get, "artist", {"query": f'artist:"{name}"', "fmt": "json"})
                    candidates = search_res.get("artists", [])
                    print(f"      found {len(candidates)} candidates. Top 3: {[c['name'] for c in candidates[:3]]}")
                    
                    best = None
                    for c in candidates:
                        # Prioritize Israeli matches if possible
                        c_country = c.get("country", "")
                        c_area = c.get("area", {}).get("name", "")
                        is_israeli = c_country == "IL" or "Israel" in c_area
                        
                        # Check name match OR alias match OR sort-name match
                        c_name = c.get("name", "")
                        c_sort = c.get("sort-name", "")
                        c_aliases = [a.get("name") for a in c.get("aliases", [])]
                        
                        # Match if name equals target OR target is in aliases OR matches sort-name
                        # (Case insensitive)
                        match_name = (c_name.lower() == name.lower())
                        match_sort = (c_sort.lower() == name.lower())
                        match_alias = any(al.lower() == name.lower() for al in c_aliases)
                        
                        if is_israeli and (match_name or match_sort or match_alias):
                            best = c
                            break
                    
                    # Fallback: exact name match + high score (even if country missing)
                    if not best and candidates:
                        first = candidates[0]
                        c_aliases = [a.get("name") for a in first.get("aliases", [])]
                        
                        match_alias = any(al.lower() == name.lower() for al in c_aliases)
                        match_sort = (first.get("sort-name", "").lower() == name.lower())
                        
                        if first.get("score", "0") == "100" and (first.get("name").lower() == name.lower() or match_sort or match_alias):
                            best = first
                            
                    if best:
                        found_mbid = best['id']
                        print(f"   ✅ Found MBID: {found_mbid} ({best['name']})")
                        
                        # Check for Duplicate / Merge Scenario
                        with driver.session() as s:
                            existing = s.run("MATCH (n) WHERE n.mbid = $mbid AND elementId(n) <> $eid RETURN elementId(n) as id", mbid=found_mbid, eid=eid).single()
                            
                            if existing:
                                existing_id = existing['id']
                                print(f"   🔄 Merging duplicate node ({eid}) into existing MB node ({existing_id})...")
                                # Merge target into existing MB node
                                # target is the second node in list, so it gets merged INTO the first
                                s.run("""
                                MATCH (mbNode), (target)
                                WHERE elementId(mbNode) = $existing_id AND elementId(target) = $eid
                                CALL apoc.refactor.mergeNodes([mbNode, target], {properties:'combine', mergeRels:true})
                                YIELD node RETURN count(node)
                                """, eid=eid, existing_id=existing_id)
                                
                                # After merge, we should crawl the SURVIVING node (existing_id)
                                # and we don't need to mark 'eid' crawled because it's gone.
                                # But to keep loop clean, we can mark existing_id as crawled after ingestion.
                                eid = existing_id # Update reference for subsequent calls
                            else:
                                # No duplicate, just update the current node
                                s.run("MATCH (n) WHERE elementId(n)=$eid SET n.mbid = $new_mbid", eid=eid, new_mbid=found_mbid)
                        
                        # Now ingest (using the valid MBID and potentially updated EID)
                        if is_person:
                            retry_with_backoff(ingest_person, found_mbid)
                        elif is_group:
                            retry_with_backoff(ingest_band, found_mbid)
                    else:
                        print(f"   ⚠️ Could not find confident MBID match for '{name}'")
                        
                except Exception as e:
                    print(f"   ❌ Search failed: {e}")
            
            # Mark as done so we don't loop forever on the same node
            with driver.session() as s:
                mark_crawled(s, eid)
                
            # Respect API limits
            print("   💤 Sleeping 1.5s...")
            time.sleep(1.5)
            
        except Exception as e:
            print(f"   ❌ Error crawling {name}: {e}")
            with driver.session() as s:
                 s.run("MATCH (n) WHERE elementId(n) = $eid SET n.crawled = true, n.crawlError = $err", eid=eid, err=str(e))
            time.sleep(2.0)

if __name__ == "__main__":
    main()
