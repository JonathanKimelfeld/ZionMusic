import os, requests, uuid, time, urllib3, json, itertools
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

# Check for MusicBrainz authentication token (optional, for higher rate limits)
MB_TOKEN = os.environ.get("MUSICBRAINZ_TOKEN")
if MB_TOKEN:
    HEADERS["Authorization"] = f"Bearer {MB_TOKEN}"

# Circuit breaker for discography processing
discography_failures = 0
max_discography_failures = 5
discography_permanently_disabled = False

# Global cache for area hierarchies to avoid redundant API calls
AREA_CACHE = {}

def mb_get(path, params, retry_count=0, context="general"):
    """Get data from MusicBrainz API with rate limiting and retry logic."""
    import time

    try:
        # Rate limiting: very conservative for discography processing
        if context == "discography":
            rate_limit = 1.0 if MB_TOKEN else 2.0  # Safe for MB (1 req/sec)
        else:
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
            backoff_time = 2 ** retry_count * 2  # Longer exponential backoff
            print(f"      ⚠️ Connection error, retrying in {backoff_time}s... ({retry_count + 1}/3)")
            time.sleep(backoff_time)
            return mb_get(path, params, retry_count + 1, context)
        else:
            print(f"      ❌ Failed after 3 retries: {e}")
            # Circuit breaker: if we keep failing, suggest skipping discography
            if context == "discography":
                print("      💡 Tip: Set SKIP_DISCOGRAPHY=1 to avoid API issues")
            raise e

def upsert_artist(session, mbid: str, name: str, artist_type: str | None, country: str = None, area: str = None, discogs_id: str = None, discogs_url: str = None, releases: list = None, wikipedia_url: str = None, wikipedia_intro: str = None, external_links: list = None):
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
    
    # Ensure releases is a list of strings (JSON) or maps? 
    # Neo4j supports list of maps. Let's assume passed as list of dicts.
    
    session.run(
        f"""
        MERGE (a:{label} {{mbid: $mbid}})
        ON CREATE SET
            a.{id_field} = $id,
            a.name = $name,
            a.country = $country,
            a.area = $area,
            a.discogsId = $discogs_id,
            a.discogsUrl = $discogs_url,
            a.releases = $releases,
            a.wikipediaUrl = $wiki_url,
            a.wikipediaIntro = $wiki_intro,
            a.externalLinks = $external_links
        ON MATCH SET
            a.name = coalesce(a.name, $name),
            a.country = coalesce(a.country, $country),
            a.area = coalesce(a.area, $area),
            a.discogsId = coalesce(a.discogsId, $discogs_id),
            a.discogsUrl = coalesce(a.discogsUrl, $discogs_url),
            a.releases = coalesce($releases, a.releases),
            a.wikipediaUrl = coalesce($wiki_url, a.wikipediaUrl),
            a.wikipediaIntro = coalesce($wiki_intro, a.wikipediaIntro),
            a.externalLinks = coalesce($external_links, a.externalLinks)
        """,
        mbid=mbid,
        id=node_id,
        name=name,
        country=country,
        area=area,
        discogs_id=discogs_id,
        discogs_url=discogs_url,
        releases=releases,
        wiki_url=wikipedia_url,
        wiki_intro=wikipedia_intro,
        external_links=external_links
    )

    return label

def add_membership(session, person_mbid, group_mbid, start, end, role, rel_type="MEMBER_OF"):
    # Clean up role? MB often returns list of attributes.
    session.run(f"""
    MATCH (p:Person {{mbid:$pm}}), (g:Group {{mbid:$gm}})
    MERGE (p)-[r:{rel_type}]->(g)
    SET r.startDate = $start, r.endDate = $end, r.role = $role, r.certainty = 0.7
    """, pm=person_mbid, gm=group_mbid, start=start, end=end, role=role)

