import os, uuid, requests, time, urllib3
from dotenv import load_dotenv
from neo4j import GraphDatabase

# =============================================================================
# INGESTION SCRIPT
# This script ingests raw Band data from MusicBrainz.
#
# RULE: This script creates MEMBER_OF relationships.
#       It MUST NOT create CO_MEMBER relationships (derived layer).
# =============================================================================

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

MB = "https://musicbrainz.org/ws/2"
HEADERS = {
    "User-Agent": "israeli-music-graph/0.1 (local dev)",
    "Accept": "application/json",
}

# Check for MusicBrainz authentication token (optional, for higher rate limits)
MB_TOKEN = os.environ.get("MUSICBRAINZ_TOKEN")
if MB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {MB_TOKEN}"

def mb_get(path, params, retry_count=0, context="general"):
    """Get data from MusicBrainz API with rate limiting and retry logic."""
    try:
        # Rate limiting: standard for band processing
        rate_limit = 0.15 if MB_TOKEN else 1.1  # 10 req/sec vs 1 req/sec
        if hasattr(mb_get, '_last_call'):
            elapsed = time.time() - mb_get._last_call
            if elapsed < rate_limit:
                time.sleep(rate_limit - elapsed)
        mb_get._last_call = time.time()

        r = requests.get(f"{MB}/{path}", params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, urllib3.exceptions.ProtocolError) as e:
        if retry_count < 3:
            print(f"      ⚠️ Connection error, retrying in {2 ** retry_count}s... ({retry_count + 1}/3)")
            time.sleep(2 ** retry_count)  # Exponential backoff
            return mb_get(path, params, retry_count + 1)
        else:
            print(f"      ❌ Failed after 3 retries: {e}")
            raise e

def upsert_person(session, mbid: str, name: str):
    session.run("""
    MERGE (p:Person {mbid:$mbid})
    ON CREATE SET p.personId=$pid, p.name=$name
    ON MATCH  SET p.name=coalesce(p.name, $name)
    """, mbid=mbid, pid=str(uuid.uuid4()), name=name)

def upsert_group(session, mbid: str, name: str, country: str = None, area: str = None):
    session.run("""
    MERGE (g:Group {mbid:$mbid})
    ON CREATE SET g.groupId=$gid, g.name=$name, g.country=$country, g.area=$area
    ON MATCH  SET g.name=coalesce(g.name, $name), g.country=coalesce(g.country, $country), g.area=coalesce(g.area, $area)
    """, mbid=mbid, gid=str(uuid.uuid4()), name=name, country=country, area=area)

def merge_membership(session, person_mbid: str, group_mbid: str, begin, end, role, rel_type="MEMBER_OF", source_tag="musicbrainz"):
    session.run(f"""
    MATCH (p:Person {{mbid:$pm}}), (g:Group {{mbid:$gm}})
    MERGE (p)-[r:{rel_type}]->(g)
    SET r.startDate = $begin,
        r.endDate   = $end,
        r.role      = $role,
        r.certainty = 0.7,
        r.source    = $source
    """, pm=person_mbid, gm=group_mbid, begin=begin, end=end, role=role, source=source_tag)

