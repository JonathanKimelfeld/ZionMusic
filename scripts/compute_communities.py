import os
import sys
from dotenv import load_dotenv
from neo4j import GraphDatabase

# =============================================================================
# COMMUNITY DETECTION SCRIPT
# Uses Neo4j Graph Data Science (GDS) to find scenes/clusters.
#
# ALGORITHM: Louvain (weighted)
# INPUT: CO_MEMBER relationships for a specific timeBucket
# OUTPUT:
#   - Person.communityId_<bucket> (Integer)
#   - Person.communitySize_<bucket> (Integer)
#   - GraphStats.modularity_<bucket> (Float)
# =============================================================================

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def main(bucket: str):
    graph_name = f"proj_{bucket}"
    community_prop = f"communityId_{bucket}"
    size_prop = f"communitySize_{bucket}"
    
    # Sanitize bucket name for Cypher property safety
    if not bucket.replace("_", "").isalnum():
        raise ValueError(f"Unsafe bucket name: {bucket}")

    with driver.session() as session:
        print(f"🧹 Ensuring no stale GDS graph named '{graph_name}'...")
        session.run(f"CALL gds.graph.drop('{graph_name}', false)")

        print(f"🏗️ Projecting graph '{graph_name}' for bucket '{bucket}'...")
        # Note: We inject the bucket value directly into the string because GDS Cypher projection
        # runs in a separate context and might not see $bucket. Since we sanitized 'bucket', this is safe.
        session.run(f"""
            CALL gds.graph.project.cypher(
                $graph_name,
                'MATCH (p:Person) RETURN id(p) AS id',
                'MATCH (s:Person)-[r:CO_MEMBER]-(t:Person) WHERE r.timeBucket = "{bucket}" RETURN id(s) AS source, id(t) AS target, r.weight AS weight',
                {{ readConcurrency: 4 }}
            )
        """, graph_name=graph_name)

        print(f"🕵️ Running Louvain on '{bucket}'...")
        # Run Louvain and write community IDs
        result = session.run(f"""
            CALL gds.louvain.write(
                $graph_name,
                {{
                    writeProperty: $community_prop,
                    relationshipWeightProperty: 'weight'
                }}
            )
            YIELD communityCount, modularity
        """, graph_name=graph_name, community_prop=community_prop).single()
        
        if not result:
            print("❌ Louvain failed to return results.")
            return

        modularity = result["modularity"]
        community_count = result["communityCount"]
        print(f"   -> Found {community_count} communities (Modularity: {modularity:.4f})")

        print(f"📏 Computing community sizes into '{size_prop}'...")
        # Post-process: Calculate size of each community and stamp it on the nodes
        # Note: We use f-string for property names since Cypher doesn't support dynamic SET keys easily.
        session.run(f"""
            MATCH (p:Person)
            WHERE p.{community_prop} IS NOT NULL
            WITH p.{community_prop} AS cid, count(p) AS size
            MATCH (p:Person)
            WHERE p.{community_prop} = cid
            SET p.{size_prop} = size
        """)

        print(f"💾 Storing modularity stats...")
        session.run(f"""
            MERGE (s:GraphStats {{id: 'stats'}})
            SET s.modularity_{bucket} = $modularity,
                s.communityCount_{bucket} = $count,
                s.lastComputed_{bucket} = datetime()
        """, modularity=modularity, count=community_count)

        # Cleanup
        print(f"🧹 Dropping projection '{graph_name}'...")
        session.run(f"CALL gds.graph.drop('{graph_name}', false)")
        
    print(f"✅ Community detection complete for {bucket}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/compute_communities.py <bucket>")
        print("Example: python scripts/compute_communities.py 1970s")
        print("Example: python scripts/compute_communities.py alltime")
        raise SystemExit(2)
    main(sys.argv[1])