def add_collaboration(session, mbid1, mbid2, role="collaborator", shared_releases_list: list = None):
    # Enforce lexicographical direction for undirected edges
    if mbid1 > mbid2:
        mbid1, mbid2 = mbid2, mbid1
        
    query = """
    MATCH (a {mbid:$m1}), (b {mbid:$m2})
    MERGE (a)-[r:COLLABORATED_WITH]-(b)
    SET r.role = coalesce(r.role, $role),
        r.source = 'release_credits'
    WITH r
    // Merge shared releases using APOC to ensure uniqueness and preserve existing ones
    SET r.sharedReleases = apoc.convert.toJson(
        apoc.coll.toSet(
            apoc.convert.fromJsonList(coalesce(r.sharedReleases, '[]')) + $new_rels
        )
    )
    """
    session.run(query, m1=mbid1, m2=mbid2, role=role, new_rels=shared_releases_list or [])

def get_discogs_info(data):
    """Extract Discogs ID and URL from artist-rels included in data."""
    if not data: return None, None
    rels = data.get("relations", [])
    for r in rels:
        if r.get("type") == "discogs":
             url_resource = r.get("url", {}).get("resource", "")
             # Extract ID from URL if possible, e.g., https://www.discogs.com/artist/12345
             # Or just store URL.
             # Discogs URLs: https://www.discogs.com/artist/328288-Kaveret
             # ID is 328288.
             try:
                 parts = url_resource.split("/")
                 if "artist" in parts:
                     idx = parts.index("artist")
                     val = parts[idx+1]
                     # val might be "123-Name"
                     discogs_id = val.split("-")[0]
                     return discogs_id, url_resource
                 return None, url_resource
             except:
                 return None, url_resource
    return None, None

def get_wikipedia_info(data):
    """
    Check for Wikipedia URL relation and fetch intro text.
    Returns (url, intro_text).
    """
    if not data: return None, None
    rels = data.get("relations", [])
    wiki_url = None
    
    for r in rels:
        if r.get("type") == "wikipedia":
             resource = r.get("url", {}).get("resource", "")
             if resource:
                 wiki_url = resource
                 break
    
    if not wiki_url:
        return None, None

    # Fetch intro from Wikipedia API
    # URL format: https://en.wikipedia.org/wiki/Title
    # API format: https://en.wikipedia.org/api/rest_v1/page/summary/Title
    try:
        title = wiki_url.split("/")[-1]
        lang = wiki_url.split("//")[1].split(".")[0] # en, he, etc.
        
        api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
        resp = requests.get(api_url, headers={"User-Agent": "ZionMusicBot/1.0"})
        if resp.status_code == 200:
            summary = resp.json().get("extract")
            return wiki_url, summary
    except Exception as e:
        print(f"      ⚠️ Failed to fetch Wikipedia intro: {e}")
        
    return wiki_url, None

def get_external_links(data):
    """
    Extract all external links from url-rels.
    Returns a list of strings (JSON) or list of dicts: [{label, url}, ...].
    We will store it as a JSON string or simpler, a list of strings if Neo4j.
    Actually, let's return a JSON string of [{label, url}].
    """
    if not data: return None
    rels = data.get("relations", [])
    
    links = []
    
    # Map MB relation types to nice labels
    type_map = {
        "discogs": "Discogs",
        "streaming": "Streaming",
        "official homepage": "Official Website",
        "social network": "Social",
        "soundcloud": "SoundCloud",
        "youtube": "YouTube",
        "bandcamp": "Bandcamp",
        "apple music": "Apple Music",
        "spotify": "Spotify",
        "deezer": "Deezer",
        "tidal": "Tidal",
        "allmusic": "AllMusic",
        "facebook": "Facebook",
        "instagram": "Instagram",
        "twitter": "Twitter",
        "wikidata": "Wikidata"
    }

    for r in rels:
        rtype = r.get("type", "").lower()
        if rtuple := r.get("url"):
            url = rtuple.get("resource")
            if not url: continue
            
            # Detect specific platforms if type is generic 'streaming' or 'social network'
            label = type_map.get(rtype, rtype.title())
            
            if "spotify" in url: label = "Spotify"
            elif "apple.com" in url: label = "Apple Music"
            elif "bandcamp.com" in url: label = "Bandcamp"
            elif "deezer.com" in url: label = "Deezer"
            elif "tidal.com" in url: label = "Tidal"
            elif "youtube.com" in url: label = "YouTube"
            elif "facebook.com" in url: label = "Facebook"
            elif "instagram.com" in url: label = "Instagram"
            elif "twitter.com" in url or "x.com" in url: label = "Twitter"
            
            links.append({"label": label, "url": url})
            
    # Deduplicate by URL
    unique_links = {v['url']:v for v in links}.values()
    return json.dumps(list(unique_links)) if unique_links else None

