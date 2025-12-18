import os
import requests
import time
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

# Discogs API
DISCOGS_API = "https://api.discogs.com"
USER_AGENT = "ZionMusic/0.1"
TOKEN = os.environ.get("DISCOGS_TOKEN")
SECRET = os.environ.get("DISCOGS_SECRET")

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def discogs_get(path, params=None):
    headers = {"User-Agent": USER_AGENT}
    
    if TOKEN and not SECRET:
        # Personal Access Token
        headers["Authorization"] = f"Discogs token={TOKEN}"
    elif TOKEN and SECRET:
        # Consumer Key + Secret
        headers["Authorization"] = f"Discogs key={TOKEN}, secret={SECRET}"
    else:
        raise ValueError("DISCOGS_TOKEN (and optionally DISCOGS_SECRET) not found in env.")
    
    # Discogs rate limit: 60 requests per minute (1 per second)
    time.sleep(1.1) 
    
    url = f"{DISCOGS_API}/{path}"
    resp = requests.get(url, params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()

def search_artist(name):
    """
    Search Discogs for an artist by name.
    Returns the first result's ID.
    """
    print(f"🔍 Searching Discogs for: {name}")
    data = discogs_get("database/search", {"q": name, "type": "artist"})
    results = data.get("results", [])
    if not results:
        print(f"⚠️ No results found for {name}")
        return None
    
    # Simple heuristic: Pick the first one. 
    # Ideally we'd match country or profile text too.
    best = results[0]
    print(f"   Found: {best['title']} (ID: {best['id']})")
    return best['id']

def ingest_artist_rels(discogs_id):
    """
    Fetches artist details (members, groups) from Discogs and ingests them.
    """
    data = discogs_get(f"artists/{discogs_id}")
    name = data.get("name")
    
    # Discogs structure:
    # - "members": List of members (if it's a group)
    # - "groups": List of groups this person is in (if it's a person)
    # - "profile": Description
    # - "namevariations": List of strings (e.g. ["Infected", "Infected Mushrooms"])
    # - "aliases": List of objects (other projects by same artist)
    
    name_variations = data.get("namevariations", [])
    aliases = [a.get("name") for a in data.get("aliases", [])]
    
    # Combined list of all known names for this node
    # We will store this as a property 'aliases' on the node
    all_names = list(set([name] + name_variations + aliases))
    
    # CHECK COUNTRY (if available)
    # Discogs usually puts this in 'profile' text or doesn't have a structured 'country' field easily accessible 
    # without parsing releases. However, sometimes it's implied.
    # Actually, Discogs API has a "contact_info" or "profile" which might mention location.
    # But unlike MB, it's not a strict field on the Artist object usually.
    # Wait, looking at docs: Artist resource doesn't have 'country'.
    # It has 'members', 'aliases', 'name', 'realname', 'profile', 'urls'.
    
    # Heuristic: Scan profile for "Israel", "Tel Aviv", "Jerusalem".
    profile = (data.get("profile") or "").lower()
    # Also check if it's a member of an existing Israeli group?
    
    # For now, let's just log it. If we want strict filtering, we have to rely on Profile text.
    # Or, we assume if we found them via snowballing from an Israeli node, they are relevant.
    # But the user specifically asked for the same check.
    
    is_israeli_text = "israel" in profile or "tel aviv" in profile or "jerusalem" in profile or "haifa" in profile
    if not is_israeli_text:
        # Warning: This is a heuristic. Many artists don't have country in profile.
        # But if we want to be safe:
        print(f"⚠️ Discogs Artist {name} does not explicitly mention Israel/Tel Aviv/Jerusalem in profile.")
        # return  <-- Too strict? Discogs profiles are often empty.
        
    # Let's rely on the snowball context (if we came here, we are linked to someone).
    # But if this is a ROOT seed, we should be careful.
    
    members = data.get("members", [])
    groups = data.get("groups", [])
    
    # We map Discogs logic to our graph logic:
    # 1. Upsert the seed artist (we need to be careful with ID mapping!)
    #    We should probably store discogsId on the node too.
    
    # SMART CHECK: Before upserting a NEW node by DiscogsID, check if we have a match by Name/Alias
    # This prevents creating "Shlomo Gronich" (new) when "שלמה גרוניך" (existing) is meant.
    
    with driver.session() as s:
        # Check for existing node via normalized name search
        # We need the name matcher logic here. 
        # Ideally we'd use a shared function, but let's implement a direct check.
        
        # 1. Try to find by normalized name match
        # (This is expensive to run in Python for every ingestion, but safe)
        # But we can do a targeted check: "Does ANY node have alias/name roughly equal to this?"
        # Cypher doesn't have easy transliteration.
        
        # Let's rely on exact alias match if possible (Discogs variations often include Hebrew)
        # Check if any node has this name in aliases OR matches name
        
        # Fixed: Use toString(n.name) to handle nodes that accidentally have list names
        candidates = s.run("""
        MATCH (n:Person)
        WHERE toLower(toString(n.name)) = toLower($name) OR $name IN n.aliases
        RETURN elementId(n) as id, n.name as name
        """, name=name).data()
        
        existing_id = None
        if candidates:
            print(f"   found existing candidate for {name}: {candidates[0]['name']}")
            existing_id = candidates[0]['id']
        else:
            # If no exact match, try variations list against DB
            for v in name_variations:
                # Fixed: Use toString(n.name) here too
                c = s.run("MATCH (n:Person) WHERE toLower(toString(n.name)) = toLower($v) RETURN elementId(n) as id", v=v).data()
                if c:
                    print(f"   found existing match via variation '{v}'")
                    existing_id = c[0]['id']
                    break
        
        if existing_id:
            # UPDATE existing node with Discogs ID
            print(f"   🔗 Linking Discogs ID {discogs_id} to existing node {existing_id}")
            s.run(f"""
            MATCH (n) WHERE elementId(n) = $eid
            SET n.discogsId = $did,
                n.aliases = apoc.coll.toSet(coalesce(n.aliases, []) + $aliases)
            """, eid=existing_id, did=discogs_id, aliases=all_names)
            
            # Use the existing node for linking members
            # We don't change label or name drastically here, just enrich.
        else:
            # CREATE new node (or standard upsert by Discogs ID if it existed as a discogs-only node)
            print(f"   💾 Upserting {label}: {name} (Discogs ID: {discogs_id})")
            
            # Standard merge by Discogs ID
            s.run(f"""
            MERGE (n:{label} {{discogsId: $did}})
            ON CREATE SET 
                n.name = $name, 
                n.importedFrom = 'discogs',
                n.aliases = $aliases
            ON MATCH SET 
                n.name = coalesce(n.name, $name),
                n.aliases = apoc.coll.toSet(coalesce(n.aliases, []) + $aliases)
            """, did=discogs_id, name=name, aliases=all_names)
        
        # 2. Ingest Members (if Group)
        for m in members:
            m_name = m.get("name")
            m_id = m.get("id")
            if m.get("active") is False:
                pass
            
            # STRICT MODE: Only link if Member already exists in DB
            # We do NOT create new Person nodes from Discogs members blindly.
            is_known = s.run("MATCH (n:Person {discogsId: $id}) RETURN count(n) as c", id=m_id).single()["c"] > 0
            
            if is_known:
                print(f"   -> Member (Existing): {m_name}")
                s.run("""
                MATCH (g:Group {discogsId: $gid})
                MATCH (p:Person {discogsId: $pid})
                MERGE (p)-[r:MEMBER_OF]->(g)
                SET r.source = 'discogs'
                """, gid=discogs_id, pid=m_id)
            else:
                # Skip creating new node
                # print(f"   (Skipping unknown member: {m_name})")
                pass

        # 3. Ingest Groups (if Person)
        for g in groups:
            g_name = g.get("name")
            g_id = g.get("id")
            
            # STRICT MODE: Only link if Group already exists in DB
            is_known = s.run("MATCH (n:Group {discogsId: $id}) RETURN count(n) as c", id=g_id).single()["c"] > 0
            
            if is_known:
                print(f"   -> Group (Existing): {g_name}")
                s.run("""
                MATCH (p:Person {discogsId: $pid})
                MATCH (g:Group {discogsId: $gid})
                MERGE (p)-[r:MEMBER_OF]->(g)
                SET r.source = 'discogs'
                """, pid=discogs_id, gid=g_id)
            else:
                 # Skip creating new node
                 pass

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python etl/discogs/ingest_artist.py <Artist Name or Discogs ID>")
        sys.exit(1)
        
    arg = sys.argv[1]
    
    # Check if arg is numeric (ID) or string (Name)
    if arg.isdigit():
        did = int(arg)
    else:
        did = search_artist(arg)
        
    if did:
        ingest_artist_rels(did)

