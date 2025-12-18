# ZionMusic Graph Logic

## Graph Layers

We strictly separate **Raw Data** from **Derived Data** to ensure data integrity and re-computability.

### 1. Raw Data (Ingestion)
- **Source**: MusicBrainz API
- **Scripts**: `etl/musicbrainz/ingest_*.py`
- **Relationships**: `(:Person)-[:MEMBER_OF]->(:Group)`
- **Philosophy**: This layer reflects the "truth" as seen in the source system. We do not invent connections here.

### 2. Derived Data (Computed)
- **Source**: Cypher queries on the Raw Data
- **Scripts**: `scripts/recompute_*.py`
- **Relationships**: `(:Person)-[:CO_MEMBER]->(:Person)`
- **Philosophy**: This layer is for analytics (cliques, collaboration networks).
- **Rule**: Derived edges can be blown away and recomputed at any time. **NEVER** write to `CO_MEMBER` from an ingestion script.

## Naming Conventions
- **Files**:
    - `ingest_*.py`: Fetches external data.
    - `recompute_*.py`: transformative logic on internal graph data.
- **Edges**:
    - `MEMBER_OF`: Explicit membership (Person in Group).
    - `CO_MEMBER`: Implicit connection (Two people in same Group at same time).