def check_israeli(mbid, name, session, visited_cache=None, upsert_if_verified=False):
    """
    Returns True if artist is Israeli or already in DB.
    Returns False (and blacklists) if explicitly foreign.
    Returns True if Unknown (benefit of doubt).
    """
    # 1. Check if already in DB (as valid node)
    existing_blacklisted = False
    if session:
        try:
            res = session.run("MATCH (n {mbid: $m}) RETURN labels(n) as l", m=mbid).single()
            if res:
                 # If explicitly blacklisted, we'll re-evaluate since rules may have changed
                 if "Blacklisted" in res["l"]:
                     existing_blacklisted = True
                 else:
                     # Already a valid node
                     return True
        except:
            pass  # No existing record

    # 2. Check API
    try:
        data = mb_get(f"artist/{mbid}", {"fmt": "json"})
        if not data:
             print(f"      ⚠️ No data returned for {name} ({mbid})")
             return False

        country = data.get("country", "")
        area = (data.get("area") or {}).get("name", "")

        # Build full begin_area string like the webpage shows
        begin_area = data.get("begin-area")
        begin_area_full = ""
        if begin_area:
            begin_area_name = begin_area.get("name", "")
            begin_area_id = begin_area.get("id")

            if begin_area_id:
                if begin_area_id in AREA_CACHE:
                    begin_area_full = AREA_CACHE[begin_area_id]
                else:
                    try:
                        # Collect area hierarchy
                        areas = []
                        visited = set()

                        def collect_area_hierarchy(area_id):
                            if area_id in visited:
                                return
                            visited.add(area_id)

                            area_data = mb_get(f"area/{area_id}", {"inc": "area-rels", "fmt": "json"})
                            current_name = area_data.get("name", "")
                            areas.append(current_name)

                            # Find parent areas
                            relations = area_data.get("relations", [])
                            for rel in relations:
                                if rel.get("type") == "part of":
                                    parent_area = rel.get("area", {})
                                    parent_name = parent_area.get("name", "")
                                    if parent_name and parent_name not in areas:
                                        parent_id = parent_area.get("id")
                                        if parent_id:
                                            collect_area_hierarchy(parent_id)
                                            break  # Only follow one parent path

                        collect_area_hierarchy(begin_area_id)

                        # Format like webpage: "Tel Aviv, Tel-Aviv (Tel Aviv District), Israel"
                        if areas:
                            # Put most specific first
                            areas.reverse()
                            if len(areas) > 1:
                                begin_area_full = areas[0] + ", " + ", ".join(areas[1:])
                            else:
                                begin_area_full = areas[0]
                        else:
                            begin_area_full = begin_area_name
                        
                        # Cache the result
                        AREA_CACHE[begin_area_id] = begin_area_full

                    except Exception:
                        begin_area_full = begin_area_name
            else:
                begin_area_full = begin_area_name

        # Check if begin-area is related to Israel
        begin_area_is_israeli = False
        if begin_area_full:
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

        # Israeli if Country=IL OR "Israel" in Area OR begin-area is Israeli
        is_israeli = (country == "IL") or ("Israel" in area) or begin_area_is_israeli

        # Foreign only if country exists and != IL, AND area doesn't contain Israel, AND begin-area is not Israeli
        # This gives benefit of the doubt - if any field suggests Israeli connection, we keep
        is_foreign = bool(country) and country != "IL" and ("Israel" not in area) and not begin_area_is_israeli

        if is_foreign:
            print(f"      ⚠️ Explicitly foreign: {name} ({country} / {area} / {begin_area_full})")
            if session:
                # Check if node exists - if so, add label; otherwise create separate Blacklisted node
                existing = session.run("MATCH (n {mbid: $mbid}) RETURN count(n) as c", mbid=mbid).single()["c"] > 0
                if existing:
                    session.run("""
                       MATCH (n {mbid: $mbid})
                       SET n:Blacklisted, n.blacklistReason = $reason, n.blacklistDate = datetime()
                    """, mbid=mbid, reason=f'Explicitly Foreign (Neighbor): {country}')
                else:
                    session.run("""
                       MERGE (b:Blacklisted {mbid: $mbid})
                       SET b.name = $name, b.country = $c, b.reason = 'Explicitly Foreign (Neighbor)'
                    """, mbid=mbid, name=name, c=country)
            return False

        # If it was previously blacklisted but now passes, remove from blacklist
        if existing_blacklisted and session:
            session.run("MATCH (n {mbid: $mbid}) REMOVE n:Blacklisted", mbid=mbid)
            print(f"      ✅ Previously blacklisted {name} now whitelisted")

        if upsert_if_verified and session:
             # Gather props from 'data' - note: check_israeli gets 'data' from API call above
             c_type = data.get("type", "Person") 
             c_wiki_url, c_wiki_intro = get_wikipedia_info(data)
             c_discogs_id, c_discogs_url = get_discogs_info(data)
             c_external_links = get_external_links(data)
             
             upsert_artist(session, mbid, name, c_type, country=country, area=area, 
                           discogs_id=c_discogs_id, discogs_url=c_discogs_url, 
                           wikipedia_url=c_wiki_url, wikipedia_intro=c_wiki_intro,
                           external_links=c_external_links)

        return True # Israeli or Unknown
    except Exception:
        return False # Fail safe

