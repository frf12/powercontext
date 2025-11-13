/**
 * Docusaurus Remark plugin to automatically remove numeric prefixes from markdown file links
 * 
 * This plugin transforms links to files with numeric prefixes (e.g., 0001-memory.md) 
 * to links without prefixes (e.g., memory.md)
 * 
 * Examples:
 * - [Link](0001-memory.md) -> [Link](memory.md)
 * - [Link](../api/0001-memory.md) -> [Link](../api/memory.md)
 * - [Link](../guides/0002-configuration.md) -> [Link](../guides/configuration.md)
 * 
 * It only processes relative paths (starting with ./ or ../) or absolute paths (starting with /)
 * that end with '.md' and have numeric prefixes.
 */

/**
 * Simple visitor function to traverse the AST
 */
function visit(tree, type, visitor) {
  if (!tree || typeof tree !== 'object') {
    return;
  }

  if (Array.isArray(tree)) {
    for (const node of tree) {
      visit(node, type, visitor);
    }
    return;
  }

  if (tree.type === type) {
    visitor(tree);
  }

  // Recursively visit children
  for (const key in tree) {
    if (key !== 'type' && key !== 'position' && typeof tree[key] === 'object') {
      visit(tree[key], type, visitor);
    }
  }
}

/**
 * Remove numeric prefix from a filename
 * @param {string} filename - The filename with or without path
 * @returns {string} - The filename without numeric prefix
 */
function removeNumericPrefix(filename) {
  // Match patterns like:
  // - 0001-memory.md
  // - ../api/0001-memory.md
  // - ./guides/0002-configuration.md
  // - /docs/api/0001-memory.md
  
  // Extract the filename part (last segment after /)
  const parts = filename.split('/');
  const lastPart = parts[parts.length - 1];
  
  // Check if the filename has a numeric prefix pattern (e.g., 0001-memory.md)
  const prefixPattern = /^(\d+-)(.+)$/;
  const match = lastPart.match(prefixPattern);
  
  if (match) {
    // Replace the filename part with the one without prefix
    parts[parts.length - 1] = match[2];
    return parts.join('/');
  }
  
  return filename;
}

/**
 * @param {import('unified').Plugin} options
 */
function remarkRemovePrefix() {
  return (tree) => {
    visit(tree, 'link', (node) => {
      if (node.url && typeof node.url === 'string') {
        // Process relative paths (./ or ../) or absolute paths (/)
        // Handle both with and without .md extension
        const isRelativePath = /^\.\.?\/.*/.test(node.url);
        const isAbsolutePath = /^\/.*/.test(node.url);
        const isLocalPath = !node.url.includes('://') && !node.url.startsWith('#') && !node.url.startsWith('mailto:');
        
        // Also handle local file references without path prefix (same directory)
        const isLocalFile = /^[^\/#]/.test(node.url) && !node.url.includes('://');
        
        if ((isRelativePath || isAbsolutePath || isLocalFile) && isLocalPath) {
          // Remove numeric prefix from the link
          const newUrl = removeNumericPrefix(node.url);
          if (newUrl !== node.url) {
            node.url = newUrl;
          }
        }
      }
    });
  };
}

module.exports = remarkRemovePrefix;

