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

@app.get("/graph/search")
def graph_search(q: str = Query(..., min_length=1), radius: int = Query(3, ge=1, le=20), limitNodes: int = Query(250, ge=1), limitEdges: int = Query(600, ge=1)):
    """
    Search for an artist and return their connected neighborhood graph.
    """
    if not q.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    with driver.session() as s:
        # Step 1: Find the root node (Person or Group)
        root_node = find_root_node(s, q)
        if not root_node:
            raise HTTPException(status_code=404, detail=f"No matching artist found for: {q}")

        root_id = root_node["id"]

        # Step 2: Fetch neighborhood up to radius with limits
        nodes, edges = fetch_neighborhood(s, root_id, radius, limitNodes, limitEdges)

        return {
            "rootId": root_id,
            "nodes": list(nodes.values()),
            "edges": edges
        }

def find_root_node(session, search_term: str) -> Optional[Dict[str, Any]]:
    """Find the best matching Person or Group node."""
    # First try exact match (case-insensitive)
    exact_query = """
    MATCH (n:Person) WHERE toLower(n.name) = toLower($search_term) RETURN n, 'Person' as label
    UNION
    MATCH (n:Group) WHERE toLower(n.name) = toLower($search_term) RETURN n, 'Group' as label
    LIMIT 1
    """

    result = session.run(exact_query, search_term=search_term).single()
    if result:
        node = result["n"]
        return {"node": node, "label": result["label"], "id": generate_node_id(node, result["label"])}

    # Fallback to contains match
    contains_query = """
    MATCH (n:Person) WHERE toLower(n.name) CONTAINS toLower($search_term) RETURN n, 'Person' as label
    UNION
    MATCH (n:Group) WHERE toLower(n.name) CONTAINS toLower($search_term) RETURN n, 'Group' as label
    ORDER BY size(n.name)  # Prefer shorter names (likely more relevant)
    LIMIT 1
    """

    result = session.run(contains_query, search_term=search_term).single()
    if result:
        node = result["n"]
        return {"node": node, "label": result["label"], "id": generate_node_id(node, result["label"])}

    return None

def fetch_neighborhood(session, root_id: str, radius: int, max_nodes: int, max_edges: int):
    """Fetch connected neighborhood up to radius with safety limits."""
    # Find the root node by our generated ID
    root_query = """
    MATCH (n)
    WHERE (n:Person AND n.personId = $root_id) OR
          (n:Group AND n.groupId = $root_id) OR
          (n.mbid = $root_id) OR
          (elementId(n) = $root_id)
    RETURN n
    LIMIT 1
    """

    root_result = session.run(root_query, root_id=root_id).single()
    if not root_result:
        return {}, []

    root_node = root_result["n"]

    # Use variable-length path expansion with limits
    paths_query = f"""
    MATCH path = (root)-[*1..{radius}]-(neighbor)
    WHERE elementId(root) = $root_element_id
    RETURN path
    LIMIT {max_edges * 2}  // Get more paths than edges to ensure coverage
    """

    paths_result = session.run(paths_query, root_element_id=root_node.element_id)

    nodes = {}
    edges = []
    edge_count = 0

    # Process paths and collect nodes/edges
    for record in paths_result:
        path = record["path"]

        # Extract nodes from path
        for node in path.nodes:
            node_id = generate_node_id(node, list(node.labels)[0] if node.labels else "Other")

            if node_id not in nodes and len(nodes) < max_nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "label": list(node.labels)[0] if node.labels else "Other",
                    "name": dict(node).get("name", node_id),
                    "props": get_minimal_props(node)
                }

        # Extract relationships from path
        for rel in path.relationships:
            if edge_count >= max_edges:
                break

            source_id = generate_node_id(rel.start_node, list(rel.start_node.labels)[0] if rel.start_node.labels else "Other")
            target_id = generate_node_id(rel.end_node, list(rel.end_node.labels)[0] if rel.end_node.labels else "Other")

            edge_id = generate_edge_id(source_id, rel.type, target_id, dict(rel))

            if not any(e["id"] == edge_id for e in edges):
                edges.append({
                    "id": edge_id,
                    "source": source_id,
                    "target": target_id,
                    "type": rel.type,
                    "props": get_minimal_edge_props(rel)
                })
                edge_count += 1

    return nodes, edges

def generate_node_id(node, label: str) -> str:
    """Generate stable node ID with priority: personId/groupId > mbid > elementId."""
    props = dict(node)

    if label == "Person" and "personId" in props:
        return props["personId"]
    elif label == "Group" and "groupId" in props:
        return props["groupId"]
    elif "mbid" in props:
        return props["mbid"]
    else:
        return node.element_id

def generate_edge_id(source: str, rel_type: str, target: str, props: Dict[str, Any]) -> str:
    """Generate stable edge ID: source|type|target|startDate|endDate."""
    start_date = props.get("startDate", "") or ""
    end_date = props.get("endDate", "") or ""
    return f"{source}|{rel_type}|{target}|{start_date}|{end_date}"

def get_minimal_props(node) -> Dict[str, Any]:
    """Extract minimal properties for nodes."""
    props = dict(node)
    result = {}

    # Always include name if present
    if "name" in props:
        result["name"] = props["name"]

    # Include IDs
    for key in ["mbid", "personId", "groupId"]:
        if key in props:
            result[key] = props[key]

    return result

def get_minimal_edge_props(rel) -> Dict[str, Any]:
    """Extract minimal properties for edges."""
    props = dict(rel)
    result = {}

    # Include dates and role if present
    for key in ["startDate", "endDate", "role"]:
        if key in props:
            result[key] = props[key]

    return result
