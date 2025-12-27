import PropTypes from 'prop-types';

const NodeDetails = ({ node }) => {
  if (!node) {
    return (
      <div className="node-details">
        <h3>Node Details</h3>
        <p>Click on a node in the graph to see its details here.</p>
      </div>
    );
  }

  const { name, label, props: nodeProps } = node;
  const mbid = nodeProps.mbid;
  const wikiIntro = nodeProps.wikipediaIntro;
  const soloReleases = nodeProps.releases || [];
  const externalLinks = nodeProps.externalLinks || [];

  // Build Unified Links List
  const links = [];

  // 1. Wikipedia (Priority)
  if (nodeProps.wikipediaUrl) {
    links.push({ label: 'Wikipedia', url: nodeProps.wikipediaUrl });
  } else if (!externalLinks.some(l => l.url.includes('wikipedia.org'))) {
    // Search fallback
    links.push({ label: 'Wikipedia (Search)', url: `https://en.wikipedia.org/wiki/Special:Search?search=${encodeURIComponent(name)}` });
  }

  // 2. MusicBrainz
  if (mbid) {
    links.push({ label: 'MusicBrainz', url: `https://musicbrainz.org/artist/${mbid}` });
  }

  // 3. Discogs
  if (nodeProps.discogsUrl) {
    links.push({ label: 'Discogs', url: nodeProps.discogsUrl });
  } else if (nodeProps.discogsId) {
    links.push({ label: 'Discogs', url: `https://www.discogs.com/artist/${nodeProps.discogsId}` });
  }

  // 4. Other Externals (Dedup)
  externalLinks.forEach(l => {
    // Normalize label
    let label = l.label || 'Link';

    // Skip if already added (by URL logic or naive label check)
    if (links.some(existing => existing.url === l.url)) return;

    // If we already have a Discogs link and this is another Discogs, maybe add it? 
    // User said "show all to external links".
    if (label === 'Discogs' && links.some(e => e.label === 'Discogs')) return;
    if (label === 'Wikipedia' && links.some(e => e.label === 'Wikipedia')) return;

    links.push(l);
  });

  // Sort: Wiki, MB, Discogs, then others
  // (The order we pushed ensures this roughly, but others might be mixed)

  // Sort Releases
  // Stable sort by date (asc)
  const sortedReleases = [...soloReleases].sort((a, b) => {
    const d1 = a.date || '9999';
    const d2 = b.date || '9999';
    if (d1 === d2) return (a.title || '').localeCompare(b.title || '');
    return d1.localeCompare(d2);
  });

  return (
    <div className="node-details">
      <h3>{name}</h3>
      <div className="property">
        <strong>Type:</strong> {label}
      </div>

      {wikiIntro && (
        <div className="wiki-intro" style={{ marginBottom: '10px', fontStyle: 'italic', fontSize: '0.9em' }}>
          <p>{wikiIntro}</p>
        </div>
      )}

      {Object.entries(nodeProps).map(([key, value]) => {
        if (["mbid", "personId", "groupId", "discogsId", "discogsUrl", "name", "wikipediaUrl", "wikipediaIntro", "releases", "externalLinks"].includes(key)) return null;
        return (
          <div key={key} className="property">
            <strong>{key}:</strong> {value}
          </div>
        );
      })}

      <div className="section">
        <h4>External Links</h4>
        <ul>
          {links.map((link, i) => (
            <li key={i}>
              <a href={link.url} target="_blank" rel="noopener noreferrer">
                {link.label}
              </a>
            </li>
          ))}
        </ul>
      </div>

      {sortedReleases.length > 0 && (
        <div className="section">
          <h4>Solo Releases ({sortedReleases.length})</h4>
          <ul className="album-list">
            {sortedReleases.map((r, i) => {
              const releaseUrl = r.url || (r.mbid ? `https://musicbrainz.org/release/${r.mbid}` : null);
              return (
                <li key={i}>
                  {releaseUrl ? (
                    <a href={releaseUrl} target="_blank" rel="noopener noreferrer" style={{ fontWeight: 'bold' }}>
                      {r.title}
                    </a>
                  ) : (
                    <strong>{r.title}</strong>
                  )}
                  {r.date && <span className="date"> ({r.date.substring(0, 4)})</span>}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
};

NodeDetails.propTypes = {
  node: PropTypes.shape({
    name: PropTypes.string.isRequired,
    label: PropTypes.string.isRequired,
    props: PropTypes.shape({
      mbid: PropTypes.string,
      wikipediaIntro: PropTypes.string,
      releases: PropTypes.arrayOf(PropTypes.object),
      externalLinks: PropTypes.arrayOf(
        PropTypes.shape({
          label: PropTypes.string,
          url: PropTypes.string.isRequired,
        })
      ),
      wikipediaUrl: PropTypes.string,
      discogsUrl: PropTypes.string,
      discogsId: PropTypes.string,
    }).isRequired,
  }),
};

export default NodeDetails;
