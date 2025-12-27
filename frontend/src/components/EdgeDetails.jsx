import PropTypes from 'prop-types';

const EdgeDetails = ({ edge, details, loading }) => {
    if (!edge) {
        return null;
    }

    if (loading) {
        return (
            <div className="node-details">
                <h3>Edge Details</h3>
                <p>Loading shared history...</p>
            </div>
        );
    }

    if (!details) {
        return (
            <div className="node-details">
                <h3>Edge Details</h3>
                <p>No available details.</p>
            </div>
        );
    }

    const { relationships, sharedReleases, sharedBands, source, target } = details;
    const rel = relationships[0]; // Display primary relationship props

    // Safe sort releases
    const sortedShared = sharedReleases ? [...sharedReleases].sort((a, b) => {
        const d1 = a.date || '9999';
        const d2 = b.date || '9999';
        if (d1 === d2) return (a.title || '').localeCompare(b.title || '');
        return d1.localeCompare(d2);
    }) : [];

    return (
        <div className="node-details">
            <h3>Connection</h3>
            <div className="property">
                <strong>{source.name}</strong> ↔ <strong>{target.name}</strong>
            </div>

            <div className="property">
                <strong>Type:</strong> {rel.type}
            </div>

            {rel.props.role && (
                <div className="property">
                    <strong>Role:</strong> {rel.props.role}
                </div>
            )}

            {(rel.props.startDate || rel.props.endDate) && (
                <div className="property">
                    <strong>Period:</strong> {rel.props.startDate || '?'} – {rel.props.endDate || 'Present'}
                </div>
            )}

            {sharedBands && sharedBands.length > 0 && (
                <div className="section">
                    <h4>Shared Bands</h4>
                    <ul>
                        {sharedBands.map((b, i) => (
                            <li key={i}>{b.name}</li>
                        ))}
                    </ul>
                </div>
            )}

            {sortedShared.length > 0 && (
                <div className="section">
                    <h4>Shared Albums ({sortedShared.length})</h4>
                    <ul className="album-list">
                        {sortedShared.map((r, i) => {
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

            {(!sortedShared || sortedShared.length === 0) && (!sharedBands || sharedBands.length === 0) && (
                <div className="property">
                    <em>No shared releases or bands found.</em>
                </div>
            )}
        </div>
    );
};

EdgeDetails.propTypes = {
    edge: PropTypes.object,
    details: PropTypes.shape({
        relationships: PropTypes.arrayOf(
            PropTypes.shape({
                type: PropTypes.string.isRequired,
                props: PropTypes.object.isRequired,
            })
        ).isRequired,
        sharedReleases: PropTypes.arrayOf(PropTypes.object),
        sharedBands: PropTypes.arrayOf(PropTypes.object),
        source: PropTypes.shape({
            name: PropTypes.string.isRequired,
        }).isRequired,
        target: PropTypes.shape({
            name: PropTypes.string.isRequired,
        }).isRequired,
    }),
    loading: PropTypes.bool.isRequired,
};

export default EdgeDetails;
