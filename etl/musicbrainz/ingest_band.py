import os, uuid, requests
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

def mb_get(path, params):
    r = requests.get(f"{MB}/{path}", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

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
    area = data.get("area", {})
    country = data.get("country", "")
    area_name = area.get("name", "")
    is_israeli = (country == "IL") or ("Israel" in area_name)
    
    if not is_israeli:
        print(f"⚠️ Skipping non-Israeli band: {group_name} ({country}/{area_name})")
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
