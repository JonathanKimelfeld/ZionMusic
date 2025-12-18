import os, requests, uuid
from dotenv import load_dotenv
from neo4j import GraphDatabase

# =============================================================================
# INGESTION SCRIPT
# This script ingests raw data from MusicBrainz (Artist + Relationships).
#
# RULE: This script creates MEMBER_OF relationships.
#       It MUST NOT create CO_MEMBER relationships (derived layer).
# =============================================================================

load_dotenv()
driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))

MB = "https://musicbrainz.org/ws/2"
HEADERS = {
    "User-Agent": "israeli-music-graph/0.1 (local dev; contact: none)",
    "Accept": "application/json",
}

def mb_get(path, params):
    r = requests.get(f"{MB}/{path}", params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def upsert_artist(session, mbid: str, name: str, artist_type: str | None, country: str = None, area: str = None):
    """
    MusicBrainz 'artist' can be either a Person or a Group.
    We map:
      - Group / Choir / Orchestra -> :Group
      - everything else            -> :Person
    """
    artist_type_norm = (artist_type or "").lower()

    is_group = artist_type_norm in {"group", "choir", "orchestra"}

    label = "Group" if is_group else "Person"
    id_field = "groupId" if is_group else "personId"
    node_id = str(uuid.uuid4())

    session.run(
        f"""
        MERGE (a:{label} {{mbid: $mbid}})
        ON CREATE SET
            a.{id_field} = $id,
            a.name = $name,
            a.country = $country,
            a.area = $area
        ON MATCH SET
            a.name = coalesce(a.name, $name),
            a.country = coalesce(a.country, $country),
            a.area = coalesce(a.area, $area)
        """,
        mbid=mbid,
        id=node_id,
        name=name,
        country=country,
        area=area
    )

    return label

def add_membership(session, person_mbid, group_mbid, start, end, role, rel_type="MEMBER_OF"):
    session.run(f"""
    MATCH (p:Person {{mbid:$pm}}), (g:Group {{mbid:$gm}})
    MERGE (p)-[r:{rel_type}]->(g)
    SET r.startDate = $start, r.endDate = $end, r.role = $role, r.certainty = 0.7
    """, pm=person_mbid, gm=group_mbid, start=start, end=end, role=role)

def main(mbid: str):
    # PRE-CHECK: If node exists and is fully populated, maybe skip?
    # Check for existing node with same MBID
    with driver.session() as s:
        exists = s.run("MATCH (n {mbid: $mbid}) RETURN count(n) as c", mbid=mbid).single()["c"] > 0
        
    # Include "artist-rels" and "aliases"
    data = mb_get(f"artist/{mbid}", {"inc": "artist-rels+aliases", "fmt": "json"})
    name = data.get("name") or data.get("sort-name") or mbid
    
    # Extract Aliases
    aliases = [a.get("name") for a in data.get("aliases", [])]
    all_names = list(set([name] + aliases))

    # Check for Israeli nationality/area
    area = data.get("area") or {}
    country = data.get("country", "")
    area_name = area.get("name", "")
    
    # REVISED LOGIC (User Request):
    # - If explicitly Israeli (IL or "Israel" in area) -> Keep.
    # - If explicitly Foreign (country exists and is NOT IL) -> Blacklist & Skip.
    # - If Unknown (country is None/Empty) -> Keep (Benefit of the doubt).
    
    is_explicitly_israeli = (country == "IL") or ("Israel" in area_name)
    # Note: If country is empty string "", it is NOT explicitly foreign.
    is_explicitly_foreign = (bool(country) and country != "IL") and (not is_explicitly_israeli)

    if is_explicitly_foreign:
        print(f"⚠️ Skipping explicitly non-Israeli artist: {name} ({country}/{area_name})")
        
        # Add to Blacklist and cleanup
        with driver.session() as s:
            # 1. Create Blacklist entry so we don't try again (optional, but good practice)
            s.run("""
            MERGE (b:Blacklisted {mbid: $mbid}) 
            SET b.name = $name, b.country = $c, b.reason = 'Explicitly Foreign'
            """, mbid=mbid, name=name, c=country)
            
            # 2. Delete the actual node from graph to prevent pollution
            s.run("MATCH (n {mbid: $mbid}) WHERE NOT n:Blacklisted DETACH DELETE n", mbid=mbid)
            print(f"   🚫 Added to Blacklist and deleted from graph.")
            
        return

    # If we are here, it's either Israeli OR Unknown. We proceed.
    if not is_explicitly_israeli:
         print(f"   ℹ️ Artist {name} has unknown country ('{country}'). Ingesting as potential Israeli.")

    with driver.session() as s:
        artist_type = data.get("type")  # "Person", "Group", etc.
        label = upsert_artist(s, mbid, name, artist_type, country=country, area=area_name)
        
        # Store aliases (using a separate query since upsert_artist is shared)
        # Or better: Update upsert_artist to accept aliases?
        # Let's do a quick update query here to avoid breaking shared function signature for now,
        # or just run a targeted SET.
        s.run(f"MATCH (n {{mbid: $mbid}}) SET n.aliases = apoc.coll.toSet(coalesce(n.aliases, []) + $aliases)", mbid=mbid, aliases=all_names)
        
        if label != "Person":
            print(f"ℹ️ MBID {mbid} is a {artist_type}; skipping member-of import.")
            return

        for rel in data.get("relations", []):
            rel_type = rel.get("type")
            
            # Map MB relation types to our Edge types
            edge_label = None
            if rel_type == "member of band":
                edge_label = "MEMBER_OF"
            elif rel_type in {"producer", "arranger", "composer", "lyricist"}:
                edge_label = rel_type.upper().replace(" ", "_")
            elif rel_type == "collaboration":
                edge_label = "COLLABORATED_WITH"
            
            if not edge_label:
                continue
                
            target = rel.get("artist")
            if not target:
                continue
            
            # TODO: We might want to check if the BAND is Israeli too?
            # But usually if the person is Israeli, we want their bands even if international (e.g. tour band).
            # However, for a "ZionMusic" graph, we probably want to filter strictly.
            # Let's verify the band's country? That would require an extra API call per band.
            # For now, let's assume: If the PERSON is Israeli, we ingest their bands.
            # But wait, if they played in "Metallica" for one gig, we don't want Metallica.
            
            # Let's leave it as-is for bands for now (person-centric filter), 
            # OR we can add a lightweight check if we had band data.
            # Since we only get band MBID and Name here, we can't check country easily without another call.
            
            band_mbid = target["id"]
            band_name = target.get("name", band_mbid)
            
            # STRICT CHECK: Only ingest this neighbor if they are Israeli (or already in DB)
            # 1. Check DB
            is_known = s.run("MATCH (n {mbid: $m}) RETURN count(n) as c", m=band_mbid).single()["c"] > 0
            
            if is_known:
                 # Already exists, safe to link
                 pass
            else:
                 # 2. Not in DB. Must verify country via API before creating.
                 # This adds latency but ensures graph quality.
                 print(f"   🔎 Verifying country for neighbor: {band_name}...")
                 try:
                     # Re-use mb_get with simple retry if needed
                     b_data = mb_get(f"artist/{band_mbid}", {"fmt": "json"})
                     b_country = b_data.get("country", "")
                     b_area_obj = b_data.get("area") or {}
                     b_area = b_area_obj.get("name", "")
                     
                     # Check Foreign status
                     b_is_israeli = (b_country == "IL") or ("Israel" in b_area)
                     b_is_foreign = (bool(b_country) and b_country != "IL") and (not b_is_israeli)

                     if b_is_foreign:
                         print(f"   ⚠️ Neighbor {band_name} is explicitly foreign ({b_country}). Blacklisting.")
                         # Add to blacklist
                         s.run("""
                            MERGE (b:Blacklisted {mbid: $mbid}) 
                            SET b.name = $name, b.country = $c, b.reason = 'Explicitly Foreign (Neighbor)'
                         """, mbid=band_mbid, name=band_name, c=b_country)
                         continue
                     else:
                         # Valid (Israeli or Unknown). Update metadata with full info found.
                         band_name = b_data.get("name", band_name)
                         # We could also grab their type/tags here if we wanted
                 except Exception as e:
                     print(f"   ❌ Failed to verify neighbor {band_name}: {e}. Skipping.")
                     continue

            begin = rel.get("begin")
            end = rel.get("end")
            attrs = rel.get("attributes") or []
            role = ", ".join(attrs) if attrs else None

            upsert_artist(s, band_mbid, band_name, "Group")
            add_membership(s, mbid, band_mbid, begin, end, role, rel_type=edge_label)

    print("✅ Ingested artist + member-of relationships into Neo4j.")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python etl/musicbrainz/ingest_one.py <artist_mbid>")
        raise SystemExit(2)
    main(sys.argv[1])
