import { useEffect, useRef } from 'react';
import PropTypes from 'prop-types';
import cytoscape from 'cytoscape';
import coseBilkent from 'cytoscape-cose-bilkent';

cytoscape.use(coseBilkent);

const CytoscapeComponent = ({ data, onNodeClick, onEdgeClick }) => {
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !data) return;

    // Convert our data format to Cytoscape elements
    const elements = convertToCytoscapeElements(data);

    // Initialize Cytoscape
    const cy = cytoscape({
      container: containerRef.current,
      elements: elements,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (ele) => ele.data('label') === 'Person' ? '#4CAF50' : '#2196F3',
            'label': 'data(name)',
            'font-size': '12px',
            'text-valign': 'center',
            'text-halign': 'center',
            'color': '#fff',
            'text-outline-color': (ele) => ele.data('label') === 'Person' ? '#2E7D32' : '#0D47A1',
            'text-outline-width': '2px',
            'width': '40px',
            'height': '40px'
          }
        },
        {
          selector: 'edge',
          style: {
            'width': '2px',
            'line-color': '#666',
            'curve-style': 'bezier',
            'label': 'data(type)',
            'font-size': '10px',
            'text-background-color': '#fff',
            'text-background-opacity': 0.8,
            'text-background-padding': '2px',
            'edge-text-rotation': 'autorotate'
          }
        },
        {
          selector: 'edge[type="MEMBER_OF"]',
          style: {
            'target-arrow-color': '#666',
            'target-arrow-shape': 'triangle',
          }
        },
        {
          selector: 'edge[type="CONTRIBUTED_TO"]',
          style: {
            'target-arrow-color': '#666',
            'target-arrow-shape': 'triangle',
          }
        },
        {
          selector: 'edge[type="RELEASED"]',
          style: {
            'target-arrow-color': '#666',
            'target-arrow-shape': 'triangle',
          }
        },
        {
          selector: 'edge[type="COLLABORATED_WITH"]',
          style: {
            'target-arrow-shape': 'none',
            'source-arrow-shape': 'none'
          }
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': '3px',
            'border-color': '#FF5722'
          }
        }
      ],
      layout: {
        name: 'cose-bilkent',
        animate: true,
        animationDuration: 1000,
        nodeRepulsion: 4500,
        idealEdgeLength: 100,
        edgeElasticity: 0.45,
        nestingFactor: 0.1,
        gravity: 0.25,
        numIter: 2500,
        fit: true,
        padding: 30
      }
    });

    window.cy = cy;
    console.log("cy exposed as window.cy");

    // Handle node clicks
    cy.on('tap', 'node', (event) => {
      const node = event.target;
      const nodeData = {
        id: node.id(),
        name: node.data('name'),
        label: node.data('label'),
        props: node.data('props') || {}
      };
      onNodeClick(nodeData);
    });

    // Handle edge clicks
    cy.on('tap', 'edge', (event) => {
      const edge = event.target;
      const edgeData = {
        id: edge.id(),
        source: edge.data('source'),
        target: edge.data('target'),
        type: edge.data('type'),
        props: edge.data('props') || {}
      };
      if (onEdgeClick) onEdgeClick(edgeData);
    });

    // Handle background clicks to deselect
    cy.on('tap', (event) => {
      if (event.target === cy) {
        onNodeClick(null);
      }
    });

    cyRef.current = cy;

    // Cleanup function
    return () => {
      if (cyRef.current) {
        cyRef.current.destroy();
      }
    };
  }, [data, onNodeClick, onEdgeClick]);

  // Update layout when data changes
  useEffect(() => {
    if (cyRef.current && data) {
      // Re-run layout on new data
      setTimeout(() => {
        if (cyRef.current) {
          cyRef.current.layout({
            name: 'cose-bilkent',
            animate: true,
            animationDuration: 800,
            fit: true,
            padding: 30
          }).run();
        }
      }, 100);
    }
  }, [data]);

  const convertToCytoscapeElements = (data) => {
    const elements = [];

    // Add nodes
    data.nodes.forEach(node => {
      elements.push({
        data: {
          id: node.id,
          name: node.name,
          label: node.label,
          props: node.props
        }
      });
    });

    // Add edges
    data.edges.forEach(edge => {
      elements.push({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          type: edge.type,
          props: edge.props
        }
      });
    });

    return elements;
  };

  return (
    <div
      ref={containerRef}
      id="cy"
      style={{
        width: '100%',
        height: '100%',
        border: '1px solid #ddd'
      }}
    />
  );
};

CytoscapeComponent.propTypes = {
  data: PropTypes.shape({
    nodes: PropTypes.arrayOf(PropTypes.object).isRequired,
    edges: PropTypes.arrayOf(PropTypes.object).isRequired,
  }).isRequired,
  onNodeClick: PropTypes.func.isRequired,
  onEdgeClick: PropTypes.func,
};

export default CytoscapeComponent;