def main(group_mbid: str):
    # For bands, members are also represented as relationships on the band's /artist page
    data = mb_get(f"artist/{group_mbid}", {"inc": "artist-rels", "fmt": "json"})
    group_name = data.get("name") or group_mbid

    # Check for Israeli nationality/area
    area = data.get("area") or {}
    country = data.get("country", "")
    area_name = area.get("name", "")

    # Build full begin_area string like the webpage shows
    begin_area = data.get("begin-area")
    begin_area_full = ""
    if begin_area:
        begin_area_name = begin_area.get("name", "")
        begin_area_id = begin_area.get("id")

        if begin_area_id:
            try:
                # Collect area hierarchy
                areas = []
                visited = set()

                def collect_area_hierarchy(area_id, depth=0):
                    if area_id in visited or depth > 5:
                        return
                    visited.add(area_id)

                    area_data = mb_get(f"area/{area_id}", {"inc": "area-rels", "fmt": "json"})
                    current_name = area_data.get("name", "")
                    areas.insert(0, current_name)

                    # Only continue if we haven't found Israel yet
                    if "Israel" not in areas:
                        # Find parent areas
                        relations = area_data.get("relations", [])
                        for rel in relations:
                            if rel.get("type") == "part of":
                                parent_area = rel.get("area", {})
                                parent_name = parent_area.get("name", "")
                                if parent_name == "Israel":
                                    # Found Israel, add it and stop
                                    areas.insert(0, parent_name)
                                    return
                                elif parent_name and parent_name not in areas:
                                    parent_id = parent_area.get("id")
                                    if parent_id:
                                        collect_area_hierarchy(parent_id, depth + 1)
                                        return  # Stop after following one path

                collect_area_hierarchy(begin_area_id)

                # Format like webpage: "Tel Aviv, Tel-Aviv, Israel"
                if areas:
                    # Reverse to put most specific first
                    areas.reverse()
                    if len(areas) > 1:
                        begin_area_full = ", ".join(areas)
                    else:
                        begin_area_full = areas[0]
                else:
                    begin_area_full = begin_area_name

            except Exception:
                begin_area_full = begin_area_name
        else:
            begin_area_full = begin_area_name

    # Check if begin-area is related to Israel
    begin_area_is_israeli = "Israel" in begin_area_full

    # Additional check: known Israeli cities
    if not begin_area_is_israeli and begin_area:
        begin_area_name = begin_area.get("name", "")
        known_israeli_cities = [
            "tel aviv", "jerusalem", "haifa", "beersheba", "rishon lezion",
            "petah tikva", "ashdod", "netanya", "holon", "bat yam", "ramat gan",
            "rehovot", "kiryat ono", "herzliya", "jaffa", "nahariya", "hadera",
            "modiin", "lod", "ramla", "nazareth", "tiberias", "safed", "eilat",
            "karmiel", "yavne", "raanana", "kfar saba", "hod hasharon"
        ]
        if begin_area_name.lower() in known_israeli_cities:
            begin_area_is_israeli = True

    is_explicitly_israeli = (country == "IL") or ("Israel" in area_name) or begin_area_is_israeli
    is_explicitly_foreign = bool(country) and country != "IL" and ("Israel" not in area_name) and not begin_area_is_israeli

    if is_explicitly_foreign:
        print(f"⚠️ Skipping explicitly non-Israeli band: {group_name} ({country}/{area_name}/{begin_area_full})")
        return

    with driver.session() as s:
        upsert_group(s, group_mbid, group_name, country=country, area=area_name)

        rels = data.get("relations", []) or []
        
        # We now look for 'member of band', 'producer', 'arranger', 'composer', 'lyricist', 'collaboration'
        interesting_types = {"member of band", "producer", "arranger", "composer", "lyricist", "collaboration"}
        member_rels = [r for r in rels if r.get("type") in interesting_types]

        if not member_rels:
            print("⚠️ No relevant relations found on this band entry.")
            return

        for rel in member_rels:
            rel_type_str = rel.get("type")
            edge_label = "MEMBER_OF"
            if rel_type_str in {"producer", "arranger", "composer", "lyricist"}:
                edge_label = rel_type_str.upper().replace(" ", "_")
            elif rel_type_str == "collaboration":
                edge_label = "COLLABORATED_WITH"
                
            person = rel.get("artist")
            if not person:
                continue
            person_mbid = person["id"]
            person_name = person.get("name", person_mbid)

            begin = rel.get("begin")
            end = rel.get("end")
            attrs = rel.get("attributes") or []
            role = ", ".join(attrs) if attrs else None

            upsert_person(s, person_mbid, person_name)
            merge_membership(s, person_mbid, group_mbid, begin, end, role, rel_type=edge_label)

    print("✅ Ingested band members into Neo4j.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python etl/musicbrainz/ingest_band.py <band_mbid>")
        raise SystemExit(2)
    main(sys.argv[1])
