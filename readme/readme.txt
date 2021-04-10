Plugin for CudaText.
Adds support for Language Server Protocol (LSP) servers.

For each language server needs to be installed separately.

To use a specific server, at least a command to start the server process and a list of supported 
language identifiers* needs to be provided - add a config to the directory: 
CudaText/data/lspconfig

Basic config for a python LSP server "pyls" would look like this:
{
    "langids": ["python"],
    "cmd": ["pyls"]
}

Name of the file is not important.

* List of lanuage identifiers can be seen here:
https://microsoft.github.io/language-server-protocol/specifications/specification-current/#-textdocumentitem-

About
-----

Author: Shovel, https://github.com/halfbrained/
License: MIT
