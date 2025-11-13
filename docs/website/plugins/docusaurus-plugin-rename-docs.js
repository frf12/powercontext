/**
 * Docusaurus plugin to rename document files by removing numeric prefixes
 * 
 * This plugin processes document files and renames them by removing numeric prefixes.
 * For example, it renames:
 * - api/0001-memory.md -> api/memory.md
 * - guides/0002-configuration.md -> guides/configuration.md
 * 
 * This allows the sidebars and links to reference files without prefixes,
 * while the actual files can still have numeric prefixes for ordering in the source.
 * 
 * The plugin runs during the `configureWebpack` phase to ensure files are renamed
 * before Docusaurus processes them.
 */

const fs = require('fs');
const path = require('path');

/**
 * Remove numeric prefix from a filename
 * @param {string} filename - The filename
 * @returns {string} - The filename without numeric prefix
 */
function removeNumericPrefix(filename) {
  // Match patterns like: 0001-memory.md, 0002-configuration.md
  const prefixPattern = /^(\d+-)(.+)$/;
  const match = filename.match(prefixPattern);
  
  if (match) {
    return match[2]; // Return the part after the prefix
  }
  
  return filename;
}

/**
 * Process a directory and rename files with numeric prefixes
 * @param {string} dirPath - The directory path to process
 * @returns {number} - Number of files renamed
 */
function processDirectory(dirPath) {
  if (!fs.existsSync(dirPath)) {
    return 0;
  }

  let renamedCount = 0;
  const files = fs.readdirSync(dirPath);
  
  for (const file of files) {
    const filePath = path.join(dirPath, file);
    const stat = fs.statSync(filePath);
    
    if (stat.isDirectory()) {
      // Recursively process subdirectories
      renamedCount += processDirectory(filePath);
    } else if (stat.isFile() && file.endsWith('.md')) {
      // Check if file has numeric prefix
      const newName = removeNumericPrefix(file);
      
      if (newName !== file) {
        const newPath = path.join(dirPath, newName);
        
        // Only rename if the target doesn't exist
        if (!fs.existsSync(newPath)) {
          fs.renameSync(filePath, newPath);
          console.log(`[docusaurus-plugin-rename-docs] Renamed: ${path.relative(process.cwd(), filePath)} -> ${path.relative(process.cwd(), newPath)}`);
          renamedCount++;
        } else {
          console.warn(`[docusaurus-plugin-rename-docs] Skipped: ${path.relative(process.cwd(), filePath)} (target already exists)`);
        }
      }
    }
  }
  
  return renamedCount;
}

/**
 * Docusaurus plugin to rename document files
 */
function docusaurusPluginRenameDocs(context, options) {
  // Process files immediately when plugin is initialized
  const docsPath = path.join(context.siteDir, 'docs');
  
  if (fs.existsSync(docsPath)) {
    console.log('[docusaurus-plugin-rename-docs] Processing docs directory to remove numeric prefixes...');
    const renamedCount = processDirectory(docsPath);
    if (renamedCount > 0) {
      console.log(`[docusaurus-plugin-rename-docs] Renamed ${renamedCount} file(s)`);
    } else {
      console.log('[docusaurus-plugin-rename-docs] No files needed renaming');
    }
  }
  
  return {
    name: 'docusaurus-plugin-rename-docs',
  };
}

module.exports = docusaurusPluginRenameDocs;

