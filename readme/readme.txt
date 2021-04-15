Plugin for CudaText.
Adds support for Language Server Protocol (LSP) servers.

For each language server needs to be installed separately.

To use a specific server, at least a command to start the server process and a map of lexers
to supported language identifier* needs to be provided - add a config to the directory: 
CudaText/settings
Config filename should be of the following format: lsp_*.json

Basic config for a python LSP server "pyls" would look like this:
{
    // map lexers to language ids
    "lexers": {
        "Python": "python",
        "Rainbow python": "python"
    },
    // command to start LSP server
    "cmd_windows":  ["C:\\Python_folder\\pyls.exe", "--param", "param3"],
    "cmd_unix":     ["pyls"],
    "cmd_macos":    ["pyls"]
}


* List of language identifiers can be seen here:
https://microsoft.github.io/language-server-protocol/specifications/specification-current/#-textdocumentitem-

About
-----

Author: Shovel, https://github.com/halfbrained/
License: MIT
