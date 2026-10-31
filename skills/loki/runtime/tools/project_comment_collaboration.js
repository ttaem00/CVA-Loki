'use strict';
const { parseEditorDocuments, runCli } = require('./parse_editor_comments.js');
function projectCommentCollaboration(input) { return parseEditorDocuments(input).periods; }
module.exports = { projectCommentCollaboration };
if (require.main === module) runCli(result => result.periods);