def process_discography(session, artist_mbid):
    """
    Fetches artist's release groups, then releases, then credits.
    Aggregates:
      - Solo Releases -> Artist Property (JSON string)
      - Shared Releases -> Collaboration Edge Property (JSON string)
    """
    global discography_failures, discography_permanently_disabled

    print("   💿 Parsing Discography for releases...")

    if discography_permanently_disabled or discography_failures >= max_discography_failures:
        if discography_permanently_disabled:
            print("      🚫 Discography permanently disabled due to excessive API failures.")
        else:
            print(f"      🚫 Circuit breaker activated ({discography_failures} failures). Skipping discography.")
        return

    offset = 0
    consecutive_errors = 0
    
    solo_releases = [] # List of {title, date, mbid, type}
    collaborations = {} # mbid -> {name, type, country, area, releases: []}
    
    seen_release_groups = set()

    max_loops = 20 # Safety break
    loop_count = 0

    while True:
        loop_count += 1
        if loop_count > max_loops:
             print("      ⚠️ Reached max discography pages. Stopping.")
             break

        try:
            # BULK FETCH: Get all releases for the artist including release-groups and artist-credits
            r_resp = mb_get("release", {
                "artist": artist_mbid, 
                "inc": "release-groups+artist-credits",
                "limit": 100, 
                "offset": offset, 
                "fmt": "json"
            }, context="discography")
            releases = r_resp.get("releases", [])
            consecutive_errors = 0
        except Exception as e:
            print(f"      ❌ Failed to get releases: {e}")
            consecutive_errors += 1
            if consecutive_errors >= 2:
                return
            time.sleep(5)
            continue

        if not releases:
            break
            
        for release in releases:
            rg = release.get("release-group", {})
            primary_type = rg.get("primary-type")
            
            # We filter for Album/EP as before
            if primary_type not in ["Album", "EP"]:
                continue
            
            rg_id = rg.get("id")
            if not rg_id: continue
            
            # We only care about the RG's credits once per session
            if rg_id in seen_release_groups:
                continue
            seen_release_groups.add(rg_id)

            rg_title = rg.get("title")
            rg_date = rg.get("first-release-date", "")
            
            credits = release.get("artist-credit", [])
            
            release_info = {
                "title": release.get("title", rg_title),
                "date": release.get("date", rg_date),
                "mbid": release.get("id"),
                "type": primary_type,
                "url": f"https://musicbrainz.org/release/{release.get('id')}"
            }
            
            # Identify all artists in the credits
            credit_artists = []
            for c in credits:
                art = c.get("artist", {})
                c_id = art.get("id")
                c_name = art.get("name")
                if c_id and c_name:
                    credit_artists.append({"mbid": c_id, "name": c_name})
            
            # 1. Solo: Exactly 1 artist and it's our target artist
            if len(credit_artists) == 1 and credit_artists[0]["mbid"] == artist_mbid:
                solo_releases.append(release_info)
            elif len(credit_artists) > 1:
                # 2. Collaborative: More than one artist.
                # Create/Update edges between EVERY pair of artists in the credit.
                for a1, a2 in itertools.combinations(credit_artists, 2):
                    # Sort by MBID to consistently identify the pair
                    pair = tuple(sorted([a1["mbid"], a2["mbid"]]))
                    
                    if pair not in collaborations:
                        collaborations[pair] = {
                            "artists": {
                                a1["mbid"]: a1["name"],
                                a2["mbid"]: a2["name"]
                            },
                            "releases": []
                        }
                    collaborations[pair]["releases"].append(release_info)

        offset += 100
        if offset >= r_resp.get("release-count", 0):
            break
            
    # --- PERSISTENCE PHASE ---
    
    # 1. Update Artist with Solo Releases
    if solo_releases:
        solo_releases.sort(key=lambda x: x.get("date", "9999"))
        print(f"      💿 Found {len(solo_releases)} solo releases.")
        
        # Use Python merge and then write to ensure we don't overwrite if multiple crawls hit different solo albums (rare but possible)
        # For simplicity, we can also use APOC on node properties if preferred.
        session.run("""
        MATCH (a {mbid: $mbid})
        SET a.releases = apoc.convert.toJson(
            apoc.coll.toSet(
                apoc.convert.fromJsonList(coalesce(a.releases, '[]')) + $new_rels
            )
        )
        """, mbid=artist_mbid, new_rels=solo_releases)
        
    # 2. Process Collaborations
    if collaborations:
        print(f"      🤝 Analyzing {len(collaborations)} collaboration pairs...")
        
        for (m1, m2), c_data in collaborations.items():
            # Filter: Both artists must be either in DB or verified as Israeli/Unknown
            # For each pair, we check/upsert both endpoints
            v1 = check_israeli(m1, c_data["artists"][m1], session, upsert_if_verified=True)
            v2 = check_israeli(m2, c_data["artists"][m2], session, upsert_if_verified=True)
            
            if v1 and v2:
                 # Sort shared releases by date
                 c_data["releases"].sort(key=lambda x: x.get("date", "9999"))
                 
                 print(f"      🔗 Linking pair: {c_data['artists'][m1]} ↔ {c_data['artists'][m2]} ({len(c_data['releases'])} shared)")
                 add_collaboration(session, m1, m2, shared_releases_list=c_data["releases"])
        if discography_permanently_disabled:
            print("      🚫 Discography permanently disabled due to excessive API failures.")
        else:
            print(f"      🚫 Circuit breaker activated ({discography_failures} failures). Skipping discography.")
            print("      💡 Tip: Discography processing paused due to API instability")
        return


