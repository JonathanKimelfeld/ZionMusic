import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

# =============================================================================
# DERIVED GRAPH LOGIC
# This script manages the CO_MEMBER relationship, which is a derived edge.
#
# NAMING CONVENTION:
# - Raw relationships: MEMBER_OF (from ingestion scripts)
# - Derived relationships: CO_MEMBER (computed by scripts like this one)
#
# DO NOT EDIT MANUALLY. Always re-run this script to regenerate.
# =============================================================================

load_dotenv()

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
)

def compute_alltime(session):
    """
    Computes an 'alltime' bucket that aggregates ALL shared bands,
    ignoring date filters.
    Weight = number of shared bands.
    """
    print("🧹 Clearing old CO_MEMBER edges for alltime...")
    session.run("MATCH ()-[c:CO_MEMBER {timeBucket: 'alltime'}]->() DELETE c")

    print("🔄 Computing new CO_MEMBER edges for alltime...")
    
    cypher = """
    MATCH (p1:Person)-[:MEMBER_OF]->(g:Group)<-[:MEMBER_OF]-(p2:Person)
    WHERE elementId(p1) < elementId(p2)
    WITH p1, p2, 
         collect(DISTINCT g.mbid) AS sharedGroupIds,
         collect(DISTINCT g.name) AS sharedGroupNames,
         count(DISTINCT g) AS sharedCount
    
    MERGE (p1)-[c:CO_MEMBER {timeBucket: 'alltime'}]->(p2)
    SET c.weight = toFloat(sharedCount),
        c.sharedGroupIds = sharedGroupIds,
        c.sharedGroups = sharedGroupNames,
        c.sharedGroupCount = sharedCount
    """
    
    session.run(cypher)
    print("✅ CO_MEMBER computed for alltime")

def compute_overlap(session, year_from, year_to, bucket):
    """
    Computes a time-bounded bucket based on shared tenure overlap in years.
    Weight = total years of overlap.
    """
    print(f"🧹 Clearing old CO_MEMBER edges for {bucket}...")
    session.run("MATCH ()-[c:CO_MEMBER {timeBucket: $bucket}]->() DELETE c", bucket=bucket)
    
    print(f"🔄 Computing new CO_MEMBER edges for {bucket} ({year_from}-{year_to})...")
    
    cypher = """
    MATCH (p1:Person)-[r1:MEMBER_OF]->(g:Group)<-[r2:MEMBER_OF]-(p2:Person)
    WHERE elementId(p1) < elementId(p2)

    WITH p1, p2, g, r1, r2,
         date({year: $yearFrom, month: 1, day: 1}) AS bucketStart,
         date({year: $yearTo, month: 12, day: 31}) AS bucketEnd

    // 1. Determine effective start/end for p1 in this bucket
    WITH p1, p2, g, r1, r2, bucketStart, bucketEnd,
         CASE WHEN r1.startDate IS NULL THEN bucketStart 
              ELSE date(r1.startDate) END AS start1_raw,
         CASE WHEN r1.endDate IS NULL THEN bucketEnd 
              ELSE date(r1.endDate) END AS end1_raw
    WITH p1, p2, g, r1, r2, bucketStart, bucketEnd, start1_raw, end1_raw,
         CASE WHEN start1_raw < bucketStart THEN bucketStart ELSE start1_raw END AS start1,
         CASE WHEN end1_raw > bucketEnd THEN bucketEnd ELSE end1_raw END AS end1

    // 2. Determine effective start/end for p2 in this bucket
    WITH p1, p2, g, r1, r2, bucketStart, bucketEnd, start1, end1,
         CASE WHEN r2.startDate IS NULL THEN bucketStart 
              ELSE date(r2.startDate) END AS start2_raw,
         CASE WHEN r2.endDate IS NULL THEN bucketEnd 
              ELSE date(r2.endDate) END AS end2_raw
    WITH p1, p2, g, bucketStart, bucketEnd, start1, end1, start2_raw, end2_raw,
         CASE WHEN start2_raw < bucketStart THEN bucketStart ELSE start2_raw END AS start2,
         CASE WHEN end2_raw > bucketEnd THEN bucketEnd ELSE end2_raw END AS end2

    // 3. Calculate Overlap
    WITH p1, p2, g,
         CASE WHEN start1 > start2 THEN start1 ELSE start2 END AS overlapStart,
         CASE WHEN end1 < end2 THEN end1 ELSE end2 END AS overlapEnd

    // 4. Filter Invalid Overlaps & Calculate Duration
    WITH p1, p2, g, overlapStart, overlapEnd,
         duration.inDays(overlapStart, overlapEnd).days / 365.25 AS years
    WHERE years >= 1.0

    // 5. Aggregate across all shared groups
    WITH p1, p2, 
         sum(years) AS totalYears,
         min(overlapStart.year) AS firstYear,
         max(overlapEnd.year) AS lastYear,
         collect(DISTINCT g.name) AS sharedGroups,
         count(DISTINCT g) AS sharedGroupCount

    // 6. Create Derived Edge
    MERGE (p1)-[c:CO_MEMBER {timeBucket: $bucket}]->(p2)
    SET c.weight = totalYears,
        c.firstOverlapYear = firstYear,
        c.lastOverlapYear = lastYear,
        c.sharedGroups = sharedGroups,
        c.sharedGroupCount = sharedGroupCount
    """
    
    session.run(cypher, yearFrom=year_from, yearTo=year_to, bucket=bucket)
    print(f"✅ CO_MEMBER computed for {bucket} ({year_from}-{year_to})")

def main(year_from: int, year_to: int, bucket: str):
    with driver.session() as s:
        if bucket == "alltime":
            compute_alltime(s)
        else:
            compute_overlap(s, year_from, year_to, bucket)

if __name__ == "__main__":
    import sys
    
    # Support "python scripts/recompute_comember.py alltime" shortcut
    if len(sys.argv) == 2 and sys.argv[1] == "alltime":
        main(0, 0, "alltime")
    elif len(sys.argv) != 4:
        print("Usage: python scripts/recompute_comember.py <year_from> <year_to> <bucket>")
        print("   OR: python scripts/recompute_comember.py alltime")
        raise SystemExit(2)
    else:
        main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
