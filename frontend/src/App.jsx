import React, { useState } from 'react';
import CytoscapeComponent from './components/CytoscapeComponent';
import NodeDetails from './components/NodeDetails';
import EdgeDetails from './components/EdgeDetails';
import './App.css';

function App() {
  const [graphData, setGraphData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null);
  const [edgeDetails, setEdgeDetails] = useState(null);
  const [loadingEdge, setLoadingEdge] = useState(false);
  const [selectedNode, setSelectedNode] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [radius, setRadius] = useState(3);
  const [includeCollaborations, setIncludeCollaborations] = useState(true);
  const includeMemberOf = true; // Enabled by default as requested

  const loadGraph = async (overrides = {}) => {
    const currentIncludeCollab = overrides.includeCollaborations !== undefined
      ? overrides.includeCollaborations
      : includeCollaborations;

    if (!searchQuery.trim()) {
      setError('Please enter an artist name');
      return;
    }

    console.log("FETCH GRAPH TRIGGERED"); // Debug log

    setLoading(true);
    setError(null);
    setGraphData(null);
    setSelectedNode(null);

    try {
      const response = await fetch(`/graph/search?q=${encodeURIComponent(searchQuery)}&radius=${radius}&limitNodes=250&limitEdges=600&incMemberOf=${includeMemberOf}&incCollab=${currentIncludeCollab}`);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      setGraphData(data);

      if (data.nodes.length === 0) {
        setError('No matching artist found');
      }
    } catch (err) {
      console.error('Error loading graph:', err);
      setError(err.message || 'Failed to load graph');
    } finally {
      setLoading(false);
    }
  };

  const handleNodeClick = React.useCallback((node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
  }, []);

  const handleEdgeClick = React.useCallback(async (edge) => {
    setSelectedNode(null);
    setSelectedEdge(edge);
    setEdgeDetails(null);

    if (edge) {
      setLoadingEdge(true);
      try {
        const response = await fetch(`/edge/details?source=${encodeURIComponent(edge.source)}&target=${encodeURIComponent(edge.target)}`);
        if (!response.ok) {
          throw new Error("Failed to fetch details");
        }
        const data = await response.json();
        setEdgeDetails(data);
      } catch (err) {
        console.error("Error loading edge details", err);
      } finally {
        setLoadingEdge(false);
      }
    }
  }, []);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      loadGraph();
    }
  };

  return (
    <div className="app">
      <div className="sidebar">
        <h2>ZionMusic Graph Explorer</h2>

        <div className="controls">
          <input
            type="text"
            placeholder="Enter artist name..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyPress={handleKeyPress}
            style={{ flex: 1 }}
          />

          <button onClick={loadGraph} disabled={loading}>
            {loading ? 'Loading...' : 'Load Graph'}
          </button>
        </div>

        <div className="filters-panel">
          <div className="filter-group">
            <label>Radius: {radius}</label>
            <input
              type="range"
              min="1"
              max="10"
              value={radius}
              onChange={(e) => setRadius(parseInt(e.target.value))}
              className="radius-slider"
            />
          </div>

          <div className="filter-group">
            <label className="checkbox-container">
              <input
                type="checkbox"
                checked={includeCollaborations}
                onChange={(e) => {
                  const val = e.target.checked;
                  setIncludeCollaborations(val);
                  loadGraph({ includeCollaborations: val });
                }}
              />
              Show Collaborations
            </label>
          </div>

          <div className="filter-status">
            <small>
              Radius: {radius} | MemberOf: ON | Collaborations: {includeCollaborations ? 'ON' : 'OFF'}
            </small>
          </div>
        </div>

        {error && (
          <div className="error">
            <strong>Error:</strong> {error}
          </div>
        )}

        {selectedNode && <NodeDetails node={selectedNode} />}
        {selectedEdge && (
          <EdgeDetails
            edge={selectedEdge}
            details={edgeDetails}
            loading={loadingEdge}
          />
        )}
        {!selectedNode && !selectedEdge && (
          <div className="node-details">
            <p>Select a node or connection to view details.</p>
          </div>
        )}
      </div>

      <div className="main-content">
        {loading && (
          <div className="loading">
            Loading graph data...
          </div>
        )}

        {!loading && graphData && (
          <CytoscapeComponent
            data={graphData}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
          />
        )}

        {!loading && !graphData && !error && (
          <div className="loading">
            Enter an artist name and click &quot;Load Graph&quot; to get started
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