def main(mbid: str):
    # PRE-CHECK: If node exists and is fully populated, maybe skip?
    # Check for existing node with same MBID
    with driver.session() as s:
        exists = s.run("MATCH (n {mbid: $mbid}) RETURN count(n) as c", mbid=mbid).single()["c"] > 0
        
    # Include "artist-rels" and "aliases" and "url-rels" for Discogs
    data = mb_get(f"artist/{mbid}", {"inc": "artist-rels+aliases+url-rels", "fmt": "json"})
    name = data.get("name") or data.get("sort-name") or mbid
    
    # Extract Discogs Info
    discogs_id, discogs_url = get_discogs_info(data)
    if discogs_id:
        print(f"   💿 Discogs ID found: {discogs_id}")
        
    # Extract Wikipedia Info
    wiki_url, wiki_intro = get_wikipedia_info(data)
    if wiki_url:
        print(f"   📖 Wikipedia found: {wiki_url}")

    # Extract Aliases & Detect Hebrew Name
    aliases_data = data.get("aliases", [])
    aliases = [a.get("name") for a in aliases_data]
    
    # Try to find a Hebrew name in aliases or main name
    hebrew_name = None
    
    # Check if main name is Hebrew (rudimentary check for any Hebrew char)
    def is_hebrew(text):
        if not text: return False
        return any("\u0590" <= char <= "\u05FF" for char in text)

    if is_hebrew(name):
        hebrew_name = name
    else:
        # Check aliases
        for a in aliases_data:
            a_name = a.get("name")
            if is_hebrew(a_name):
                hebrew_name = a_name
                break
    
    if hebrew_name:
        print(f"   🇮🇱 Hebrew name detected: {hebrew_name}")
        name = hebrew_name

    sort_name = data.get("sort-name")
    if sort_name and sort_name != name and sort_name not in {"Israel", "israel"}:
        aliases.append(sort_name)
    all_names = list(set([name] + aliases))
    
    # Extract External Links
    external_links_json = get_external_links(data)
    
    # Fallback to update Discogs if we missed it via get_discogs_info but found it in external links?
    # get_discogs_info is specific to the relation type 'discogs'. 
    # get_external_links covers it too. 
    # But let's keep get_discogs_info mostly for extraction of the ID.

    # Check for Israeli nationality/area
    area = data.get("area") or {}
    country = data.get("country", "")
    area_name = area.get("name", "")
    begin_area = data.get("begin-area")

    # Build full begin_area string
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
                    if area_id in visited or depth > 5:  # Prevent infinite recursion
                        return
                    visited.add(area_id)

                    area_data = mb_get(f"area/{area_id}", {"inc": "area-rels", "fmt": "json"})
                    current_name = area_data.get("name", "")
                    areas.insert(0, current_name)  # Insert at beginning to maintain order

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
    begin_area_is_israeli = False
    if begin_area_full:
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

    # REVISED LOGIC (User Request):
    # - If explicitly Israeli (IL or "Israel" in area or begin-area is Israeli) -> Keep.
    # - If explicitly Foreign (country exists and is NOT IL) -> Blacklist & Skip.
    # - If Unknown (country is None/Empty) -> Keep (Benefit of the doubt).

    is_explicitly_israeli = (country == "IL") or ("Israel" in area_name) or begin_area_is_israeli
    # Foreign only if country exists and != IL, AND area doesn't contain Israel, AND begin-area is not Israeli
    is_explicitly_foreign = bool(country) and country != "IL" and ("Israel" not in area_name) and not begin_area_is_israeli

    if is_explicitly_foreign:
        print(f"⚠️ Skipping explicitly non-Israeli artist: {name} ({country}/{area_name}/{begin_area_full})")
        
        # Add to Blacklist but keep relationships
        with driver.session() as s:
            # Add Blacklisted label to existing node, preserving all relationships
            s.run("""
            MATCH (n {mbid: $mbid})
            SET n:Blacklisted, n.blacklistReason = $reason, n.blacklistDate = datetime()
            """, mbid=mbid, reason=f'Explicitly Foreign: {country}/{area_name}/{begin_area_full}')
            print(f"   🚫 Marked as blacklisted (keeping relationships intact).")
            
        return

    # If we are here, it's either Israeli OR Unknown. We proceed.
    if not is_explicitly_israeli:
         print(f"   ℹ️ Artist {name} has unknown country ('{country}'). Ingesting as potential Israeli.")

    with driver.session() as s:
        artist_type = data.get("type")  # "Person", "Group", etc.
        label = upsert_artist(s, mbid, name, artist_type, country=country, area=area_name, discogs_id=discogs_id, discogs_url=discogs_url, wikipedia_url=wiki_url, wikipedia_intro=wiki_intro, external_links=external_links_json)
        
        # Store aliases (using a separate query since upsert_artist is shared)
        # Or better: Update upsert_artist to accept aliases?
        # Let's do a quick update query here to avoid breaking shared function signature for now,
        # or just run a targeted SET.
        s.run(f"MATCH (n {{mbid: $mbid}}) SET n.aliases = apoc.coll.toSet(coalesce(n.aliases, []) + $aliases)", mbid=mbid, aliases=all_names)
        
        if label != "Person":
            print(f"ℹ️ MBID {mbid} is a {artist_type}; skipping member-of import.")
            # For groups, we might still want discography? User asked for "albums in common"
            # which applies to Group-Group or Person-Group too.
            # So we SHOULD NOT skip discography for groups.
            # But the original code returned here. Removing return to allow discography.
            pass  # Continue to relationships even if Group 
        else: 
            # Original code continued only if Person, but actually Groups can have members too (sub-groups?)
            # But usually 'member of' is for Persons.
            # The original code:
            # if label != "Person": return
            # We want to continue to discography. But skipping "MEMBER_OF" logic for Groups is fine if Groups don't have MEMBER_OF relations in MB.
            # Actually, Groups have "members" (reverse). MB returns "member of band" for Person -> Group.
            # If we are looking at a Group, we see "members" rel type.
            # The current code iterates `data.get("relations", [])`.
            pass

        for rel in data.get("relations", []):
            rel_type = rel.get("type")
            
            # Map MB relation types to our Edge types
            edge_label = None
            if rel_type == "member of band":
                if label == "Group": continue # Groups don't have member_of usually, unless sub-group
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
                     b_area = (b_data.get("area") or {}).get("name", "")
                     b_begin_area_obj = b_data.get("begin-area")

                     # Build full begin_area string for neighbor
                     b_begin_area_full = ""
                     if b_begin_area_obj:
                         b_begin_area_name = b_begin_area_obj.get("name", "")
                         b_begin_area_id = b_begin_area_obj.get("id")

                         if b_begin_area_id:
                             try:
                                 # Collect area hierarchy
                                 areas = []
                                 visited = set()

                                 def collect_area_hierarchy(area_id):
                                     if area_id in visited:
                                         return
                                     visited.add(area_id)

                                     area_data = mb_get(f"area/{area_id}", {"inc": "area-rels", "fmt": "json"})
                                     current_name = area_data.get("name", "")
                                     areas.append(current_name)

                                     # Find parent areas
                                     relations = area_data.get("relations", [])
                                     for rel in relations:
                                         if rel.get("type") == "part of":
                                             parent_area = rel.get("area", {})
                                             parent_name = parent_area.get("name", "")
                                             if parent_name and parent_name not in areas:
                                                 parent_id = parent_area.get("id")
                                                 if parent_id:
                                                     collect_area_hierarchy(parent_id)
                                                     break  # Only follow one parent path

                                 collect_area_hierarchy(b_begin_area_id)

                                 # Format like webpage
                                 if areas:
                                     areas.reverse()
                                     if len(areas) > 1:
                                         b_begin_area_full = areas[0] + ", " + ", ".join(areas[1:])
                                     else:
                                         b_begin_area_full = areas[0]
                                 else:
                                     b_begin_area_full = b_begin_area_name

                             except Exception:
                                 b_begin_area_full = b_begin_area_name
                         else:
                             b_begin_area_full = b_begin_area_name

                     # Check if begin-area is related to Israel
                     b_begin_area_is_israeli = False
                     if b_begin_area_full:
                         b_begin_area_is_israeli = "Israel" in b_begin_area_full

                     # Additional check: known Israeli cities
                     if not b_begin_area_is_israeli and b_begin_area_obj:
                         b_begin_area_name = b_begin_area_obj.get("name", "")
                         known_israeli_cities = [
                             "tel aviv", "jerusalem", "haifa", "beersheba", "rishon lezion",
                             "petah tikva", "ashdod", "netanya", "holon", "bat yam", "ramat gan",
                             "rehovot", "kiryat ono", "herzliya", "jaffa", "nahariya", "hadera",
                             "modiin", "lod", "ramla", "nazareth", "tiberias", "safed", "eilat",
                             "karmiel", "yavne", "raanana", "kfar saba", "hod hasharon"
                         ]
                         if b_begin_area_name.lower() in known_israeli_cities:
                             b_begin_area_is_israeli = True

                     # Check Foreign status
                     b_is_israeli = (b_country == "IL") or ("Israel" in b_area) or b_begin_area_is_israeli
                     b_is_foreign = (bool(b_country) and b_country != "IL") and (not b_is_israeli)

                     if b_is_foreign:
                         print(f"   ⚠️ Neighbor {band_name} is explicitly foreign ({b_country}). Blacklisting.")
                         # Check if node exists - if so, add label; otherwise create separate Blacklisted node
                         existing = s.run("MATCH (n {mbid: $mbid}) RETURN count(n) as c", mbid=band_mbid).single()["c"] > 0
                         if existing:
                             s.run("""
                                MATCH (n {mbid: $mbid})
                                SET n:Blacklisted, n.blacklistReason = $reason, n.blacklistDate = datetime()
                             """, mbid=band_mbid, reason=f'Explicitly Foreign (Neighbor): {b_country}')
                         else:
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

    # 4. Process Discography for Collaborators (New Feature)
    # Enable by default as per request
    if os.environ.get("ENABLE_DISCOGRAPHY", "1") == "1":
        print("   ⚠️ Discography processing enabled")
        with driver.session() as s:
            process_discography(s, mbid)
            # Reset circuit breaker on successful completion
            global discography_failures
            if discography_failures > 0:
                discography_failures = max(0, discography_failures - 1)  # Gradually reduce
    else:
        print("   ⏭️ Skipping discography processing (set ENABLE_DISCOGRAPHY=1 to enable)")

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == "--reset-circuit":
        print("🔄 Resetting discography circuit breaker")
        discography_failures = 0  # Already global at module level
        discography_permanently_disabled = False
        print("✅ Circuit breaker reset")
        sys.exit(0)
    elif len(sys.argv) == 2 and sys.argv[1] == "--enable-discography":
        print("🔄 Re-enabling discography processing")
        discography_permanently_disabled = False  # Already global at module level
        discography_failures = 0
        print("✅ Discography processing re-enabled")
        sys.exit(0)
    elif len(sys.argv) != 2:
        print("Usage: python etl/musicbrainz/ingest_one.py <artist_mbid>")
        print("Optional: Set ENABLE_DISCOGRAPHY=1 to enable discography processing (disabled by default)")
        print("Special commands:")
        print("  --reset-circuit: Reset API circuit breaker")
        print("  --enable-discography: Re-enable permanently disabled discography")
        raise SystemExit(2)
    main(sys.argv[1])
