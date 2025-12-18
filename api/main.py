import os
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from neo4j import GraphDatabase

load_dotenv()

app = FastAPI(title="Israeli Music Graph API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For local dev/demo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def serialize_graph(result_cursor):
    """
    Transforms Neo4j records into a JSON graph format (nodes + edges).
    Expects records to contain Node and Relationship objects.
    """
    # Consume the cursor immediately so we can iterate multiple times
    records = list(result_cursor)

    nodes = {}
    edges = []
    
    # Map internal element_id -> mbid (or useful ID)
    id_map = {} 
    
    # First pass: Collect all Nodes
    for record in records:
        for item in record.values():
            if hasattr(item, "labels"): # It's a Node
                props = dict(item)
                # Use mbid as primary ID, fallback to element_id
                oid = props.get("mbid", item.element_id)
                id_map[item.element_id] = oid
                
                if oid not in nodes:
                    nodes[oid] = {
                        "id": oid,
                        "labels": list(item.labels),
                        "data": props
                    }
    
    # Second pass: Collect all Relationships
    for record in records:
        for item in record.values():
            if hasattr(item, "type"): # It's a Relationship
                props = dict(item)
                
                # Resolving endpoints
                src_eid = item.start_node.element_id
                dst_eid = item.end_node.element_id
                
                src = id_map.get(src_eid)
                dst = id_map.get(dst_eid)

                # DEBUG: If not found, look harder (maybe it's an int vs string issue?)
                if not src:
                    # Try lookup by string conversion if it was int, or vice versa
                    # But Python driver uses strings for element_id usually.
                    pass

                # Only include edge if both nodes are present in our result set
                if src and dst:
                    edges.append({
                        "id": item.element_id,
                        "source": src,
                        "target": dst,
                        "type": item.type,
                        "data": props
                    })
                else:
                    # Print failure to stderr so it definitely shows up
                    print(f"Skipped Edge {item.type}. Src: {src_eid} (Found? {src_eid in id_map}), Dst: {dst_eid} (Found? {dst_eid in id_map})", file=sys.stderr)
                    
    return {"nodes": list(nodes.values()), "edges": edges}
                    
    return {"nodes": list(nodes.values()), "edges": edges}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=1)):
    cypher = """
    CALL db.index.fulltext.queryNodes("nameIndex", $q) YIELD node, score
    RETURN labels(node) AS labels, node, score
    ORDER BY score DESC
    LIMIT 25
    """
    # Fallback if index doesn't exist
    fallback = """
    MATCH (n)
    WHERE (n:Person OR n:Group) AND toLower(n.name) CONTAINS toLower($q)
    RETURN labels(n) AS labels, n AS node, 0.0 AS score
    LIMIT 25
    """
    with driver.session() as s:
        try:
            rows = s.run(cypher, q=q).data()
        except Exception:
            rows = s.run(fallback, q=q).data()

    out = []
    for r in rows:
        node = r["node"]
        out.append({
            "labels": r["labels"],
            "score": r["score"],
            "props": dict(node)
        })
    return out

@app.get("/person/{mbid}/network")
def person_network(mbid: str, bucket: str = "alltime", minWeight: float = 1.0):
    """
    Returns the ego network of a person based on CO_MEMBER relationships.
    """
    cypher = """
    MATCH (p:Person {mbid: $mbid})-[r:CO_MEMBER]-(other:Person)
    WHERE r.timeBucket = $bucket AND r.weight >= $minWeight
    RETURN p, r, other
    """
    with driver.session() as s:
        result = s.run(cypher, mbid=mbid, bucket=bucket, minWeight=minWeight)
        return serialize_graph(result)

@app.get("/band/{mbid}/lineup")
def band_lineup(mbid: str, year_from: int = Query(1900, alias="from"), year_to: int = Query(2100, alias="to")):
    """
    Returns the members of a band within a specific time range.
    """
    cypher = """
    MATCH (g:Group {mbid: $mbid})<-[r:MEMBER_OF]-(p:Person)
    WHERE
      (r.startDate IS NULL OR date(r.startDate).year <= $year_to) AND
      (r.endDate   IS NULL OR date(r.endDate).year   >= $year_from)
    RETURN g, r, p
    """
    with driver.session() as s:
        result = s.run(cypher, mbid=mbid, year_from=year_from, year_to=year_to)
        return serialize_graph(result)

@app.get("/communities")
def get_communities(bucket: str):
    """
    Returns community statistics for a given time bucket.
    """
    # Sanitize bucket to prevent injection since we inject it into property name
    if not bucket.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid bucket name")
        
    prop_id = f"communityId_{bucket}"
    prop_size = f"communitySize_{bucket}"
    
    with driver.session() as s:
        # 1. Get global stats
        stats_query = f"""
        MATCH (s:GraphStats {{id: 'stats'}})
        RETURN s.modularity_{bucket} as modularity, s.communityCount_{bucket} as count
        """
        stats = s.run(stats_query).data()
        metadata = stats[0] if stats else {"modularity": None, "count": 0}

        # 2. Get community list (top 100 by size)
        # We assume communitySize is pre-computed on nodes
        comm_query = f"""
        MATCH (p:Person)
        WHERE p.{prop_id} IS NOT NULL
        WITH p.{prop_id} as id, p.{prop_size} as size
        RETURN DISTINCT id, size
        ORDER BY size DESC
        LIMIT 100
        """
        rows = s.run(comm_query).data()
        
    return {
        "meta": metadata,
        "communities": rows
    }
